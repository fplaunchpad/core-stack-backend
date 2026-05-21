"""Mission Antyodaya TEHSIL-level local integration pipeline.

This file is the production CoRE Stack entry point. It keeps the working
pipeline in one reviewable place while delegating generic admin-boundary,
GEE, GeoServer, and layer-registration concerns to shared utilities.

The admin side is the left table. We keep valid admin rows that are unique by
``pc11_village_id + geometry`` and retain rows even when Antyodaya metrics are
missing, leaving those metric columns null.
"""

from __future__ import annotations

import csv
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import polars as pl
from django.conf import settings

try:
    import pyogrio
except ImportError:  # pragma: no cover - production env should include pyogrio.
    pyogrio = None

from computing.models import Dataset, LayerType
from computing.utils import (
    fix_invalid_geometry_in_gdf,
    save_layer_info_to_db,
    update_layer_sync_status,
)
from nrm_app.celery import app
from utilities.constants import (
    ADMIN_BOUNDARY_INPUT_DIR,
    ANTYODAYA_2020,
    ANTYODAYA_DATASET_NAME,
    ANTYODAYA_GEOSERVER_WORKSPACE,
    ANTYODAYA_OUTPUT_DIR,
)
from utilities.gee_utils import _publish_to_gee
from utilities.geoserver_utils import upload_file_to_geoserver
from utilities.scripts.admin_utils import (
    ADMIN_COLUMNS,
    OUTPUT_ADMIN_COLUMNS,
    _clean_int_id_expr,
    _ensure_integer_output_ids,
    _find_district_file,
    _find_state_dir,
    _first_non_empty,
    _is_valid_admin_text,
    _read_admin_unique_values,
    _validate_output_gdf,
    get_clean_admin_block_gdf,
)


ANTYODAYA_VALUE_SUFFIXES = (
    "_cat_cluster",
    "_cat_value",
    "_feat_cluster",
    "_feat_value",
)
ANTYODAYA_LAYER_PREFIX = "antyodaya20"
ALGORITHM_VERSION = "1.0"
ADMIN_COLUMNS_REMAPPING = [
    ("pc11_village_id", "village_id"),
    ("NAME", "village_name"),
]


def _repo_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else Path(settings.BASE_DIR) / path


def _normalize_location(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _location_slugs(state: str, district: str, block: str) -> tuple[str, str, str]:
    return (
        _normalize_location(state),
        _normalize_location(district),
        _normalize_location(block),
    )


def list_antyodaya_states(admin_input_dir=ADMIN_BOUNDARY_INPUT_DIR) -> list[str]:
    admin_dir = _repo_path(admin_input_dir)
    return sorted(path.name for path in admin_dir.iterdir() if path.is_dir())


def list_antyodaya_districts(state, admin_input_dir=ADMIN_BOUNDARY_INPUT_DIR) -> list[str]:
    state_dir = _find_state_dir(state, _repo_path(admin_input_dir))
    return sorted(path.stem for path in state_dir.glob("*.geojson"))


def list_antyodaya_blocks(state, district, admin_input_dir=ADMIN_BOUNDARY_INPUT_DIR) -> list[str]:
    state_dir = _find_state_dir(state, _repo_path(admin_input_dir))
    district_file = _find_district_file(state_dir, district)
    return sorted(
        str(value).strip()
        for value in _read_admin_unique_values(district_file, "TEHSIL")
        if _is_valid_admin_text(value)
    )


def _read_admin_tehsil_gdf(
    state: str,
    district: str,
    block: str,
    *,
    session_id: str | None = None,
):
    return get_clean_admin_block_gdf(
        state,
        district,
        block,
        admin_input_dir=_repo_path(ADMIN_BOUNDARY_INPUT_DIR),
        admin_columns=ADMIN_COLUMNS,
        session_id=session_id,
        columns_remapping=ADMIN_COLUMNS_REMAPPING,
        log_extra={"pipeline": "antyodaya"},
    )


def _read_antyodaya_header(csv_path: Path) -> list[str]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def _antyodaya_selected_columns(csv_path: Path) -> list[str]:
    header = _read_antyodaya_header(csv_path)
    metric_columns = [column for column in header if column.endswith(ANTYODAYA_VALUE_SUFFIXES)]
    if "village_id" not in header:
        raise ValueError(f"`village_id` not found in {csv_path}")
    return ["village_id", *metric_columns]


def _read_matching_antyodaya_rows(village_ids: list[int]) -> tuple[pd.DataFrame, list[str], dict]:
    csv_path = _repo_path(ANTYODAYA_2020)
    selected_columns = _antyodaya_selected_columns(csv_path)
    metric_columns = selected_columns[1:]
    if not village_ids:
        return (
            pd.DataFrame(columns=[*metric_columns, "village_id_join"]),
            metric_columns,
            {
                "antyodaya_rows_read": 0,
                "antyodaya_duplicate_id_rows_dropped": 0,
                "antyodaya_unique_rows": 0,
            },
        )

    """
    Scan only the id and metric columns needed for this block. The filter is
    pushed into Polars' lazy scan so the full Antyodaya CSV is not materialized
    as a pandas dataframe.
    """
    filtered = (
        pl.scan_csv(csv_path, infer_schema_length=1000)
        .select(selected_columns)
        .with_columns(_clean_int_id_expr("village_id"))
        .filter(pl.col("village_id_join").is_in(village_ids))
        .select([*metric_columns, "village_id_join"])
        .collect()
    )
    deduped = filtered.unique(subset=["village_id_join"], keep="first", maintain_order=True)
    stats = {
        "antyodaya_rows_read": int(filtered.height),
        "antyodaya_duplicate_id_rows_dropped": int(filtered.height - deduped.height),
        "antyodaya_unique_rows": int(deduped.height),
    }
    return (
        pd.DataFrame(deduped.to_dicts(), columns=[*metric_columns, "village_id_join"]),
        metric_columns,
        stats,
    )


def _join_antyodaya(admin_gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict]:
    join_started = time.perf_counter()
    village_ids = (
        admin_gdf["_admin_village_id_join"]
        .dropna()
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    anti_started = time.perf_counter()
    anti_df, metric_columns, anti_stats = _read_matching_antyodaya_rows(village_ids)
    anti_read_seconds = time.perf_counter() - anti_started

    """
    Admin is the left table. This preserves every valid unique admin
    ``pc11_village_id + geometry`` row even if Antyodaya has no matching
    village_id yet.
    """
    merge_started = time.perf_counter()
    output_gdf = admin_gdf.merge(
        anti_df,
        how="left",
        left_on="_admin_village_id_join",
        right_on="village_id_join",
        sort=False,
    )
    output_gdf["village_id"] = output_gdf["_admin_village_id_join"].astype("Int64")
    output_gdf["village_name"] = output_gdf["NAME"].astype("string")
    output_gdf = output_gdf.drop(
        columns=[
            "pc11_village_id",
            "NAME",
            "_admin_village_id_join",
            "_admin_geometry_hash",
            "_admin_pc11_village_id_was_zero",
            "village_id_join",
        ],
        errors="ignore",
    )

    output_columns = [*OUTPUT_ADMIN_COLUMNS, *metric_columns, "geometry"]
    output_gdf = gpd.GeoDataFrame(
        output_gdf.reindex(columns=output_columns),
        geometry="geometry",
        crs=admin_gdf.crs,
    )
    matched_rows = int(output_gdf[metric_columns].notna().any(axis=1).sum()) if metric_columns else 0
    stats = {
        "admin_rows": int(len(admin_gdf)),
        "admin_valid_village_ids": int(len(village_ids)),
        **anti_stats,
        "output_rows": int(len(output_gdf)),
        "matched_rows": matched_rows,
        "unmatched_rows": int(len(output_gdf) - matched_rows),
        "unmatched_admin_rows_retained": int(len(output_gdf) - matched_rows),
        "antyodaya_read_seconds": anti_read_seconds,
        "join_merge_seconds": time.perf_counter() - merge_started,
        "join_total_seconds": time.perf_counter() - join_started,
    }
    return output_gdf, stats


def _build_antyodaya_layer_gdf(
    state: str,
    district: str,
    block: str,
    *,
    session_id: str | None = None,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Build and validate one local Antyodaya layer GeoDataFrame."""
    state_slug, district_slug, block_slug = _location_slugs(state, district, block)
    admin_started = time.perf_counter()
    admin_gdf, district_file, resolved_block, valid_admin_stats = _read_admin_tehsil_gdf(
        state, district, block, session_id=session_id
    )
    admin_read_seconds = time.perf_counter() - admin_started

    join_started = time.perf_counter()
    output_gdf, join_stats = _join_antyodaya(admin_gdf)
    join_seconds = time.perf_counter() - join_started
    output_gdf = fix_invalid_geometry_in_gdf(output_gdf)
    output_gdf = _ensure_integer_output_ids(output_gdf)
    sanity_stats = _validate_output_gdf(output_gdf)

    metadata = {
        "state": state,
        "district": district,
        "requested_block": block,
        "state_slug": state_slug,
        "district_slug": district_slug,
        "block_slug": block_slug,
        "db_state": _first_non_empty(admin_gdf["state_name"], state),
        "db_district": _first_non_empty(admin_gdf["district_name"], district),
        "db_block": _first_non_empty(admin_gdf["TEHSIL"], resolved_block),
        "resolved_block": resolved_block,
        "district_file": district_file.as_posix(),
        "source_antyodaya_csv": _repo_path(ANTYODAYA_2020).as_posix(),
        "admin_read_seconds": admin_read_seconds,
        "join_seconds": join_seconds,
        **valid_admin_stats,
        **join_stats,
        **sanity_stats,
    }
    return output_gdf, metadata


def _output_base_dir(state: str, district: str, block: str) -> Path:
    return (
        _repo_path(ANTYODAYA_OUTPUT_DIR)
        / _normalize_location(state)
        / _normalize_location(district)
        / _normalize_location(block)
    )


def _layer_name(district: str, block: str) -> str:
    return f"{ANTYODAYA_LAYER_PREFIX}_{_normalize_location(district)}_{_normalize_location(block)}"


def _write_local_outputs(gdf: gpd.GeoDataFrame, output_dir: Path, layer_name: str) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    gpkg_base = output_dir / layer_name
    gpkg_path = gpkg_base.with_suffix(".gpkg")
    legacy_gee_csv_path = output_dir / f"{layer_name}_gee_upload.csv"
    legacy_metadata_path = output_dir / f"{layer_name}_metadata.json"

    for path in (gpkg_path, legacy_gee_csv_path, legacy_metadata_path):
        if path.exists():
            path.unlink()

    gdf = fix_invalid_geometry_in_gdf(gdf)
    if pyogrio is not None:
        pyogrio.write_dataframe(gdf, gpkg_path, layer=layer_name, driver="GPKG")
    else:
        gdf.to_file(gpkg_path, layer=layer_name, driver="GPKG")

    return {
        "gpkg_base_path": gpkg_base.as_posix(),
        "gpkg_path": gpkg_path.as_posix(),
    }


def _ensure_antyodaya_dataset() -> None:
    Dataset.objects.get_or_create(
        name=ANTYODAYA_DATASET_NAME,
        defaults={
            "layer_type": LayerType.VECTOR,
            "workspace": ANTYODAYA_GEOSERVER_WORKSPACE,
        },
    )


def _publish_local_outputs(
    *,
    paths: dict[str, str],
    state_slug: str,
    district_slug: str,
    block_slug: str,
    layer_name: str,
    gee_account_id,
    sync_to_gee: bool,
    sync_to_geoserver: bool,
    overwrite: bool,
    make_gee_asset_public: bool,
) -> dict[str, Any]:
    """Publish local files to optional external sinks and collect timings."""
    geoserver_response = None
    geoserver_seconds = None
    if sync_to_geoserver:
        print(f"[{datetime.now()}] Publishing Antyodaya layer to GeoServer...")
        geoserver_started = time.perf_counter()
        geoserver_response = upload_file_to_geoserver(
            paths["gpkg_path"],
            layer_name=layer_name,
            workspace=ANTYODAYA_GEOSERVER_WORKSPACE,
            overwrite=overwrite,
        )
        geoserver_seconds = time.perf_counter() - geoserver_started

    asset_id = None
    gee_task_id = None
    gee_response = None
    gee_made_public = None
    gee_upload_seconds = None
    if sync_to_gee:
        if not gee_account_id:
            gee_account_id = getattr(settings, "GEE_DEFAULT_ACCOUNT_ID", None)
        if not gee_account_id:
            gee_response = {
                "ok": False,
                "error_type": "GEEConfigurationMissing",
                "error": "GEE sync requested; set GEE_DEFAULT_ACCOUNT_ID or pass gee_account_id.",
            }
        else:
            print(f"[{datetime.now()}] Uploading Antyodaya layer to GEE asset...")
            gee_started = time.perf_counter()
            try:
                asset_id, gee_task_id, gee_made_public = _publish_to_gee(
                    paths["gpkg_path"],
                    state_slug,
                    district_slug,
                    block_slug,
                    layer_name,
                    gee_account_id,
                    overwrite=overwrite,
                    make_public=make_gee_asset_public,
                    asset_properties={"source": "mission_antyodaya_2020"},
                )
                gee_response = {
                    "ok": True,
                    "asset_id": asset_id,
                    "task_id": gee_task_id,
                    "made_public": gee_made_public,
                }
            except Exception as exc:
                gee_response = {
                    "ok": False,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc)[:500],
                }
                print(f"[{datetime.now()}] GEE upload failed: {gee_response['error']}")
            gee_upload_seconds = time.perf_counter() - gee_started

    return {
        "gee_asset_id": asset_id,
        "gee_account_id": gee_account_id,
        "gee_response": gee_response,
        "gee_task_id": gee_task_id,
        "gee_made_public": gee_made_public,
        "gee_upload_seconds": gee_upload_seconds,
        "geoserver_response": geoserver_response,
        "geoserver_seconds": geoserver_seconds,
    }


def generate_antyodaya_layer(
    state,
    district,
    block,
    gee_account_id=None,
    sync_to_gee=None,
    sync_to_geoserver=False,
    overwrite=False,
    make_gee_asset_public=True,
):
    """
    Generate one TEHSIL-level Mission Antyodaya vector layer.

    The local path is always the source of truth. GEE sync defaults to true only
    when a ``gee_account_id`` is supplied; GeoServer sync must be explicitly
    requested by API/script callers.
    """
    start_time = datetime.now()
    perf_started = time.perf_counter()
    state_slug, district_slug, block_slug = _location_slugs(state, district, block)
    session_id = (
        f"antyodaya_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_"
        f"{state_slug}_{district_slug}_{block_slug}"
    )
    if sync_to_gee is None:
        sync_to_gee = bool(gee_account_id)

    print(
        f"[{start_time}] Starting Antyodaya layer for "
        f"{state_slug}/{district_slug}/{block_slug}"
    )

    output_gdf, metadata = _build_antyodaya_layer_gdf(
        state, district, block, session_id=session_id
    )
    metadata["session_id"] = session_id
    layer_name = _layer_name(district_slug, block_slug)
    output_dir = _output_base_dir(state_slug, district_slug, block_slug)

    write_started = time.perf_counter()
    paths = _write_local_outputs(output_gdf, output_dir, layer_name)
    write_local_seconds = time.perf_counter() - write_started

    publish = _publish_local_outputs(
        paths=paths,
        state_slug=state_slug,
        district_slug=district_slug,
        block_slug=block_slug,
        layer_name=layer_name,
        gee_account_id=gee_account_id,
        sync_to_gee=bool(sync_to_gee),
        sync_to_geoserver=bool(sync_to_geoserver),
        overwrite=overwrite,
        make_gee_asset_public=make_gee_asset_public,
    )

    layer_id = None
    asset_id = publish["gee_asset_id"]
    geoserver_response = publish["geoserver_response"]
    if asset_id:
        _ensure_antyodaya_dataset()
        layer_id = save_layer_info_to_db(
            metadata["db_state"],
            metadata["db_district"],
            metadata["db_block"],
            layer_name=layer_name,
            asset_id=asset_id,
            dataset_name=ANTYODAYA_DATASET_NAME,
            algorithm="local-admin-village-id-join",
            algorithm_version=ALGORITHM_VERSION,
            misc={**metadata, **paths},
            is_override=overwrite,
        )
        if geoserver_response and geoserver_response.get("ok") and layer_id:
            update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)

    elapsed = (datetime.now() - start_time).total_seconds()
    result = {
        "status": "success",
        "elapsed_seconds": elapsed,
        "local_compute_seconds": (
            time.perf_counter()
            - perf_started
            - (publish["gee_upload_seconds"] or 0)
            - (publish["geoserver_seconds"] or 0)
        ),
        "write_local_seconds": write_local_seconds,
        "layer_name": layer_name,
        "layer_id": layer_id,
        **publish,
        **paths,
        **metadata,
    }
    print(
        f"[{datetime.now()}] Antyodaya layer completed: "
        f"layer={layer_name}, rows={result['output_rows']}, "
        f"local_compute_seconds={result['local_compute_seconds']:.3f}, "
        f"gee_ok={publish['gee_response'].get('ok') if publish['gee_response'] else None}, "
        f"geoserver_ok={geoserver_response.get('ok') if geoserver_response else None}"
    )
    return result


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def generate_antyodaya_layer_task(
    self,
    state,
    district,
    block,
    gee_account_id=None,
    sync_to_gee=None,
    sync_to_geoserver=False,
    overwrite=False,
    make_gee_asset_public=True,
):
    """Celery wrapper used by the Antyodaya API endpoint."""
    return generate_antyodaya_layer(
        state=state,
        district=district,
        block=block,
        gee_account_id=gee_account_id,
        sync_to_gee=sync_to_gee,
        sync_to_geoserver=sync_to_geoserver,
        overwrite=overwrite,
        make_gee_asset_public=make_gee_asset_public,
    )

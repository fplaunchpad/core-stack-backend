"""
Mission Antyodaya local integration utilities.

This module intentionally holds the implementation details for the Antyodaya
pipeline so that ``computing/misc/antyodaya.py`` can stay as a thin CoRE Stack
Celery/API wrapper.

Pipeline contract:

    1. Read only the requested district GeoJSON and only the requested TEHSIL.
    2. Keep valid, unique admin villages, with the first row winning duplicate
       ``pc11_village_id`` values.
    3. Join locally by ``admin.pc11_village_id == antyodaya.village_id``.
    4. Write the canonical local GPKG plus a GEE-upload CSV with the same field
       order. GEE and GeoServer publishing are optional sinks.

No clipping, joining, or feature computation is delegated to Earth Engine.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import ee
import geopandas as gpd
import pandas as pd
import polars as pl
from django.conf import settings
from nrm_app.settings import GCS_BUCKET_NAME
from shapely.geometry import shape

try:
    import pyogrio
except ImportError:  # pragma: no cover - production env should include pyogrio.
    pyogrio = None

try:
    import ijson
except ImportError:  # pragma: no cover - production env should include ijson.
    ijson = None

try:
    import orjson
except ImportError:  # pragma: no cover - production env should include orjson.
    orjson = None

from computing.models import Dataset, LayerType
from computing.utils import (
    fix_invalid_geometry_in_gdf,
    save_layer_info_to_db,
    update_layer_sync_status,
)
from utilities.constants import (
    ADMIN_BOUNDARY_INPUT_DIR,
    ANTYODAYA_2020,
    ANTYODAYA_DATASET_NAME,
    ANTYODAYA_GEOSERVER_WORKSPACE,
    ANTYODAYA_OUTPUT_DIR,
    GEE_PATHS,
)
from utilities.gee_utils import (
    check_task_status,
    ee_initialize,
    ensure_gee_folder_path as _ensure_gee_folder_path,
    gcs_csv_to_gee_table_manifest_cli,
    get_gee_dir_path,
    is_gee_asset_exists,
    make_asset_public,
    probe_gcs_upload_access,
    upload_file_to_gcs,
)
from utilities.geoserver_utils import Geoserver, ensure_workspace as _ensure_geoserver_workspace

ADMIN_COLUMNS = [
    "state_name",
    "district_name",
    "TEHSIL",
    "pc11_village_id",
    "NAME",
    "pc11_state_id",
    "pc11_district_id",
    "pc11_subdistrict_id",
]
OUTPUT_ADMIN_COLUMNS = [
    "state_name",
    "district_name",
    "TEHSIL",
    "village_id",
    "village_name",
    "pc11_state_id",
    "pc11_district_id",
    "pc11_subdistrict_id",
]
ANTYODAYA_VALUE_SUFFIXES = (
    "_cat_cluster",
    "_cat_value",
    "_feat_cluster",
    "_feat_value",
)
ANTYODAYA_LAYER_PREFIX = "antyodaya20"
ALGORITHM_VERSION = "1.0"
GEE_UPLOAD_CSV_DELIMITER = "\t"
GEE_UPLOAD_CSV_QUALIFIER = '"'
WORLD_BOUNDS_RING = [
    [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]
]


def _json_loads_bytes(data: bytes):
    if orjson is not None:
        return orjson.loads(data)
    return json.loads(data.decode("utf-8"))


def _json_dumps_text(payload, indent: bool = False) -> str:
    if orjson is not None:
        option = orjson.OPT_INDENT_2 if indent else 0
        return orjson.dumps(payload, option=option, default=str).decode("utf-8")
    return json.dumps(payload, indent=2 if indent else None, default=str)


JSON_DECODE_ERRORS = (
    (json.JSONDecodeError, orjson.JSONDecodeError)
    if orjson is not None
    else (json.JSONDecodeError,)
)


def _make_json_compatible(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: _make_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_json_compatible(item) for item in value]
    return value


def _prepare_geometry_for_ee_csv(geometry: Any) -> Any:
    if geometry is None:
        return None
    if not isinstance(geometry, dict):
        return _make_json_compatible(geometry)

    def coerce_coordinate_value(value: Any) -> Any:
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return value
            try:
                if any(char in stripped for char in (".", "e", "E")):
                    return float(stripped)
                return int(stripped)
            except ValueError:
                return value
        if isinstance(value, (list, tuple)):
            return [coerce_coordinate_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: coerce_coordinate_value(item)
                if key == "coordinates"
                else _make_json_compatible(item)
                for key, item in value.items()
            }
        return _make_json_compatible(value)

    normalized = coerce_coordinate_value(dict(geometry))
    geometry_type = str(normalized.get("type") or "")
    if geometry_type not in {"Point", "MultiPoint"} and "geodesic" not in normalized:
        normalized["geodesic"] = False
    return normalized


def _is_world_bounds_polygon(bounds_geojson: Any) -> bool:
    if not isinstance(bounds_geojson, dict):
        return False
    if bounds_geojson.get("type") != "Polygon":
        return False
    return bounds_geojson.get("coordinates") == WORLD_BOUNDS_RING


def _inspect_uploaded_asset_geometry_health(asset_id: str) -> dict[str, Any]:
    try:
        fc = ee.FeatureCollection(asset_id)
        first = ee.Feature(fc.first())
        geom = first.geometry()
        info = ee.Dictionary(
            {
                "feature_count": fc.size(),
                "geometry_type": geom.type(),
                "geodesic": geom.geodesic(),
                "bounds": geom.bounds(),
            }
        ).getInfo()
        info["suspicious_world_bounds"] = _is_world_bounds_polygon(info.get("bounds"))
        return info
    except Exception as exc:
        return {
            "inspection_error": str(exc),
            "suspicious_world_bounds": False,
        }


def _repo_path(path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return Path(settings.BASE_DIR) / path


def _normalize_location(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _location_slugs(state: str, district: str, block: str) -> tuple[str, str, str]:
    return (
        _normalize_location(state),
        _normalize_location(district),
        _normalize_location(block),
    )


def _normalize_for_match(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _first_non_empty(series: pd.Series, fallback: str) -> str:
    values = series.dropna().astype(str).str.strip()
    values = values[values != ""]
    if values.empty:
        return str(fallback or "").replace("_", " ").strip()
    return values.iloc[0]


def _sql_quote(value: str) -> str:
    return value.replace("'", "''")


def _clean_int_id_value(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.lower() in {"", "0", "nan", "none", "null"}:
        return None
    if re.fullmatch(r"\d+\.0+", text):
        text = re.sub(r"\.0+$", "", text)
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def _clean_int_id_series(series: pd.Series) -> pd.Series:
    return series.map(_clean_int_id_value).astype("Int64")


def _clean_int_id_expr(column: str) -> pl.Expr:
    value = (
        pl.col(column)
        .cast(pl.Utf8)
        .str.strip_chars()
        .str.replace(r"\.0+$", "")
        .str.to_lowercase()
    )
    return (
        pl.when(value.is_in(["", "0", "nan", "none", "null"]) | ~value.str.contains(r"^\d+$"))
        .then(None)
        .otherwise(value.cast(pl.Int64))
        .alias(f"{column}_join")
    )


def _find_state_dir(state: str, admin_input_dir: Path) -> Path:
    target = _normalize_for_match(state)
    candidates = [p for p in admin_input_dir.iterdir() if p.is_dir()]
    for path in candidates:
        if _normalize_for_match(path.name) == target:
            return path
    available = ", ".join(sorted(p.name for p in candidates)[:30])
    raise FileNotFoundError(f"State '{state}' not found under {admin_input_dir}. Available: {available}")


def _find_district_file(state_dir: Path, district: str) -> Path:
    target = _normalize_for_match(district)
    candidates = sorted(state_dir.glob("*.geojson"))
    for path in candidates:
        if _normalize_for_match(path.stem) == target:
            return path

    # Fall back to inspecting only district_name attributes when filename spellings differ.
    for path in candidates:
        try:
            names = {
                _normalize_for_match(value)
                for value in _read_admin_unique_values(path, "district_name")
                if value is not None
            }
            if target in names:
                return path
        except Exception:
            continue

    available = ", ".join(path.stem for path in candidates[:30])
    raise FileNotFoundError(f"District '{district}' not found in {state_dir}. Available: {available}")


def _is_geojson(path: Path) -> bool:
    return path.suffix.lower() in {".geojson", ".json"}


def _stream_admin_geojson_rows(
    path: Path,
    columns: list[str],
    read_geometry: bool,
    filter_column: str | None = None,
    filter_value: str | None = None,
):
    if ijson is None:
        raise ImportError("ijson is required for streaming GeoJSON admin-boundary files")

    with path.open("rb") as handle:
        for feature in ijson.items(handle, "features.item", use_float=True):
            properties = feature.get("properties") or {}
            if filter_column is not None and properties.get(filter_column) != filter_value:
                continue

            row = {column: properties.get(column) for column in columns}
            if read_geometry:
                geometry = feature.get("geometry")
                row["geometry"] = shape(geometry) if geometry else None
            yield row


def _read_admin_unique_values(path: Path, column: str) -> set:
    if _is_geojson(path) and ijson is not None:
        values = set()
        with path.open("rb") as handle:
            for feature in ijson.items(handle, "features.item", use_float=True):
                value = (feature.get("properties") or {}).get(column)
                if value is not None:
                    values.add(value)
        return values

    df = _read_admin_attributes(path, [column], read_geometry=False)
    return set(df[column].dropna().unique())


def _read_admin_attributes(
    path: Path,
    columns: list[str],
    read_geometry: bool,
    where: str | None = None,
    filter_column: str | None = None,
    filter_value: str | None = None,
):
    if _is_geojson(path) and ijson is not None:
        rows = list(_stream_admin_geojson_rows(path, columns, read_geometry, filter_column, filter_value))
        if read_geometry:
            return gpd.GeoDataFrame(rows, columns=[*columns, "geometry"], geometry="geometry", crs="EPSG:4326")
        return pd.DataFrame(rows, columns=columns)

    if pyogrio is not None:
        return pyogrio.read_dataframe(
            path,
            columns=columns,
            read_geometry=read_geometry,
            where=where,
            use_arrow=True,
        )
    return gpd.read_file(path, columns=columns, where=where)


def list_antyodaya_states(admin_input_dir=ADMIN_BOUNDARY_INPUT_DIR) -> list[str]:
    admin_dir = _repo_path(admin_input_dir)
    return sorted(path.name for path in admin_dir.iterdir() if path.is_dir())


def list_antyodaya_districts(state, admin_input_dir=ADMIN_BOUNDARY_INPUT_DIR) -> list[str]:
    state_dir = _find_state_dir(state, _repo_path(admin_input_dir))
    return sorted(path.stem for path in state_dir.glob("*.geojson"))


def list_antyodaya_blocks(state, district, admin_input_dir=ADMIN_BOUNDARY_INPUT_DIR) -> list[str]:
    district_file = _find_district_file(_find_state_dir(state, _repo_path(admin_input_dir)), district)
    return sorted(str(value).strip() for value in _read_admin_unique_values(district_file, "TEHSIL") if str(value).strip())


def _resolve_block_name(district_file: Path, block: str) -> str:
    values = list_antyodaya_blocks(district_file.parent.name, district_file.stem, district_file.parent.parent)
    target = _normalize_for_match(block)
    for value in values:
        if _normalize_for_match(value) == target:
            return value
    available = ", ".join(values[:30])
    raise ValueError(f"Block/TEHSIL '{block}' not found in {district_file}. Available: {available}")


def _read_admin_block_gdf(state: str, district: str, block: str) -> tuple[gpd.GeoDataFrame, Path, str]:
    admin_dir = _repo_path(ADMIN_BOUNDARY_INPUT_DIR)
    state_dir = _find_state_dir(state, admin_dir)
    district_file = _find_district_file(state_dir, district)
    block_name = _resolve_block_name(district_file, block)

    where = f"TEHSIL = '{_sql_quote(block_name)}'"
    gdf = _read_admin_attributes(
        district_file,
        ADMIN_COLUMNS,
        read_geometry=True,
        where=where,
        filter_column="TEHSIL",
        filter_value=block_name,
    )
    if "geometry" not in gdf.columns:
        raise ValueError(f"No geometry column found after reading {district_file}")

    # pyogrio/GDAL can return case-conflicting columns such as `State`; keep only the requested schema.
    keep_columns = [column for column in ADMIN_COLUMNS if column in gdf.columns] + ["geometry"]
    gdf = gpd.GeoDataFrame(gdf[keep_columns], geometry="geometry", crs=gdf.crs)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    for id_column in [
        "pc11_village_id",
        "pc11_state_id",
        "pc11_district_id",
        "pc11_subdistrict_id",
    ]:
        if id_column in gdf.columns:
            gdf[id_column] = _clean_int_id_series(gdf[id_column])
    gdf["_admin_village_id_join"] = gdf["pc11_village_id"]
    return gdf, district_file, block_name


def _filter_valid_admin_rows(admin_gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict]:
    raw_rows = int(len(admin_gdf))
    valid_mask = admin_gdf["_admin_village_id_join"].notna()
    valid_gdf = admin_gdf.loc[valid_mask].copy()
    duplicate_mask = valid_gdf["_admin_village_id_join"].duplicated(keep="first")
    unique_gdf = valid_gdf.loc[~duplicate_mask].copy()
    stats = {
        "admin_rows_raw": raw_rows,
        "admin_invalid_id_rows_dropped": int(raw_rows - len(valid_gdf)),
        "admin_duplicate_id_rows_dropped": int(duplicate_mask.sum()),
        "admin_rows": int(len(unique_gdf)),
    }
    return unique_gdf, stats


def _read_antyodaya_header(csv_path: Path) -> list[str]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def _antyodaya_selected_columns(csv_path: Path) -> list[str]:
    header = _read_antyodaya_header(csv_path)
    metric_columns = [column for column in header if column.endswith(ANTYODAYA_VALUE_SUFFIXES)]
    if "village_id" not in header:
        raise ValueError(f"`village_id` not found in {csv_path}")
    return ["village_id", *metric_columns]


def _read_matching_antyodaya_rows(village_ids: list[str]) -> tuple[pd.DataFrame, list[str], dict]:
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
    Polars keeps this scan lazy until ``collect``. The projection is only
    ``village_id`` plus the Antyodaya metric columns, and the filter is only the
    requested admin village ids. We deliberately avoid writing a persistent
    parquet cache here because this pipeline should be deterministic from the
    source CSV and should not require cache invalidation during deployment.
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
    return pd.DataFrame(deduped.to_dicts(), columns=[*metric_columns, "village_id_join"]), metric_columns, stats


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

    merge_started = time.perf_counter()
    output_gdf = admin_gdf.merge(
        anti_df,
        how="inner",
        left_on="_admin_village_id_join",
        right_on="village_id_join",
        sort=False,
    )
    output_gdf["village_id"] = output_gdf["_admin_village_id_join"].astype("Int64")
    output_gdf["village_name"] = output_gdf["NAME"].astype("string")
    output_gdf = output_gdf.drop(
        columns=["pc11_village_id", "NAME", "_admin_village_id_join", "village_id_join"],
        errors="ignore",
    )

    output_columns = [*OUTPUT_ADMIN_COLUMNS, *metric_columns, "geometry"]
    output_gdf = gpd.GeoDataFrame(
        output_gdf.reindex(columns=output_columns),
        geometry="geometry",
        crs=admin_gdf.crs,
    )
    merge_seconds = time.perf_counter() - merge_started

    matched_rows = int(len(output_gdf))
    stats = {
        "admin_rows": int(len(admin_gdf)),
        "admin_valid_village_ids": int(len(village_ids)),
        **anti_stats,
        "output_rows": int(len(output_gdf)),
        "matched_rows": matched_rows,
        "unmatched_rows": int(len(admin_gdf) - matched_rows),
        "unmatched_admin_rows_dropped": int(len(admin_gdf) - matched_rows),
        "antyodaya_read_seconds": anti_read_seconds,
        "join_merge_seconds": merge_seconds,
        "join_total_seconds": time.perf_counter() - join_started,
    }
    return output_gdf, stats


def _ensure_integer_output_ids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    for column in [
        "village_id",
        "pc11_state_id",
        "pc11_district_id",
        "pc11_subdistrict_id",
    ]:
        if column not in gdf.columns:
            raise ValueError(f"Output is missing required id column: {column}")
        gdf[column] = _clean_int_id_series(gdf[column])
    return gdf


def _validate_output_gdf(gdf: gpd.GeoDataFrame) -> dict:
    if gdf.empty:
        raise ValueError("Antyodaya output is empty after valid admin join.")
    if "geometry" not in gdf.columns:
        raise ValueError("Antyodaya output has no geometry column.")
    if "pc11_village_id" in gdf.columns:
        raise ValueError("Output must not contain pc11_village_id; use integer village_id only.")

    required_columns = [*OUTPUT_ADMIN_COLUMNS, "geometry"]
    missing_columns = [column for column in required_columns if column not in gdf.columns]
    if missing_columns:
        raise ValueError(f"Output is missing required columns: {missing_columns}")

    id_columns = [
        "village_id",
        "pc11_state_id",
        "pc11_district_id",
        "pc11_subdistrict_id",
    ]
    id_null_counts = {column: int(gdf[column].isna().sum()) for column in id_columns}
    bad_id_columns = {column: count for column, count in id_null_counts.items() if count}
    if bad_id_columns:
        raise ValueError(f"Output contains null or non-integer ids: {bad_id_columns}")

    duplicate_village_ids = int(gdf["village_id"].duplicated().sum())
    if duplicate_village_ids:
        raise ValueError(f"Output contains duplicate village_id rows: {duplicate_village_ids}")

    null_geometries = int(gdf.geometry.isna().sum())
    empty_geometries = int(gdf.geometry.is_empty.sum())
    invalid_geometries = int((~gdf.geometry.is_valid).sum())
    if null_geometries or empty_geometries or invalid_geometries:
        raise ValueError(
            "Output geometry sanity check failed: "
            f"null={null_geometries}, empty={empty_geometries}, invalid={invalid_geometries}"
        )

    crs_epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    if crs_epsg != 4326:
        raise ValueError(f"Output CRS must be EPSG:4326, got {gdf.crs!r}")

    return {
        "sanity_check_passed": True,
        "sanity_output_rows": int(len(gdf)),
        "sanity_village_id_unique": int(gdf["village_id"].nunique(dropna=True)),
        "sanity_village_name_nulls": int(gdf["village_name"].isna().sum()),
        "sanity_duplicate_village_ids": duplicate_village_ids,
        "sanity_null_geometries": null_geometries,
        "sanity_empty_geometries": empty_geometries,
        "sanity_invalid_geometries": invalid_geometries,
        "sanity_crs_epsg": crs_epsg,
        "sanity_id_dtypes": {column: str(gdf[column].dtype) for column in id_columns},
    }


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
    csv_path = output_dir / f"{layer_name}_gee_upload.csv"
    legacy_metadata_path = output_dir / f"{layer_name}_metadata.json"

    for path in (gpkg_path, csv_path, legacy_metadata_path):
        if path.exists():
            path.unlink()

    gdf = fix_invalid_geometry_in_gdf(gdf)
    if pyogrio is not None:
        pyogrio.write_dataframe(gdf, gpkg_path, layer=layer_name, driver="GPKG")
    else:
        gdf.to_file(gpkg_path, layer=layer_name, driver="GPKG")
    _write_gee_upload_csv(gdf, csv_path)

    return {
        "gpkg_base_path": gpkg_base.as_posix(),
        "gpkg_path": gpkg_path.as_posix(),
        "gee_csv_path": csv_path.as_posix(),
    }


def _write_gee_upload_csv(gdf: gpd.GeoDataFrame, csv_path: Path) -> None:
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["geometry"] = gdf.geometry.map(
        lambda geom: _json_dumps_text(
            _prepare_geometry_for_ee_csv(geom.__geo_interface__)
        )
        if geom is not None and not geom.is_empty
        else None
    )
    df.to_csv(
        csv_path,
        index=False,
        sep=GEE_UPLOAD_CSV_DELIMITER,
        quotechar=GEE_UPLOAD_CSV_QUALIFIER,
        quoting=csv.QUOTE_ALL,
    )


def _ensure_antyodaya_dataset() -> None:
    Dataset.objects.get_or_create(
        name=ANTYODAYA_DATASET_NAME,
        defaults={
            "layer_type": LayerType.VECTOR,
            "workspace": ANTYODAYA_GEOSERVER_WORKSPACE,
        },
    )


def _strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _summarize_external_error(exc: Exception, max_chars: int = 500) -> str:
    text = str(exc).replace("\\n", "\n")
    status_match = re.search(r"Status\s*:\s*(\d+)", text)
    message_match = re.search(r"<p><b>Message</b>\s*(.*?)</p>", text, re.S)
    root_match = re.search(
        r"<p><b>Root Cause</b></p><pre>(.*?)(?:\n|</pre>)", text, re.S
    )

    parts = []
    if status_match:
        parts.append(f"status={status_match.group(1)}")
    if message_match:
        parts.append(f"message={_strip_html(message_match.group(1))}")
    if root_match:
        parts.append(f"root_cause={_strip_html(root_match.group(1))}")
    if not parts:
        parts.append(_strip_html(text))
    return "; ".join(parts)[:max_chars]


def _upload_gpkg_to_geoserver(geo: Geoserver, gpkg_path: str, layer_name: str):
    return geo.create_shp_datastore(
        path=gpkg_path,
        store_name=layer_name,
        workspace=ANTYODAYA_GEOSERVER_WORKSPACE,
        file_extension="gpkg",
    )


def _publish_to_geoserver(gpkg_base_path: str, layer_name: str, overwrite: bool = False):
    gpkg_path = f"{gpkg_base_path}.gpkg"
    geo = Geoserver()
    try:
        _ensure_geoserver_workspace(geo, ANTYODAYA_GEOSERVER_WORKSPACE)
        response = _upload_gpkg_to_geoserver(geo, gpkg_path, layer_name)
        return {"ok": True, "response": response}
    except Exception as exc:
        if overwrite:
            try:
                geo.delete_vector_store(
                    workspace=ANTYODAYA_GEOSERVER_WORKSPACE,
                    store=layer_name,
                )
                response = _upload_gpkg_to_geoserver(geo, gpkg_path, layer_name)
                return {"ok": True, "response": response, "recreated_store": True}
            except Exception as retry_exc:
                exc = retry_exc

        error = {
            "ok": False,
            "error_type": exc.__class__.__name__,
            "error": _summarize_external_error(exc),
        }
        print(f"[{datetime.now()}] GeoServer publish failed: {error['error']}")
        return error


def _publish_to_gee(
    csv_path: str,
    state: str,
    district: str,
    block: str,
    layer_name: str,
    gee_account_id,
    overwrite: bool,
    make_public: bool,
):
    """
    Publish the already-computed CSV to a GEE table asset.

    The upload flow keeps GEE as a sink: initialize credentials, stage the CSV in
    GCS, start the manifest upload, wait for the task result, and optionally make
    the final asset public. It does not run an additional "asset exists and looks
    correct" verification pass after the task finishes.
    """
    ee_initialize(gee_account_id, strict=True)

    state_slug, district_slug, block_slug = _location_slugs(state, district, block)
    asset_parent = get_gee_dir_path(
        [state_slug, district_slug, block_slug],
        GEE_PATHS["MWS"]["GEE_ASSET_PATH"],
    ).rstrip("/")
    asset_id = f"{asset_parent}/{layer_name}"

    if is_gee_asset_exists(asset_id, log=False):
        if not overwrite:
            print(f"[{datetime.now()}] GEE asset already exists: {asset_id}")
            made_public = make_asset_public(asset_id) if make_public else None
            if make_public and not made_public:
                raise RuntimeError(f"Failed to make existing GEE asset public: {asset_id}")
            return asset_id, None, made_public

    # Fail before creating Earth Engine folders when the staging bucket is not usable.
    probe_gcs_upload_access(gee_account_id=gee_account_id)
    _ensure_gee_folder_path(asset_parent)

    if is_gee_asset_exists(asset_id, log=False) and overwrite:
        ee.data.deleteAsset(asset_id)
        time.sleep(5)

    destination_blob = (
        f"antyodaya/{state_slug}/{district_slug}/{block_slug}/{Path(csv_path).name}"
    )
    upload_file_to_gcs(csv_path, destination_blob, gee_account_id=gee_account_id)
    gcs_uri = f"gs://{GCS_BUCKET_NAME}/{destination_blob}"
    csv_path_obj = Path(csv_path)
    csv_sha256 = hashlib.sha256()
    with csv_path_obj.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            csv_sha256.update(chunk)
    task_id = gcs_csv_to_gee_table_manifest_cli(
        gcs_uri,
        asset_id,
        gee_account_id,
        primary_geometry_column="geometry",
        crs="EPSG:4326",
        max_vertices=1000000,
        max_error_meters=1,
        csv_delimiter=GEE_UPLOAD_CSV_DELIMITER,
        csv_qualifier=GEE_UPLOAD_CSV_QUALIFIER,
        asset_properties={
            "source": "mission_antyodaya_local_pipeline",
            "state": state_slug,
            "district": district_slug,
            "block": block_slug,
            "layer_name": layer_name,
            "local_gee_csv_sha256": csv_sha256.hexdigest(),
            "local_gee_csv_size_bytes": str(csv_path_obj.stat().st_size),
        },
    )
    if not task_id:
        raise RuntimeError("Failed to start GEE table upload task")
    check_task_status([task_id])
    made_public = make_asset_public(asset_id) if make_public else None
    if make_public and not made_public:
        raise RuntimeError(f"Failed to make GEE asset public: {asset_id}")
    return asset_id, task_id, made_public

def generate_antyodaya_layer(
    state,
    district,
    block,
    gee_account_id=None,
    sync_to_gee=True,
    sync_to_geoserver=True,
    overwrite=False,
    make_gee_asset_public=True,
):
    start_time = datetime.now()
    perf_started = time.perf_counter()
    state_slug, district_slug, block_slug = _location_slugs(state, district, block)
    print(
        f"[{start_time}] Starting Antyodaya layer for "
        f"{state_slug}/{district_slug}/{block_slug}"
    )

    admin_started = time.perf_counter()
    admin_gdf, district_file, resolved_block = _read_admin_block_gdf(state, district, block)
    admin_read_seconds = time.perf_counter() - admin_started
    admin_gdf, valid_admin_stats = _filter_valid_admin_rows(admin_gdf)
    db_state = _first_non_empty(admin_gdf["state_name"], state)
    db_district = _first_non_empty(admin_gdf["district_name"], district)
    db_block = _first_non_empty(admin_gdf["TEHSIL"], resolved_block)

    join_started = time.perf_counter()
    output_gdf, join_stats = _join_antyodaya(admin_gdf)
    join_seconds = time.perf_counter() - join_started
    output_gdf = fix_invalid_geometry_in_gdf(output_gdf)
    output_gdf = _ensure_integer_output_ids(output_gdf)
    sanity_stats = _validate_output_gdf(output_gdf)

    layer_name = _layer_name(district_slug, block_slug)
    output_dir = _output_base_dir(state_slug, district_slug, block_slug)
    metadata = {
        "state": state,
        "district": district,
        "requested_block": block,
        "state_slug": state_slug,
        "district_slug": district_slug,
        "block_slug": block_slug,
        "db_state": db_state,
        "db_district": db_district,
        "db_block": db_block,
        "resolved_block": resolved_block,
        "district_file": district_file.as_posix(),
        "source_antyodaya_csv": _repo_path(ANTYODAYA_2020).as_posix(),
        "layer_name": layer_name,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "admin_read_seconds": admin_read_seconds,
        **valid_admin_stats,
        **join_stats,
        **sanity_stats,
    }
    write_started = time.perf_counter()
    paths = _write_local_outputs(output_gdf, output_dir, layer_name)
    write_local_seconds = time.perf_counter() - write_started

    geoserver_response = None
    geoserver_seconds = None
    if sync_to_geoserver:
        print(f"[{datetime.now()}] Publishing Antyodaya layer to GeoServer...")
        geoserver_started = time.perf_counter()
        geoserver_response = _publish_to_geoserver(
            paths["gpkg_base_path"], layer_name, overwrite=overwrite
        )
        geoserver_seconds = time.perf_counter() - geoserver_started

    asset_id = None
    gee_task_id = None
    gee_response = None
    gee_made_public = None
    gee_upload_seconds = None
    if sync_to_gee:
        if not gee_account_id:
            gee_account_id = settings.GEE_DEFAULT_ACCOUNT_ID
        print(f"[{datetime.now()}] Uploading Antyodaya layer to GEE asset...")
        gee_started = time.perf_counter()
        try:
            asset_id, gee_task_id, gee_made_public = _publish_to_gee(
                paths["gee_csv_path"],
                state_slug,
                district_slug,
                block_slug,
                layer_name,
                gee_account_id,
                overwrite=overwrite,
                make_public=make_gee_asset_public,
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
                "error": _summarize_external_error(exc),
            }
            print(f"[{datetime.now()}] GEE upload failed: {gee_response['error']}")
        gee_upload_seconds = time.perf_counter() - gee_started

    layer_id = None
    if asset_id:
        _ensure_antyodaya_dataset()
        layer_id = save_layer_info_to_db(
            db_state,
            db_district,
            db_block,
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
        "local_compute_seconds": time.perf_counter() - perf_started - (gee_upload_seconds or 0) - (geoserver_seconds or 0),
        "admin_read_seconds": admin_read_seconds,
        "join_seconds": join_seconds,
        "write_local_seconds": write_local_seconds,
        "geoserver_seconds": geoserver_seconds,
        "gee_upload_seconds": gee_upload_seconds,
        "layer_name": layer_name,
        "gee_asset_id": asset_id,
        "gee_account_id": gee_account_id,
        "gee_response": gee_response,
        "gee_task_id": gee_task_id,
        "gee_made_public": gee_made_public,
        "geoserver_response": geoserver_response,
        "layer_id": layer_id,
        **paths,
        **valid_admin_stats,
        **join_stats,
        **sanity_stats,
    }
    print(
        f"[{datetime.now()}] Antyodaya layer completed: "
        f"layer={layer_name}, rows={result['output_rows']}, "
        f"local_compute_seconds={result['local_compute_seconds']:.3f}, "
        f"gee_ok={gee_response.get('ok') if gee_response else None}, "
        f"geoserver_ok={geoserver_response.get('ok') if geoserver_response else None}"
    )
    return result

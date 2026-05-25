"""STAC collection trigger helpers (sync + async) with per-asset response payloads."""

from __future__ import annotations

import contextvars
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from django.conf import settings

from computing.layer_asset_ids import resolve_asset_id_field

log = logging.getLogger(__name__)

_STAC_QUEUE = "nrm"

# Per-request/task accumulator for STAC entries (read in API via consume_stac_results).
_stac_results_context: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("stac_results", default=None)
)

# Enforced for all API / signal / Celery STAC runs (never write STAC to production S3).
STAC_UPLOAD_TO_S3 = False
STAC_OVERWRITE_METADATA = True


def layer_generation_sync_mode() -> bool:
    """Same flag as layer-generation APIs (``LAYER_GENERATION_SYNC_MODE``)."""
    return bool(getattr(settings, "LAYER_GENERATION_SYNC_MODE", False))


def append_stac_result(entry: dict[str, Any]) -> None:
    """Record a STAC entry for the current layer-generation task/request."""
    acc = _stac_results_context.get()
    if acc is None:
        acc = []
        _stac_results_context.set(acc)
    acc.append(entry)


def consume_stac_results() -> list[dict[str, Any]]:
    """Return and clear STAC entries accumulated during the current context."""
    acc = _stac_results_context.get()
    _stac_results_context.set(None)
    return list(acc) if acc else []


def format_geoserver_layer_name(template: str, layer) -> str:
    """Format ``LayerMapping.geoserver_layer_name`` for a saved Layer."""
    from utilities.gee_utils import valid_gee_text

    if not template:
        return ""
    misc = layer.misc or {}
    try:
        return template.format(
            district=valid_gee_text(layer.district.district_name.lower()),
            block=valid_gee_text(layer.block.tehsil_name.lower()),
            state=valid_gee_text(layer.state.state_name.lower()),
            start_year=str(misc.get("start_year", "") or ""),
            end_year=str(misc.get("end_year", "") or ""),
        )
    except (KeyError, IndexError, AttributeError):
        return ""


def build_stac_not_generated_entry(layer, *, reason: str | None = None) -> dict[str, Any]:
    """Response payload when no ``LayerMapping`` / layer_mapping.csv row exists."""
    misc = layer.misc or {}
    asset_id = layer.gee_asset_path
    if asset_id in (None, "", "not available"):
        asset_id = None
    dataset_name = (layer.dataset.name or "").strip() if layer.dataset_id else None
    message = reason or (
        "STAC not generated: no matching row in layer_mapping.csv "
        f"(dataset={dataset_name!r}, layer_name={layer.layer_name!r})"
    )
    return {
        "asset_id": asset_id,
        "layer_id": layer.id,
        "dataset_name": dataset_name,
        "layer_name": layer.layer_name,
        "state": layer.state.state_name,
        "district": layer.district.district_name,
        "block": layer.block.tehsil_name,
        "start_year": str(misc.get("start_year", "") or ""),
        "end_year": str(misc.get("end_year", "") or ""),
        "success": False,
        "stac_generated": False,
        "message": message,
        "error": message,
        "upload_to_s3": STAC_UPLOAD_TO_S3,
        "overwrite_metadata": STAC_OVERWRITE_METADATA,
        "mode": "sync" if layer_generation_sync_mode() else "async",
        "stac_metadata": [],
        "stac_items": [],
        "stac_item_ids": [],
    }


def _layer_mapping_csv_path() -> Path:
    return (
        Path(settings.BASE_DIR)
        / "data"
        / "STAC_specs"
        / "input"
        / "metadata"
        / "layer_mapping.csv"
    )


def _mapping_from_csv_row(row: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        layer_name=(row.get("layer_name") or "").strip(),
        layer_type=(row.get("layer_type") or "").strip(),
        geoserver_layer_name=(row.get("geoserver_layer_name") or "").strip(),
        geoserver_workspace_name=(row.get("geoserver_workspace_name") or "").strip(),
        db_dataset_name=(row.get("db_dataset_name") or "").strip(),
        auto_stac=True,
    )


def resolve_mapping_from_csv(layer) -> SimpleNamespace | None:
    """Resolve mapping from layer_mapping.csv when DB rows are missing."""
    import pandas as pd

    from computing.STAC_specs import constants

    dataset_name = (layer.dataset.name or "").strip()
    if not dataset_name:
        return None

    csv_path = _layer_mapping_csv_path()
    try:
        if csv_path.is_file():
            df = pd.read_csv(csv_path)
        else:
            df = pd.read_csv(constants.LAYER_MAP_GITHUB_URL)
    except Exception as exc:  # noqa: BLE001
        log.warning("STAC: could not read layer_mapping.csv: %s", exc)
        return None

    df["db_dataset_name"] = df["db_dataset_name"].astype(str).str.strip()
    rows = df[df["db_dataset_name"] == dataset_name]
    if rows.empty:
        return None

    saved_name = (layer.layer_name or "").strip()
    if len(rows) == 1:
        return _mapping_from_csv_row(rows.iloc[0].to_dict())

    matches = []
    for _, row in rows.iterrows():
        template = (row.get("geoserver_layer_name") or "").strip()
        if not template:
            matches.append(row)
            continue
        if format_geoserver_layer_name(template, layer) == saved_name:
            matches.append(row)

    if len(matches) == 1:
        return _mapping_from_csv_row(matches[0].to_dict())
    if len(matches) > 1:
        log.info(
            "STAC: %d CSV rows for dataset=%s; picking first",
            len(matches),
            dataset_name,
        )
        return _mapping_from_csv_row(matches[0].to_dict())

    log.warning(
        "STAC: CSV rows for dataset=%s but none match layer_name=%s",
        dataset_name,
        saved_name,
    )
    return None


def _pick_mapping_candidate(candidates: list, layer) -> Any | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        only = candidates[0]
        template = (only.geoserver_layer_name or "").strip()
        if not template:
            return only
        gs_name = format_geoserver_layer_name(template, layer)
        saved_name = (layer.layer_name or "").strip()
        if gs_name == saved_name:
            return only
        log.warning(
            "STAC: LayerMapping geoserver name %r != saved layer_name %r",
            gs_name,
            saved_name,
        )
        return None

    layer_name = (layer.layer_name or "").strip()
    if not layer_name:
        return None

    matches = [
        c
        for c in candidates
        if format_geoserver_layer_name(c.geoserver_layer_name, layer) == layer_name
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None

    log.info(
        "STAC: %d ambiguous LayerMapping matches for layer id=%s; picking first",
        len(matches),
        layer.id,
    )
    return matches[0]


def resolve_mapping_from_layer(layer) -> Any | None:
    """Return the best matching LayerMapping with ``auto_stac=True`` for a Layer."""
    from computing.models import LayerMapping

    if not layer.dataset_id:
        return None

    dataset_name = (layer.dataset.name or "").strip()
    if not dataset_name:
        return None

    candidates = list(
        LayerMapping.objects.filter(
            db_dataset_name__iexact=dataset_name, auto_stac=True
        )
    )
    picked = _pick_mapping_candidate(candidates, layer)
    if picked is not None:
        return picked

    csv_mapping = resolve_mapping_from_csv(layer)
    if csv_mapping is not None:
        log.info(
            "STAC: resolved mapping from layer_mapping.csv for dataset=%s",
            dataset_name,
        )
        return csv_mapping

    return None


def trigger_stac_for_layer(layer, mapping) -> dict[str, Any]:
    """Run STAC for a Layer + LayerMapping (sync or async per env flag)."""
    misc = layer.misc or {}
    asset_id = layer.gee_asset_path
    if asset_id in (None, "", "not available"):
        asset_id = None

    geoserver_layer = format_geoserver_layer_name(
        mapping.geoserver_layer_name, layer
    ) or None

    return trigger_stac_collection(
        layer_type=mapping.layer_type,
        state=layer.state.state_name,
        district=layer.district.district_name,
        block=layer.block.tehsil_name,
        layer_name=mapping.layer_name,
        start_year=str(misc.get("start_year", "") or ""),
        end_year=str(misc.get("end_year", "") or ""),
        layer_id=layer.id,
        asset_id=asset_id,
        geoserver_layer_name=geoserver_layer,
        queue=_STAC_QUEUE,
    )


def build_layer_task_result(
    *,
    success: bool,
    asset_id: str | None = None,
    layer_id: int | None = None,
    stac: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Standard Celery return payload for layer-generation tasks."""
    entries = list(stac) if stac else consume_stac_results()
    return {
        "success": success,
        "asset_id": asset_id,
        "layer_id": layer_id,
        "stac": entries,
    }


def enrich_task_return(
    result: Any,
    *,
    asset_id: str | None = None,
    layer_id: int | None = None,
) -> Any:
    """Attach accumulated STAC results when a task returns a bool or bare value."""
    pending = consume_stac_results()
    if isinstance(result, dict):
        if pending and not result.get("stac"):
            result = {**result, "stac": pending}
        return result
    if pending or asset_id is not None or layer_id is not None:
        return build_layer_task_result(
            success=bool(result),
            asset_id=asset_id,
            layer_id=layer_id,
            stac=pending or None,
        )
    return result


def normalize_asset_id_list(asset_id=None, asset_ids=None) -> list[str]:
    if asset_ids is not None:
        return [str(a) for a in asset_ids if a]
    if isinstance(asset_id, list):
        return [str(a) for a in asset_id if a]
    if asset_id:
        return [str(asset_id)]
    return []


def build_stac_result_entry(
    *,
    asset_id: str | None,
    layer_name: str,
    layer_type: str,
    state: str,
    district: str,
    block: str,
    generation: dict[str, Any],
    stac_task_id: str | None = None,
    mode: str = "sync",
) -> dict[str, Any]:
    items = generation.get("items") or []
    stac_metadata = [i["stac"] for i in items if isinstance(i.get("stac"), dict)]
    entry = {
        "asset_id": asset_id,
        "layer_name": layer_name,
        "layer_type": layer_type,
        "state": state,
        "district": district,
        "block": block,
        "success": bool(generation.get("success")),
        "upload_to_s3": STAC_UPLOAD_TO_S3,
        "overwrite_metadata": STAC_OVERWRITE_METADATA,
        "mode": mode,
        "stac_item_ids": [i.get("item_id") for i in items if i.get("item_id")],
        "stac_items": items,
        "stac_metadata": stac_metadata,
    }
    if generation.get("error"):
        entry["error"] = generation["error"]
    if generation.get("geoserver_workspace"):
        entry["geoserver_workspace"] = generation["geoserver_workspace"]
    if generation.get("geoserver_layer"):
        entry["geoserver_layer"] = generation["geoserver_layer"]
    if stac_task_id:
        entry["stac_task_id"] = stac_task_id
    return entry


def _stac_task_kwargs(
    *,
    layer_type: str,
    state: str,
    district: str,
    block: str,
    layer_name: str,
    start_year: str = "",
    end_year: str = "",
    overwrite: bool = False,
    layer_id: int | None = None,
    geoserver_layer_name: str | None = None,
) -> dict[str, Any]:
    return {
        "layer_type": layer_type,
        "state": state,
        "district": district,
        "block": block,
        "layer_name": layer_name,
        "start_year": start_year,
        "end_year": end_year,
        "upload_to_s3": STAC_UPLOAD_TO_S3,
        "overwrite": overwrite,
        "overwrite_metadata": STAC_OVERWRITE_METADATA,
        "layer_id": layer_id,
        "geoserver_layer_name": geoserver_layer_name,
    }


def run_stac_collection_sync(
    *,
    layer_type: str,
    state: str,
    district: str,
    block: str,
    layer_name: str,
    start_year: str = "",
    end_year: str = "",
    overwrite: bool = False,
    layer_id: int | None = None,
    geoserver_layer_name: str | None = None,
) -> dict[str, Any]:
    from computing.STAC_specs.stac_collection import STACCollectionGenerator

    kwargs = _stac_task_kwargs(
        layer_type=layer_type,
        state=state,
        district=district,
        block=block,
        layer_name=layer_name,
        start_year=start_year,
        end_year=end_year,
        overwrite=overwrite,
        layer_id=layer_id,
        geoserver_layer_name=geoserver_layer_name,
    )
    generator = STACCollectionGenerator()
    gs_layer = kwargs.get("geoserver_layer_name")
    if layer_type == "raster":
        return generator.generate_raster(
            kwargs["state"],
            kwargs["district"],
            kwargs["block"],
            kwargs["layer_name"],
            start_year=kwargs["start_year"],
            end_year=kwargs["end_year"],
            upload_to_s3=kwargs["upload_to_s3"],
            overwrite=kwargs["overwrite"],
            overwrite_metadata=kwargs["overwrite_metadata"],
            layer_id=kwargs["layer_id"],
        )
    if layer_type == "vector":
        return generator.generate_vector(
            kwargs["state"],
            kwargs["district"],
            kwargs["block"],
            kwargs["layer_name"],
            upload_to_s3=kwargs["upload_to_s3"],
            overwrite=kwargs["overwrite"],
            overwrite_metadata=kwargs["overwrite_metadata"],
            layer_id=kwargs["layer_id"],
            geoserver_layer_name=gs_layer,
        )
    raise ValueError(f"Unknown layer_type: {layer_type}")


def _stac_location_fields(
    layer_name: str,
    layer_type: str,
    state: str,
    district: str,
    block: str,
) -> dict[str, str]:
    return {
        "layer_name": layer_name,
        "layer_type": layer_type,
        "state": state,
        "district": district,
        "block": block,
    }


def build_stac_async_pending_entry(
    *,
    asset_id: str | None,
    layer_name: str,
    layer_type: str,
    state: str,
    district: str,
    block: str,
    stac_task_id: str | None = None,
    stac_async_error: str | None = None,
) -> dict[str, Any]:
    entry = {
        "asset_id": asset_id,
        "success": None,
        "upload_to_s3": STAC_UPLOAD_TO_S3,
        "overwrite_metadata": STAC_OVERWRITE_METADATA,
        "mode": "async",
        "stac_item_ids": [],
        "stac_items": [],
        "layer_generation_sync_mode": False,
        **_stac_location_fields(layer_name, layer_type, state, district, block),
    }
    if stac_task_id:
        entry["stac_async_task_id"] = stac_task_id
    if stac_async_error:
        entry["stac_async_error"] = stac_async_error
    return entry


def trigger_stac_collection(
    *,
    layer_type: str,
    state: str,
    district: str,
    block: str,
    layer_name: str,
    start_year: str = "",
    end_year: str = "",
    overwrite: bool = False,
    layer_id: int | None = None,
    asset_id: str | None = None,
    geoserver_layer_name: str | None = None,
    queue: str = "nrm",
) -> dict[str, Any]:
    """
    Run STAC with ``upload_to_s3=False`` and ``overwrite_metadata=True`` (enforced).

    Mirrors ``LAYER_GENERATION_SYNC_MODE``:

    - ``True``: STAC runs synchronously in-process (no Celery queue).
    - ``False``: STAC is queued on Celery only (no inline sync).
    """
    task_kwargs = _stac_task_kwargs(
        layer_type=layer_type,
        state=state,
        district=district,
        block=block,
        layer_name=layer_name,
        start_year=start_year,
        end_year=end_year,
        overwrite=overwrite,
        layer_id=layer_id,
        geoserver_layer_name=geoserver_layer_name,
    )
    location = _stac_location_fields(
        layer_name, layer_type, state, district, block
    )

    if layer_generation_sync_mode():
        sync_generation = run_stac_collection_sync(
            layer_type=layer_type,
            state=state,
            district=district,
            block=block,
            layer_name=layer_name,
            start_year=start_year,
            end_year=end_year,
            overwrite=overwrite,
            layer_id=layer_id,
            geoserver_layer_name=geoserver_layer_name,
        )
        entry = build_stac_result_entry(
            asset_id=asset_id,
            generation=sync_generation,
            mode="sync",
            **location,
        )
        entry["layer_generation_sync_mode"] = True
        return entry

    try:
        async_task = dispatch_stac_collection_async(**task_kwargs, queue=queue)
        return build_stac_async_pending_entry(
            asset_id=asset_id,
            stac_task_id=async_task.id,
            **location,
        )
    except Exception as exc:  # noqa: BLE001
        return build_stac_async_pending_entry(
            asset_id=asset_id,
            stac_async_error=str(exc),
            **location,
        )


def dispatch_stac_collection_async(
    *,
    layer_type: str,
    state: str,
    district: str,
    block: str,
    layer_name: str,
    start_year: str = "",
    end_year: str = "",
    overwrite: bool = False,
    layer_id: int | None = None,
    geoserver_layer_name: str | None = None,
    queue: str = "nrm",
):
    from computing.STAC_specs.stac_collection import generate_stac_collection_task

    kwargs = _stac_task_kwargs(
        layer_type=layer_type,
        state=state,
        district=district,
        block=block,
        layer_name=layer_name,
        start_year=start_year,
        end_year=end_year,
        overwrite=overwrite,
        layer_id=layer_id,
        geoserver_layer_name=geoserver_layer_name,
    )
    return generate_stac_collection_task.apply_async(kwargs=kwargs, queue=queue)


def parse_layer_generation_specs(data: dict) -> list[dict[str, Any]]:
    """Build one STAC job spec per layer; attach asset_id when provided."""
    layers = data.get("layers")
    if layers:
        specs = []
        for row in layers:
            if not isinstance(row, dict):
                continue
            specs.append(
                {
                    "state": row.get("state") or data.get("state"),
                    "district": row.get("district") or data.get("district"),
                    "block": row.get("block") or data.get("block"),
                    "layer_name": row.get("layer_name"),
                    "layer_type": row.get("layer_type"),
                    "start_year": row.get("start_year", data.get("start_year", "")),
                    "end_year": row.get("end_year", data.get("end_year", "")),
                    "asset_id": row.get("asset_id"),
                    "overwrite": row.get("overwrite", data.get("overwrite", False)),
                }
            )
        return specs

    asset_ids = normalize_asset_id_list(
        data.get("asset_id"), data.get("asset_ids")
    )
    spec = {
        "state": data.get("state"),
        "district": data.get("district"),
        "block": data.get("block"),
        "layer_name": data.get("layer_name"),
        "layer_type": data.get("layer_type"),
        "start_year": data.get("start_year", ""),
        "end_year": data.get("end_year", ""),
        "overwrite": data.get("overwrite", False),
        "asset_id": None,
    }
    if not asset_ids:
        return [spec]
    return [{**spec, "asset_id": aid} for aid in asset_ids]


def stac_from_task_result(task_result: Any) -> list[dict[str, Any]] | None:
    """Extract ``stac`` list from a layer task return value (sync-mode responses)."""
    if not isinstance(task_result, dict):
        return None
    stac = task_result.get("stac")
    if stac is None:
        return None
    if isinstance(stac, list):
        return stac
    return [stac]


def collect_stac_from_tasks(tasks: list[Any]) -> list[dict[str, Any]]:
    """Merge STAC payloads from one or more completed Celery tasks (multi-layer APIs)."""
    merged: list[dict[str, Any]] = []
    for task in tasks or []:
        if task is None:
            continue
        try:
            if not getattr(task, "ready", lambda: False)():
                continue
            if getattr(task, "failed", lambda: False)():
                continue
            chunk = stac_from_task_result(getattr(task, "result", None))
            if chunk:
                merged.extend(chunk)
        except Exception:
            continue
    if not merged:
        merged = consume_stac_results()
    return merged


def format_stac_api_response(stac_entries: list[dict[str, Any]]) -> dict[str, Any]:
    async_only = any(e.get("mode") == "async" for e in stac_entries)
    if async_only:
        status = "initiated"
        message = "STAC collection generation initiated"
    else:
        status = "completed"
        message = "STAC collection generation completed"

    payload: dict[str, Any] = {
        "status": status,
        "Success": message,
        "message": message,
        "upload_to_s3": STAC_UPLOAD_TO_S3,
        "overwrite_metadata": STAC_OVERWRITE_METADATA,
        "layer_generation_sync_mode": layer_generation_sync_mode(),
        "stac": stac_entries,
    }
    asset_ids = [e.get("asset_id") for e in stac_entries if e.get("asset_id")]
    resolved = resolve_asset_id_field(asset_ids=asset_ids or None)
    if resolved is not None:
        payload["asset_id"] = resolved
    if len(asset_ids) > 1:
        payload["asset_ids"] = asset_ids
    return payload

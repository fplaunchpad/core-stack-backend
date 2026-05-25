"""STAC collection trigger helpers (sync + async) with per-asset response payloads."""

from __future__ import annotations

from typing import Any

from django.conf import settings

from computing.layer_asset_ids import resolve_asset_id_field

# Enforced for all API / signal / Celery STAC runs (never write STAC to production S3).
STAC_UPLOAD_TO_S3 = False
STAC_OVERWRITE_METADATA = True


def layer_generation_sync_mode() -> bool:
    """Same flag as layer-generation APIs (``LAYER_GENERATION_SYNC_MODE``)."""
    return bool(getattr(settings, "LAYER_GENERATION_SYNC_MODE", False))


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
    }
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
    )
    generator = STACCollectionGenerator()
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

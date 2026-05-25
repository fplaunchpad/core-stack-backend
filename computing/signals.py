"""Auto-trigger STAC collection generation when a Layer is synced to GeoServer.

Uses ``LAYER_GENERATION_SYNC_MODE`` (same as layer-generation APIs):

- ``True``: STAC runs synchronously in-process (``upload_to_s3=False``).
- ``False``: STAC is queued on Celery only — no inline sync (``upload_to_s3=False``).
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from computing.models import Layer, LayerMapping
from computing.stac_trigger import layer_generation_sync_mode, trigger_stac_collection
from utilities.gee_utils import valid_gee_text

log = logging.getLogger(__name__)

_STAC_QUEUE = "nrm"


def _format_geoserver_name(template: str, layer: Layer) -> str:
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


def _resolve_mapping(layer: Layer) -> LayerMapping | None:
    """Return the best matching LayerMapping for a saved Layer."""
    if not layer.dataset_id:
        return None

    dataset_name = (layer.dataset.name or "").strip()
    if not dataset_name:
        return None

    candidates = list(
        LayerMapping.objects.filter(db_dataset_name=dataset_name, auto_stac=True)
    )
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    layer_name = (layer.layer_name or "").strip()
    if not layer_name:
        return None

    matches = [
        c
        for c in candidates
        if _format_geoserver_name(c.geoserver_layer_name, layer) == layer_name
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        log.warning(
            "STAC auto-trigger: no LayerMapping match for layer id=%s dataset=%s name=%s",
            layer.id,
            dataset_name,
            layer_name,
        )
        return None

    log.info(
        "STAC auto-trigger: %d ambiguous LayerMapping matches for layer id=%s; picking first",
        len(matches),
        layer.id,
    )
    return matches[0]


def trigger_stac_for_layer(layer: Layer, mapping: LayerMapping) -> dict:
    misc = layer.misc or {}
    asset_id = layer.gee_asset_path
    if asset_id in (None, "", "not available"):
        asset_id = None

    log.info(
        "STAC auto-trigger: layer id=%s (%s/%s) asset_id=%s sync_mode=%s",
        layer.id,
        mapping.layer_type,
        mapping.layer_name,
        asset_id,
        layer_generation_sync_mode(),
    )

    geoserver_layer = _format_geoserver_name(
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


@receiver(post_save, sender=Layer, dispatch_uid="computing.signals.trigger_stac_on_geoserver_sync")
def trigger_stac_on_geoserver_sync(sender, instance: Layer, created, **kwargs):
    if not instance.is_sync_to_geoserver or instance.is_stac_specs_generated:
        return

    mapping = _resolve_mapping(instance)
    if mapping is None:
        return

    try:
        trigger_stac_for_layer(instance, mapping)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "STAC auto-trigger: failed for layer id=%s: %s",
            instance.id,
            exc,
        )

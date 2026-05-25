"""Auto-trigger STAC collection generation when a Layer is synced to GeoServer.

Uses ``LAYER_GENERATION_SYNC_MODE`` (same as layer-generation APIs):

- ``True``: STAC runs synchronously in ``update_layer_sync_status`` before save.
- ``False``: STAC is queued on Celery via this signal when ``is_sync_to_geoserver`` flips.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from computing.models import Layer
from computing.stac_trigger import (
    layer_generation_sync_mode,
    resolve_mapping_from_layer,
    trigger_stac_for_layer,
)

log = logging.getLogger(__name__)


@receiver(post_save, sender=Layer, dispatch_uid="computing.signals.trigger_stac_on_geoserver_sync")
def trigger_stac_on_geoserver_sync(sender, instance: Layer, created, **kwargs):
    if not instance.is_sync_to_geoserver or instance.is_stac_specs_generated:
        return

    # Sync mode runs STAC inline in update_layer_sync_status before save.
    if layer_generation_sync_mode():
        return

    mapping = resolve_mapping_from_layer(instance)
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

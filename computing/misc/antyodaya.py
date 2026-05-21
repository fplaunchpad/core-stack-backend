"""
Thin CoRE Stack wrapper for the Mission Antyodaya layer pipeline.

The heavy local data work lives in
``utilities.scripts.antyodaya.antyodaya_utils``. Keeping this file small makes
the production entry point easier to review: API code imports the Celery task
from here, while utility scripts can still import the implementation helpers
through this module during the transition.
"""

from __future__ import annotations

from nrm_app.celery import app
from utilities.scripts.antyodaya import antyodaya_utils as _utils

generate_antyodaya_layer = _utils.generate_antyodaya_layer
list_antyodaya_states = _utils.list_antyodaya_states
list_antyodaya_districts = _utils.list_antyodaya_districts
list_antyodaya_blocks = _utils.list_antyodaya_blocks
ANTYODAYA_LAYER_PREFIX = _utils.ANTYODAYA_LAYER_PREFIX


def __getattr__(name):
    return getattr(_utils, name)


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def generate_antyodaya_layer_task(
    self,
    state,
    district,
    block,
    gee_account_id=None,
    sync_to_gee=True,
    sync_to_geoserver=True,
    overwrite=False,
    make_gee_asset_public=True,
):
    """
    Celery task wrapper for one TEHSIL/block Antyodaya layer generation.

    The task delegates to the utility module and keeps GEE/GeoServer optional so
    local-only, GeoServer-only, and full publish modes all share the same local
    dataframe construction and output schema.
    """
    return _utils.generate_antyodaya_layer(
        state=state,
        district=district,
        block=block,
        gee_account_id=gee_account_id,
        sync_to_gee=sync_to_gee,
        sync_to_geoserver=sync_to_geoserver,
        overwrite=overwrite,
        make_gee_asset_public=make_gee_asset_public,
    )

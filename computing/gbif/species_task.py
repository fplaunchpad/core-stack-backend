"""
Celery tasks that orchestrate the species pipeline (Plan B).

Follows the repo task pattern (`@app.task(bind=True)`, `queue="nrm"`) used by get_change_detection.

  generate_species_richness  -> Level A: per-MWS richness snapshot for a taxon in a block
  generate_species_change    -> Level B: rarefied richness change (then vs now) for a taxon

Both take the same inputs the change-detection endpoints take, plus `taxon_key`.
"""

import json

from nrm_app.celery import app
from utilities.gee_utils import ee_initialize, valid_gee_text
from computing.utils import (
    sync_layer_to_geoserver,
    save_layer_info_to_db,
    update_layer_sync_status,
)

from . import config
from .gbif_download import download_occurrences
from .gbif_clean import clean_occurrences
from .gbif_richness import mws_species_richness
from .gbif_species_change import mws_species_change, split_window


def _prepare_occurrences(taxon_key, start_year, end_year):
    """Download (cached) + clean the occurrences for this taxon/window. Returns a DataFrame."""
    raw_csv = download_occurrences(taxon_key, int(start_year), int(end_year))
    return clean_occurrences(raw_csv)


def _publish(state, district, block, gdf, layer_name, dataset_name):
    """GeoDataFrame -> GeoServer vector + DB registration (standard chain)."""
    fc = json.loads(gdf.to_json())
    res = sync_layer_to_geoserver(state, fc, layer_name, config.WORKSPACE)
    layer_id = save_layer_info_to_db(
        state,
        district,
        block,
        layer_name=layer_name,
        asset_id="not available",
        dataset_name=dataset_name,
        algorithm="GBIF",
    )
    if res.get("status_code") == 201 and layer_id:
        update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
        return True
    return False


@app.task(bind=True)
def generate_species_richness(
    self, state, district, block, taxon_key, start_year, end_year, gee_account_id
):
    """Level A — per-MWS species richness snapshot."""
    ee_initialize(gee_account_id)
    clean_df = _prepare_occurrences(taxon_key, start_year, end_year)
    gdf = mws_species_richness(clean_df, state, district, block)
    layer_name = (
        f"species_richness_{valid_gee_text(district.lower())}_"
        f"{valid_gee_text(block.lower())}_{taxon_key}"
    )
    ok = _publish(state, district, block, gdf, layer_name, "Species Richness")
    return f"species_richness done for {district}_{block} taxon={taxon_key}: synced={ok}"


@app.task(bind=True)
def generate_species_change(
    self, state, district, block, taxon_key, start_year, end_year, gee_account_id
):
    """Level B — rarefied species richness change (then vs now)."""
    ee_initialize(gee_account_id)
    clean_df = _prepare_occurrences(taxon_key, start_year, end_year)
    then_years, now_years = split_window(start_year, end_year)
    gdf = mws_species_change(
        clean_df, state, district, block, then_years, now_years
    )
    layer_name = (
        f"species_change_{valid_gee_text(district.lower())}_"
        f"{valid_gee_text(block.lower())}_{taxon_key}_{start_year}_{end_year}"
    )
    ok = _publish(state, district, block, gdf, layer_name, "Species Change")
    return f"species_change done for {district}_{block} taxon={taxon_key}: synced={ok}"

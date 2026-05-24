import os
import json
import datetime
import pandas as pd
import geopandas as gpd
from nrm_app.celery import app
from utilities.gee_utils import valid_gee_text
from computing.local_compute_helper import (
    PROJECT_ROOT,
    PRECOMPUTED_TEHSIL_WATERSHED_DIR,
    build_output_vector_path,
    get_watershed_areas_in_hectares,
    load_precomputed_watersheds,
    read_validated_vector_file,
    validate_geometry,
    write_vector_output,
)
from computing.utils import (
    push_shape_to_geoserver,
    save_layer_info_to_db,
    update_layer_sync_status,
    fix_invalid_geometry_in_gdf,
)

FACILITIES_POLYGON_PATH = PROJECT_ROOT / "data/facilities/facilities_polygon.gpkg"
LOCAL_OUTPUT_BASE_DIR = PROJECT_ROOT / "data/facilities/facilities_local"
GEOSERVER_WORKSPACE = "facilities"

from shapely.ops import unary_union


def _compute_proximity_for_watersheds(watersheds_gdf, facilities_gdf):

    watersheds_gdf = validate_geometry(watersheds_gdf).reset_index(drop=True)
    facilities_gdf = validate_geometry(facilities_gdf).reset_index(drop=True)

    outer_boundary = watersheds_gdf.geometry.unary_union

    # ── Step 1: Filter by ROI (equivalent to GEE filterBounds) ────────────
    facilities_in_roi = facilities_gdf[facilities_gdf.intersects(outer_boundary)].copy()

    if facilities_in_roi.empty:
        print("No facilities found within the outer boundary.")
        return gpd.GeoDataFrame(columns=facilities_gdf.columns, crs=facilities_gdf.crs)

    print(f"Facilities within outer boundary: {len(facilities_in_roi)}")

    # ── Step 2: Final cleanup ─────────────────────────────────────────────
    facilities_in_roi = facilities_in_roi[~facilities_in_roi.geometry.is_empty]
    facilities_in_roi = facilities_in_roi[facilities_in_roi.geometry.is_valid]
    facilities_in_roi = facilities_in_roi[facilities_in_roi.geometry.notna()]
    facilities_in_roi = fix_invalid_geometry_in_gdf(facilities_in_roi)

    facilities_in_roi = facilities_in_roi[
        facilities_in_roi.geometry.apply(
            lambda g: g is not None
            and not g.is_empty
            and g.bounds[0] <= g.bounds[2]
            and g.bounds[1] <= g.bounds[3]
        )
    ]

    print(f"Final valid facilities: {len(facilities_in_roi)}")
    return facilities_in_roi


@app.task(bind=True)
def facilities_proximity_vector(
    self,
    asset_folder_list=None,
    app_type=None,
    gee_account_id=None,
    state=None,
    district=None,
    block=None,
    asset_suffix=None,
    roi=None,
    facilities_polygon_path=FACILITIES_POLYGON_PATH,
    precomputed_roi_dir=None,
    push_to_geoserver=True,
    sync_layer_metadata=True,
):
    """
    Orchestrates the local facilities vector generation.
    """
    if state and district and block:
        layer_name = (
            f"facilities_{valid_gee_text(str(district).strip().lower())}_{valid_gee_text(str(block).strip().lower())}"
        )
        watersheds_gdf, watershed_source = load_precomputed_watersheds(
            state=state,
            district=district,
            block=block,
            precomputed_roi_dir=precomputed_roi_dir,
        )
        print(f"Watershed boundary source: {watershed_source}")
    else:
        if not roi or not asset_suffix:
            raise ValueError(
                "For non state/district/block runs, both `roi` and `asset_suffix` are required."
            )
        layer_name = f"{asset_suffix}_facilities".lower()
        watersheds_gdf = read_validated_vector_file(
            roi,
            f"ROI file has no valid geometries: {roi}",
        )
        print(f"ROI source: {roi}")

    if not os.path.exists(FACILITIES_POLYGON_PATH):
        raise FileNotFoundError(f"PAN INDIA Facililities file not found")

    facilities_gdf = read_validated_vector_file(
        FACILITIES_POLYGON_PATH,
        f"PAN INDIA Facililities file has no valid geometries",
    )

    result_gdf = _compute_proximity_for_watersheds(
        watersheds_gdf=watersheds_gdf,
        facilities_gdf=facilities_gdf,
    )

    output_path = build_output_vector_path(
        layer_name=layer_name,
        state=state,
        district=district,
        block=block,
        output_base_dir=LOCAL_OUTPUT_BASE_DIR,
    )

    asset_id = write_vector_output(
        gdf=result_gdf,
        output_path=output_path,
        layer_name=layer_name,
    )
    print(f"Saved local facilities vector: {asset_id}")

    if push_to_geoserver:
        geoserver_response = push_shape_to_geoserver(
            os.path.splitext(asset_id)[0],
            workspace=GEOSERVER_WORKSPACE,
            layer_name=layer_name,
            file_type="gpkg",
        )
        print(f"GeoServer response: {geoserver_response}")

    if sync_layer_metadata and state and district and block:
        layer_id = save_layer_info_to_db(
            state=state,
            district=district,
            block=block,
            layer_name=layer_name,
            asset_id=asset_id,
            dataset_name="Facilities",
        )
        if layer_id:
            update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
            print("Sync to GeoServer flag updated for facilities vector")

    return True


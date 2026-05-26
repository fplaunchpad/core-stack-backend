import os
import geopandas as gpd

from nrm_app.celery import app
from utilities.gee_utils import valid_gee_text
from computing.utils import (
    push_shape_to_geoserver,
    save_layer_info_to_db,
    update_layer_sync_status,
)
from computing.local_compute_helper import (
    PROJECT_ROOT,
    build_output_vector_path,
    load_precomputed_watersheds,
    read_validated_vector_file,
    write_vector_output,
)


GREEN_CREDIT_PAN_INDIA_LOCAL_PATH = (
    PROJECT_ROOT / "data/base_layers/Pan_India_green_credit.geojson"
)
GREEN_CREDIT_OUTPUT_BASE_DIR = PROJECT_ROOT / "data/layers/green_credit"
GEOSERVER_WORKSPACE = "green_credit"


def _compute_green_credit_for_watersheds(watersheds_gdf, green_credit_gdf):
    """
    Spatially joins Green Credit features with watershed polygons.
    Equivalent to the GEE Join.saveFirst() with spatial intersection.
    """
    if green_credit_gdf.empty:
        return green_credit_gdf
        
    if watersheds_gdf.crs and green_credit_gdf.crs and watersheds_gdf.crs != green_credit_gdf.crs:
        green_credit_gdf = green_credit_gdf.to_crs(watersheds_gdf.crs)

    # We only need the 'uid' from watersheds
    target_watersheds = watersheds_gdf[["uid", "geometry"]].copy()
    
    joined_gdf = gpd.sjoin(
        green_credit_gdf,
        target_watersheds,
        how="inner",
        predicate="intersects"
    )

    # To mimic ee.Join.saveFirst(), drop duplicates based on the original feature index
    joined_gdf = joined_gdf[~joined_gdf.index.duplicated(keep="first")]
    
    if "index_right" in joined_gdf.columns:
        joined_gdf = joined_gdf.drop(columns=["index_right"])

    return joined_gdf


@app.task(bind=True)
def generate_green_credit_data_local(
    self,
    state=None,
    district=None,
    block=None,
    asset_suffix=None,
    roi_path=None,
    gee_account_id=None,
    precomputed_roi_dir=None,
    push_to_geoserver=True,
    sync_layer_metadata=True,
):
    _ = self, gee_account_id
    if state and district and block:
        layer_name = f"{valid_gee_text(str(district).strip().lower())}_{valid_gee_text(str(block).strip().lower())}_green_credit"
        watersheds_gdf, watershed_source = load_precomputed_watersheds(
            state=state,
            district=district,
            block=block,
            precomputed_roi_dir=precomputed_roi_dir,
        )
        print(f"Watershed boundary source: {watershed_source}")
    else:
        if not roi_path or not asset_suffix:
            raise ValueError("ROI path and asset_suffix are required for custom runs.")
        layer_name = f"{asset_suffix}_green_credit".lower()
        watersheds_gdf = read_validated_vector_file(roi_path, f"Invalid ROI file: {roi_path}")
        print(f"ROI source: {roi_path}")

    if not os.path.exists(GREEN_CREDIT_PAN_INDIA_LOCAL_PATH):
        raise FileNotFoundError(f"PAN INDIA Green Credit file not found at {GREEN_CREDIT_PAN_INDIA_LOCAL_PATH}")

    print("Loading Green Credit data overlapping ROI...")
    green_credit_gdf = read_validated_vector_file(
        GREEN_CREDIT_PAN_INDIA_LOCAL_PATH,
        "PAN INDIA Green Credit file has no valid geometries overlapping ROI",
        mask=watersheds_gdf,
    )
    print(f"Loaded {len(green_credit_gdf)} Green Credit features")

    result_gdf = _compute_green_credit_for_watersheds(
        watersheds_gdf=watersheds_gdf,
        green_credit_gdf=green_credit_gdf,
    )
    print(f"Final valid Green Credit features after spatial join: {len(result_gdf)}")

    output_path = build_output_vector_path(
        layer_name=layer_name,
        state=state,
        district=district,
        block=block,
        output_base_dir=GREEN_CREDIT_OUTPUT_BASE_DIR,
    )

    asset_id = write_vector_output(
        gdf=result_gdf,
        output_path=output_path,
        layer_name=layer_name,
    )
    print(f"Saved local Green Credit vector: {asset_id}")

    layer_at_geoserver = False

    if push_to_geoserver:
        geoserver_response = push_shape_to_geoserver(
            os.path.splitext(asset_id)[0],
            workspace=GEOSERVER_WORKSPACE,
            layer_name=layer_name,
            file_type="gpkg",
        )
        print(f"GeoServer response: {geoserver_response}")
        if geoserver_response and geoserver_response.get("status_code") in (200, 201):
            layer_at_geoserver = True

    if sync_layer_metadata and state and district and block:
        layer_id = save_layer_info_to_db(
            state=state,
            district=district,
            block=block,
            layer_name=layer_name,
            asset_id=asset_id,
            dataset_name="Green Credit",
        )
        if layer_id:
            update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
            print("Sync to GeoServer flag updated for Green Credit vector")

    return layer_at_geoserver




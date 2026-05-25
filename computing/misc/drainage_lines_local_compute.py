import os
import geopandas as gpd
from nrm_app.celery import app
from utilities.gee_utils import valid_gee_text
from computing.local_compute_helper import (
    PROJECT_ROOT,
    build_output_vector_path,
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
from projects.models import Project

DRAINAGE_LINES_PATH = (
    PROJECT_ROOT / "data/layers/drainage_lines/Pan_India_drainage_lines.gpkg"
)
LOCAL_OUTPUT_BASE_DIR = (
    PROJECT_ROOT / "data/layers/drainage_lines/drainage_lines_local"
)
GEOSERVER_WORKSPACE = "drainage"


def _compute_drainage_lines_for_watersheds(watersheds_gdf, drainage_gdf):
    watersheds_gdf = validate_geometry(watersheds_gdf).reset_index(drop=True)
    drainage_gdf = validate_geometry(drainage_gdf).reset_index(drop=True)

    outer_boundary = watersheds_gdf.geometry.unary_union

    # Step 1: Filter drainage features that intersect the ROI (equivalent to GEE filterBounds)
    drainage_in_roi = drainage_gdf[drainage_gdf.intersects(outer_boundary)].copy()

    if drainage_in_roi.empty:
        print("No drainage lines found within the outer boundary.")
        return gpd.GeoDataFrame(columns=drainage_gdf.columns, crs=drainage_gdf.crs)

    print(f"Drainage lines within outer boundary: {len(drainage_in_roi)}")

    # Step 2: Drop empty/invalid geometries
    drainage_in_roi = fix_invalid_geometry_in_gdf(drainage_in_roi)
    drainage_in_roi = drainage_in_roi[
        drainage_in_roi.geometry.notna()
        & ~drainage_in_roi.geometry.is_empty
        & drainage_in_roi.geometry.is_valid
    ]

    print(f"Final valid drainage lines: {len(drainage_in_roi)}")
    return drainage_in_roi


@app.task(bind=True)
def clip_drainage_lines(
    self,
    state=None,
    district=None,
    block=None,
    asset_suffix=None,
    asset_folder=None,
    gee_account_id=None,
    roi_path=None,
    app_type="MWS",
    proj_id=None,
    precomputed_roi_dir=None,
    push_to_geoserver=True,
    sync_layer_metadata=True,
):
    """
    Orchestrates the local drainage lines vector generation.
    """
    _ = self, asset_folder, gee_account_id, app_type

    if state and district and block:
        layer_name = f"{valid_gee_text(str(district).strip().lower())}_{valid_gee_text(str(block).strip().lower())}_25may"
        watersheds_gdf, watershed_source = load_precomputed_watersheds(
            state=state,
            district=district,
            block=block,
            precomputed_roi_dir=precomputed_roi_dir,
        )
        print(f"Watershed boundary source: {watershed_source}")
    else:
        proj_obj = Project.objects.get(pk=proj_id)
        state = proj_obj.name
        layer_name = asset_suffix
        watersheds_gdf = read_validated_vector_file(
            roi_path,
            f"ROI file has no valid geometries: {roi_path}",
        )
        print(f"ROI source: {roi_path}")

    if not os.path.exists(DRAINAGE_LINES_PATH):
        raise FileNotFoundError(f"PAN INDIA drainage lines file not found at {DRAINAGE_LINES_PATH}")

    drainage_gdf = read_validated_vector_file(
        DRAINAGE_LINES_PATH,
        f"PAN INDIA drainage lines file has no valid geometries",
    )

    result_gdf = _compute_drainage_lines_for_watersheds(
        watersheds_gdf=watersheds_gdf,
        drainage_gdf=drainage_gdf,
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
    print(f"Saved local drainage lines vector: {asset_id}")

    if push_to_geoserver:
        geoserver_response = push_shape_to_geoserver(
            os.path.splitext(asset_id)[0],
            workspace=GEOSERVER_WORKSPACE,
            layer_name=layer_name,
            file_type="gpkg",
        )
        print(f"GeoServer response: {geoserver_response}")

    if sync_layer_metadata:
        layer_id = None
        if state and district and block:
            layer_id = save_layer_info_to_db(
                state=state,
                district=district,
                block=block,
                layer_name=layer_name,
                asset_id=asset_id,
                dataset_name="Drainage",
            )
        if layer_id:
            update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
            print("Sync to GeoServer flag updated for drainage lines vector")

    return True

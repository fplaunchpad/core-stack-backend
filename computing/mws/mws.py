import ee
from nrm_app.celery import app

from computing.stac_trigger import enrich_task_return
from computing.utils import (
    sync_fc_to_geoserver,
    save_layer_info_to_db,
    update_layer_sync_status,
    geoserver_sync_succeeded,
)

from utilities.constants import MWS_DATASET
from utilities.gee_utils import (
    ee_initialize,
    check_task_status,
    valid_gee_text,
    get_gee_asset_path,
    is_gee_asset_exists,
    make_asset_public,
    export_vector_asset_to_gee,
)


@app.task(bind=True)
def mws_layer(self, state, district, block, gee_account_id):
    ee_initialize(gee_account_id)
    description = (
        "filtered_mws_"
        + valid_gee_text(district.lower())
        + "_"
        + valid_gee_text(block.lower())
        + "_uid"
    )
    asset_id = get_gee_asset_path(state, district, block) + description
    layer_name = (
        f"mws_{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}"
    )
    layer_id = None

    if not is_gee_asset_exists(asset_id):
        mwses_uid_fc = ee.FeatureCollection(MWS_DATASET)

        admin_boundary = ee.FeatureCollection(
            get_gee_asset_path(state, district, block)
            + "admin_boundary_"
            + valid_gee_text(district.lower())
            + "_"
            + valid_gee_text(block.lower())
        )
        filtered_mws_block_uid = mwses_uid_fc.filterBounds(admin_boundary.geometry())

        task_id = export_vector_asset_to_gee(
            filtered_mws_block_uid, description, asset_id
        )
        mws_task_id_list = check_task_status([task_id])
        print("mws_task_id_list", mws_task_id_list)

    layer_generated = False
    if is_gee_asset_exists(asset_id):
        make_asset_public(asset_id)
        layer_id = save_layer_info_to_db(
            state,
            district,
            block,
            layer_name=layer_name,
            asset_id=asset_id,
            dataset_name="MWS",
            algorithm_version="1.2",
        )
        fc = ee.FeatureCollection(asset_id)
        res = sync_fc_to_geoserver(
            fc,
            state,
            layer_name,
            "mws",
        )

        if geoserver_sync_succeeded(res) and layer_id:
            update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
            print("sync to geoserver flag is updated")
            layer_generated = True

    return enrich_task_return(
        layer_generated,
        asset_id=asset_id,
        layer_id=layer_id,
    )

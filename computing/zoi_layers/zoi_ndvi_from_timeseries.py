"""
ZOI NDVI using the ndvi_timeseries compute (HLS-interpolated NDVI).

Does not modify get_ndvi_data / get_ndvi_for_zoi. Accepts the same inputs as
get_ndvi_data and returns the same FeatureCollection shape (NDVI_<year> JSON properties).
"""

import ee

from computing.misc.hls_interpolated_ndvi import get_padded_ndvi_ts_image
from computing.surface_water_bodies.swb import sync_asset_to_db_and_geoserver
from computing.utils import sync_project_fc_to_geoserver
from projects.models import Project
from utilities.constants import GEE_PATHS
from utilities.gee_utils import (
    ee_initialize,
    get_gee_dir_path,
    check_task_status,
    is_gee_asset_exists,
)
from waterrejuvenation.utils import wait_for_task_completion


def _merge_ndvi_year_assets(chunk_assets):
    """Same merge logic as waterrejuvenation.utils.merge_assets_chunked_on_year."""

    def merge_features(feature):
        uid = feature.get("UID")
        matched_features = []
        for i in range(1, len(chunk_assets)):
            matched_feature = ee.Feature(
                ee.FeatureCollection(chunk_assets[i])
                .filter(ee.Filter.eq("UID", uid))
                .first()
            )
            matched_features.append(matched_feature)

        merged_properties = feature.toDictionary()
        for f in matched_features:
            merged_properties = merged_properties.combine(
                f.toDictionary(), overwrite=False
            )

        return ee.Feature(feature.geometry(), merged_properties)

    return ee.FeatureCollection(chunk_assets[0]).map(merge_features)


def build_ndvi_timeseries_from_timeseries_compute(
    suitability_vector,
    start_year,
    end_year,
    description,
    asset_id,
    ndvi_interval_days=14,
    reducer_scale=30,
):
    """
    Drop-in replacement for get_ndvi_data output shape, using ndvi_timeseries NDVI source.

    Args:
        suitability_vector: ee.FeatureCollection with UID (ZOI polygons).
        start_year, end_year, description, asset_id: Same as get_ndvi_data.

    Returns:
        ee.FeatureCollection with NDVI_<year> JSON properties (identical to get_ndvi_data).
    """
    task_ids = []
    asset_ids = []
    year = start_year

    while year <= end_year:
        start_date = f"{year}-07-01"
        end_date = f"{year + 1}-06-30"
        ndvi_description = f"ndvi_{year}_{description}"
        ndvi_asset_id = f"{asset_id}_ndvi_{year}"

        if is_gee_asset_exists(ndvi_asset_id):
            ee.data.deleteAsset(ndvi_asset_id)

        ndvi = get_padded_ndvi_ts_image(
            start_date, end_date, suitability_vector.bounds(), ndvi_interval_days
        )

        def map_image(image):
            date_str = image.date().format("YYYY-MM-dd")
            reduced = image.reduceRegions(
                collection=suitability_vector,
                reducer=ee.Reducer.mean(),
                scale=reducer_scale,
            )

            def annotate(feature):
                ndvi_val = ee.Algorithms.If(
                    ee.Algorithms.IsEqual(feature.get("gapfilled_NDVI_lsc"), None),
                    -9999,
                    feature.get("gapfilled_NDVI_lsc"),
                )
                return feature.set("ndvi_date", date_str).set("ndvi", ndvi_val)

            return reduced.map(annotate)

        all_ndvi = ndvi.map(map_image).flatten()
        uids = suitability_vector.aggregate_array("UID")

        def build_feature(uid):
            feature_geom = ee.Feature(
                suitability_vector.filter(ee.Filter.eq("UID", uid)).first()
            )
            filtered = all_ndvi.filter(ee.Filter.eq("UID", uid))
            date_ndvi_list = filtered.aggregate_array("ndvi_date").zip(
                filtered.aggregate_array("ndvi")
            )
            ndvi_dict = ee.Dictionary(date_ndvi_list.flatten())
            ndvi_json = ee.String.encodeJSON(ndvi_dict)
            return feature_geom.set(f"NDVI_{year}", ndvi_json)

        merged_fc = ee.FeatureCollection(uids.map(build_feature))

        try:
            task = ee.batch.Export.table.toAsset(
                collection=merged_fc,
                description=ndvi_description,
                assetId=ndvi_asset_id,
            )
            task.start()
            print(f"Started export for {year}")
            asset_ids.append(ndvi_asset_id)
            task_ids.append(task.status()["id"])
        except Exception as e:
            print("Export error:", e)

        year += 1

    check_task_status(task_ids)
    return _merge_ndvi_year_assets(asset_ids)


def get_ndvi_for_zoi_from_timeseries_compute(
    state=None,
    district=None,
    block=None,
    zoi_roi=None,
    asset_suffix=None,
    asset_folder_list=None,
    start_date="2017-07-01",
    end_date="2025-06-30",
    start_year=2017,
    end_year=2024,
    app_type="MWS",
    gee_account_id=None,
    proj_id=None,
):
    """
    Same orchestration and result as get_ndvi_for_zoi (zoi3), but NDVI values come from
    the ndvi_timeseries compute (get_padded_ndvi_ts_image) instead of get_ndvi_data.
    """
    print("started generating ndvi for zoi (timeseries compute)")
    ee_initialize(gee_account_id)

    description_zoi = "cropping_intensity_zoi_" + asset_suffix
    asset_id_zoi = (
        get_gee_dir_path(
            asset_folder_list, asset_path=GEE_PATHS[app_type]["GEE_ASSET_PATH"]
        )
        + description_zoi
    )

    if zoi_roi:
        zoi_collections = ee.FeatureCollection(zoi_roi)
    else:
        zoi_collections = ee.FeatureCollection(asset_id_zoi)

    description_ndvi = asset_suffix
    ndvi_asset_path = (
        get_gee_dir_path(
            asset_folder_list, asset_path=GEE_PATHS[app_type]["GEE_ASSET_PATH"]
        )
        + description_ndvi
    )

    fc = build_ndvi_timeseries_from_timeseries_compute(
        zoi_collections,
        start_year,
        end_year,
        description_ndvi,
        ndvi_asset_path,
    )

    task = ee.batch.Export.table.toAsset(
        collection=fc, description=description_ndvi, assetId=ndvi_asset_path
    )
    task.start()
    wait_for_task_completion(task)

    if state and district and block:
        layer_name = f"waterbodies_zoi_{asset_suffix}"
        sync_asset_to_db_and_geoserver(
            ndvi_asset_path,
            layer_name,
            asset_suffix,
            start_date,
            end_date,
            state,
            district,
            block,
        )
    elif proj_id:
        proj_obj = Project.objects.get(pk=proj_id)
        layer_name = f"waterbodies_zoi_{asset_suffix}"
        sync_project_fc_to_geoserver(
            fc, proj_obj.name, layer_name, "zoi_layers"
        )

    return fc

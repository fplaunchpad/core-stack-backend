import ee
from computing.utils import (
    sync_fc_to_geoserver,
    save_layer_info_to_db,
    update_layer_sync_status,
    get_layer_object,
)
from utilities.constants import GEE_PATHS
from utilities.gee_utils import (
    ee_initialize,
    check_task_status,
    valid_gee_text,
    get_gee_dir_path,
    is_gee_asset_exists,
    make_asset_public,
    export_vector_asset_to_gee,
    merge_fc_into_existing_fc,
)
from nrm_app.celery import app
from utilities.geoserver_utils import Geoserver
from enum import IntEnum
from typing import Optional, Any


class LULCLabel(IntEnum):
    BACKGROUND             = 0
    BUILT_UP               = 1
    WATER_KHARIF           = 2
    WATER_KHARIF_RABI      = 3
    WATER_KHARIF_RABI_ZAID = 4
    TREE_FOREST            = 6
    BARREN                 = 7
    SINGLE_KHARIF          = 8
    SINGLE_NON_KHARIF      = 9
    DOUBLE                 = 10
    TRIPLE                 = 11
    SHRUB_SCRUB            = 12


@app.task(bind=True)
def generate_cropping_intensity(
    self,
    state: Optional[str] = None,
    district: Optional[str] = None,
    block: Optional[str] = None,
    roi_path: Optional[str] = None,
    asset_suffix: Optional[str] = None,
    asset_folder_list: Optional[list[str]] = None,
    app_type: str = "MWS",
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    gee_account_id: Optional[str] = None,
    zoi_ci_asset: Optional[str] = None,
) -> bool:
    """
    Generate and sync a cropping intensity layer for a block-level location or
    a custom region of interest.
    """
    ee_initialize(gee_account_id)
    if state and district and block:
        asset_suffix: str = (
            valid_gee_text(district.lower()) + "_" + valid_gee_text(block.lower())
        )
        asset_folder_list: list[str] = [state, district, block]

        roi_path: str = (
            get_gee_dir_path(
                asset_folder_list, asset_path=GEE_PATHS[app_type]["GEE_ASSET_PATH"]
            )
            + f"filtered_mws_{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}_uid"
        )

    if zoi_ci_asset:
        description: str = "cropping_intensity_zoi_" + asset_suffix
        layer_name: str = f"{asset_suffix}_intensity_ZOI"
    else:
        description: str = "cropping_intensity_" + asset_suffix
        layer_name: str = f"{asset_suffix}_intensity"
    print(f"Description: {description=}")

    asset_id: str = (
        get_gee_dir_path(
            asset_folder_list, asset_path=GEE_PATHS[app_type]["GEE_ASSET_PATH"]
        )
        + description
    )

    print(f"Asset id string: {asset_id=}")

    roi = ee.FeatureCollection(roi_path)

    if is_gee_asset_exists(asset_id):
        layer_obj: Optional[Any] = None
        try:
            # `get_layer_object` fetches the most recent Layer record matching a 
            # geographic location + layer name + dataset
            layer_obj = get_layer_object(
                state,
                district,
                block,
                layer_name=layer_name,
                dataset_name="Cropping Intensity",
            )
        except Exception:
            print("DB layer not found for cropping intensity.")

        existing_end_year: int = get_last_date(asset_id, layer_obj)

        if existing_end_year < end_year:
            new_start_year: int = existing_end_year
            new_asset_id: str = f"{asset_id}_{new_start_year}_{end_year}"
            new_description: str = f"{description}_{new_start_year}_{end_year}"

            # generate_gee_asset returns (task_id, asset_id); task_id is None if asset already exists.
            if not is_gee_asset_exists(new_asset_id):
                print(f"{new_asset_id} doesn't exist")
                new_task_id, new_asset_id = generate_gee_asset(
                    roi,
                    new_asset_id,
                    new_description,
                    asset_suffix,
                    asset_folder_list,
                    app_type,
                    new_start_year,
                    end_year,
                    zoi=zoi_ci_asset,
                )
                if new_task_id:
                    check_task_status([new_task_id])
                    print("Cropping Intensity new year data generated.")

            # Check if data for new year is generated, if yes then merge it in existing asset
            if is_gee_asset_exists(new_asset_id):
                merge_fc_into_existing_fc(asset_id, description, new_asset_id)

    else:
        task_id, asset_id = generate_gee_asset(
            roi,
            asset_id,
            description,
            asset_suffix,
            asset_folder_list,
            app_type,
            start_year,
            end_year,
            zoi=zoi_ci_asset,
        )
        if task_id:
            task_status = check_task_status([task_id])
            print("Cropping intensity task completed - task_status: ", task_status)

    layer_at_geoserver = save_to_db_and_sync_to_geoserver(
        layer_name=layer_name,
        asset_id=asset_id,
        start_year=start_year,
        end_year=end_year,
        asset_suffix=asset_suffix,
        state=state,
        district=district,
        block=block,
    )
    return layer_at_geoserver


def generate_gee_asset(
    roi: ee.FeatureCollection,
    asset_id: str,
    description: str,
    asset_suffix: str,
    asset_folder_list: list[str],
    app_type: str,
    start_year: int,
    end_year: int,
    zoi: Optional[str] = None,
) -> tuple[Optional[str], str]:
    print("Now running the `generate_gee_asset` function.")
    print(f"ZOI asset: {zoi}")
    print(f"Final asset id: {asset_id}")

    if is_gee_asset_exists(asset_id):
        return None, asset_id

    lulc_scale: int = 10
    lulc_band_name: list[str] = ["predicted_label"]
    lulc_images: list[ee.Image] = []
    initial_year: int = 2017
    year: int = initial_year
    while year <= end_year:
        lulc_images.append(
            ee.Image(
                get_gee_dir_path(
                    asset_folder_list, asset_path=GEE_PATHS[app_type]["GEE_ASSET_PATH"]
                )
                + asset_suffix
                + "_"
                + str(year)
                + "-07-01_"
                + str(year + 1)
                + "-06-30_LULCmap_10m"
            )
        )
        year += 1
    lulc: ee.List = ee.List(lulc_images)

    args = [
        {"label": LULCLabel.SINGLE_KHARIF,     "txt": "single_kharif_cropped_area_"},
        {"label": LULCLabel.SINGLE_NON_KHARIF, "txt": "single_non_kharif_cropped_area_"},
        {"label": LULCLabel.DOUBLE,            "txt": "doubly_cropped_area_"},
        {"label": LULCLabel.TRIPLE,            "txt": "triply_cropped_area_"},
    ]

    def get_class_area(feature: ee.Feature) -> ee.Feature:
        value: ee.Number = ee.Number(feature.get("sum")).multiply(0.0001)
        return feature.set(arg["txt"] + str(current_year), value)

    for arg in args:
        year: int = start_year
        while year <= end_year:
            current_year: int = year
            image: ee.Image = ee.Image(lulc.get(current_year - initial_year)).select(lulc_band_name)
            mask: ee.Image = image.eq(ee.Number(arg["label"]))
            pixel_area: ee.Image = ee.Image.pixelArea()
            masked_pixel_area: ee.Image = pixel_area.updateMask(mask)
            roi = masked_pixel_area.reduceRegions(
                roi, ee.Reducer.sum(), lulc_scale, image.projection()
            )
            year += 1
            roi = roi.map(get_class_area)

    # Single cropped area
    year: int = start_year

    def get_single_cropped_area(feature: ee.Feature) -> ee.Feature:
        single_kharif: ee.Number = ee.Number(feature.get("single_kharif_cropped_area_" + str(current_year)))
        single_non_kharif: ee.Number = ee.Number(
            feature.get("single_non_kharif_cropped_area_" + str(current_year))
        )
        return feature.set(
            "single_cropped_area_" + str(current_year),
            single_kharif.add(single_non_kharif),
        )

    while year <= end_year:
        current_year: int = year
        year += 1
        roi = roi.map(get_single_cropped_area)

    # croppable area
    single_kharif_all_years: ee.Image = ee.Image.constant(0)
    single_non_kharif_all_years: ee.Image = ee.Image.constant(0)
    triple_all_years: ee.Image = ee.Image.constant(0)
    double_all_years: ee.Image = ee.Image.constant(0)

    year: int = initial_year

    while year <= end_year:
        image: ee.Image = ee.Image(lulc.get(year - initial_year)).select(lulc_band_name)
        single_kharif_all_years = single_kharif_all_years.Or(image.eq(LULCLabel.SINGLE_KHARIF))
        single_non_kharif_all_years = single_non_kharif_all_years.Or(
            image.eq(LULCLabel.SINGLE_NON_KHARIF)
        )
        double_all_years = double_all_years.Or(image.eq(LULCLabel.DOUBLE))
        triple_all_years = triple_all_years.Or(image.eq(LULCLabel.TRIPLE))
        year += 1

    croppable_area_all_years: ee.Image = (
        single_kharif_all_years.Or(single_non_kharif_all_years)
        .Or(triple_all_years)
        .Or(double_all_years)
    )
    mask: ee.Image = croppable_area_all_years
    pixel_area: ee.Image = ee.Image.pixelArea()
    croppable_area: ee.Image = pixel_area.updateMask(mask)
    roi = croppable_area.reduceRegions(roi, ee.Reducer.sum(), lulc_scale)

    def calculate_total_cropped_area(feature: ee.Feature) -> ee.Feature:
        value: ee.Number = ee.Number(feature.get("sum")).multiply(0.0001)
        return feature.set(
            "total_cropable_area_ever_hydroyear_"
            + str(initial_year)
            + "_"
            + str(end_year),
            value
        )

    roi = roi.map(calculate_total_cropped_area)

    def calculate_cropping_intensity(feature: ee.Feature) -> ee.Feature:
        year: int = start_year
        while year <= end_year:
            total_croppable_area: ee.Number = ee.Number(feature.get(
                "total_cropable_area_ever_hydroyear_"
                + str(initial_year)
                + "_"
                + str(end_year)
            ))

            single_cropped_area: ee.Number = ee.Number(feature.get("single_cropped_area_" + str(year)))
            double_cropped_area: ee.Number = ee.Number(feature.get("doubly_cropped_area_" + str(year)))
            triple_cropped_area: ee.Number = ee.Number(feature.get("triply_cropped_area_" + str(year)))

            single_fraction: ee.Number = single_cropped_area.divide(total_croppable_area)
            double_fraction: ee.Number = double_cropped_area.divide(total_croppable_area)
            triple_fraction: ee.Number = triple_cropped_area.divide(total_croppable_area)

            cropping_intensity: ee.Number = single_fraction.add(double_fraction.multiply(2)).add(
                triple_fraction.multiply(3)
            )

            feature = feature.set(
                "cropping_intensity_" + str(year), cropping_intensity
            )
            year += 1

        return feature

    roi = ee.FeatureCollection(roi.map(calculate_cropping_intensity))

    # Export feature collection to GEE
    task_id: Optional[str] = export_vector_asset_to_gee(roi, description, asset_id)
    return task_id, asset_id


def save_to_db_and_sync_to_geoserver(
    layer_name: Optional[str] = None,
    asset_id: Optional[str] = None,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    asset_suffix: Optional[str] = None,
    state: Optional[str] = None,
    district: Optional[str] = None,
    block: Optional[str] = None,
) -> bool:
    print("Now processing `save_to_db_and_sync_to_geoserver` ... ")
    layer_id: Optional[Any] = None
    # TODO: currently saving info to DB for block level layers only, 
    # make changes to accommodate others
    if (state and district and block):  
        layer_id = save_layer_info_to_db(
            state=state,
            district=district,
            block=block,
            layer_name=layer_name,
            asset_id=asset_id,
            dataset_name="Cropping Intensity",
            misc={
                "start_year": start_year,
                "end_year": end_year,
            },
        )

    make_asset_public(asset_id)

    fc = ee.FeatureCollection(asset_id)
    sync_result: dict[str, Any] = sync_fc_to_geoserver(fc, asset_suffix, layer_name, "crop_intensity")
    print(f"Geoserver sync result: {sync_result}")
    layer_at_geoserver: bool = False
    # TODO: currently saving info to DB for block level layers only, 
    # make changes to accommodate all
    if (sync_result["status_code"] == 201 and layer_id):  
        update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
        print("sync to geoserver flag updated")
        layer_at_geoserver = True
    return layer_at_geoserver


def get_last_date(asset_id: str, layer_obj: Optional[Any]) -> int:
    if layer_obj:
        existing_end_year = layer_obj.misc["end_year"]
    else:
        fc: ee.FeatureCollection = ee.FeatureCollection(asset_id)
        col_names: list[str] = fc.first().propertyNames().getInfo()
        filtered_col: list[str] = [
            col.split("_")[2]
            for col in col_names
            if col.startswith("cropping_intensity_")
        ]
        filtered_col.sort()
        existing_end_year = filtered_col[-1]

    return int(existing_end_year)

import ee
from computing.utils import (
    sync_fc_to_geoserver,
    save_layer_info_to_db,
    update_layer_sync_status,
)
from utilities.gee_utils import (
    valid_gee_text,
    get_gee_asset_path,
    export_vector_asset_to_gee,
    is_gee_asset_exists,
    check_task_status,
    make_asset_public,
)
from typing import Literal


def crop_grids_lulc(state: str, district: str, block: str) -> bool:
    """Filter crop grid tiles by LULC crop cover, export to GEE, save to DB, and publish 
    to GeoServer."""
    
    lulc_image: ee.Image = ee.Image(
        get_gee_asset_path(state, district, block)
        + valid_gee_text(district.lower()) + "_"
        + valid_gee_text(block.lower()) + "_2023-07-01_2024-06-30_LULCmap_10m"
    )

    tiles_uid: ee.FeatureCollection = ee.FeatureCollection(
        get_gee_asset_path(state, district, block)
        + "crop_grid_"
        + valid_gee_text(district.lower()) + "_"
        + valid_gee_text(block.lower()) + "_with_uid_16ha"
    )

    # Generate crop tiles
    description = (
        "crop_gridXlulc_"
        + valid_gee_text(district.lower()) + "_"
        + valid_gee_text(block.lower()) + "_with_uid_16ha"
    )

    asset_id: str = get_gee_asset_path(state, district, block) + description
    crop_tiles: ee.FeatureCollection = lulc_crop_tiles(tiles_uid, lulc_image)
    layer_name: str = (
        f"{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}_grid"
    )
    if not is_gee_asset_exists(asset_id):
        task: str | None  = export_vector_asset_to_gee(crop_tiles, description, asset_id)
        if task:
            task_id = check_task_status([task])
            print(f"crop gridXlulc task completed  - task_id: {task_id}")

    layer_at_geoserver: bool = False
    if is_gee_asset_exists(asset_id):
        make_asset_public(asset_id)
        layer_id: int | None = save_layer_info_to_db(
            state,
            district,
            block,
            layer_name=layer_name,
            asset_id=asset_id,
            dataset_name="Crop GridXlulc",
        )

        result: Literal['No features in FeatureCollection'] | dict[str, int | str]
        result = sync_fc_to_geoserver(
            crop_tiles, state, layer_name, workspace="crop_grid_layers"
        )
        if result["status_code"] == 201 and layer_id:
            # update flag in db affirming that the layer is syncned to geoserver
            update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
            print("sync to geoserver flag updated")
            layer_at_geoserver = True
        print("Successfully pushed to GeoServer!", result)
    return layer_at_geoserver


def lulc_crop_tiles(tiles_uid: ee.FeatureCollection
                    , lulc_image: ee.Image) -> ee.FeatureCollection:
    """Filter tiles to those with more than 40% crop cover according to the LULC image.

    Maps compute_crop_fraction over every tile to attach a 'fraction' property,
    then filters to tiles where fraction > 0.4.
    """
    check_tile: ee.FeatureCollection = tiles_uid.map(lambda tile:
                                            compute_crop_fraction(tile, lulc_image))
    crop_tiles: ee.FeatureCollection = check_tile.filter(ee.Filter.gt("fraction", 0.4))
    return crop_tiles


def compute_crop_fraction(poly: ee.Feature, lulc_image: ee.Image) -> ee.Feature:
    """Compute the crop cover fraction for a single tile and attach it as a property.

    Clips the LULC image to the tile's geometry, then sums the pixel fractions
    belonging to crop classes (single kharif, single non-kharif, double, triple)
    to produce a single crop cover fraction. Returns the tile (poly) with that
    fraction set as the 'fraction' property. This function is intended to be
    called via ee.FeatureCollection.map(), which applies it to every tile in the
    collection and produces an annotated collection as a result.
    """
    col: ee.Image = lulc_image.clip(poly.geometry())

    classification: ee.Image = col.select(["predicted_label"])
    dw_composite: ee.Image = classification.reduce(ee.Reducer.mode())

    single_kharif: ee.Number = cover(9.0, poly, dw_composite)  # single kharif
    single_non_kharif: ee.Number = cover(10.0, poly, dw_composite)  # single non-kharif
    double: ee.Number = cover(11.0, poly, dw_composite)  # double
    triple: ee.Number = cover(12.0, poly, dw_composite)  # triple

    fraction: ee.Number = single_kharif.add(single_non_kharif.add(double.add(triple)))
    return poly.set("fraction", fraction)


def cover(cls: float, geo: ee.Feature, dw_composite: ee.Image) -> ee.Number:
    """Compute the fraction of a tile's area covered by a specific LULC class.

    Creates a binary image where pixels matching cls are 1 and all others are 0.
    Counts all pixels in the tile geometry as the total, then uses selfMask() to
    zero out non-matching pixels so that a second count captures only the matching
    ones. Returns matching_pixels / total_pixels as the cover fraction.
    """
    relevant_area: ee.Image = dw_composite.eq(cls).rename(["relevant_area"])
    stats_total: ee.Dictionary = relevant_area.reduceRegion(
        reducer=ee.Reducer.count(), 
        geometry=geo.geometry(), 
        scale=30, maxPixels=1e10
    )
    total_pixels: ee.ComputedObject = stats_total.get("relevant_area")

    relevant_area_masked: ee.Image = relevant_area.selfMask()
    stats_masked: ee.Dictionary = relevant_area_masked.reduceRegion(
        reducer=ee.Reducer.count(), geometry=geo.geometry(), scale=30, maxPixels=1e10
    )
    relevant_area_pixels: ee.ComputedObject = stats_masked.get("relevant_area")
    fraction: ee.Number = ee.Number(relevant_area_pixels).divide(total_pixels)
    return fraction

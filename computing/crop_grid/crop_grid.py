import json
import os
import ee
import geojson
import geopandas as gpd
from shapely import geometry
from typing import Any

from utilities.gee_utils import (
    ee_initialize,
    check_task_status,
    valid_gee_text,
    get_gee_asset_path,
    is_gee_asset_exists,
    export_vector_asset_to_gee,
)
from .crop_gridXlulc import crop_grids_lulc
from nrm_app.celery import app
# TODO: Documentation needed for these constants
from utilities.constants import SOI_TEHSIL, CROP_GRID_PATH, CRS_4326


@app.task(bind=True)
def create_crop_grids(self, state: str, district: str
                          , block: str, gee_account_id: int) -> bool:
    """
    Generate crop grid layer for the given location (tehsil level)

    For a given GEE asset located by a state-district-block combination,
    this function:
    - obtains the block co-ordinates
    - generates a path based on state, district and block values
    - generates GeoJSON coordinates based on the constructed path
    - generates grids based on path and block co-ordinates
    - generates a LULC layer based for a state-district-block combination and
    returns it
    """
    ee_initialize(gee_account_id)
    description = (
        "crop_grid_"
        + valid_gee_text(district.lower())
        + "_"
        + valid_gee_text(block.lower() + "_with_uid_16ha"))

    asset_id = get_gee_asset_path(state, district, block) + description

    # Use block co-ordinates to generate GeoJSON to FeatureCollection asset
    # when a ready GEE asset is not available
    if not is_gee_asset_exists(asset_id):
        # Get block coordinates
        block_coords = get_block_coordinates(state, district, block)
        geom_len = len(block_coords)
        state_dir = os.path.join(CROP_GRID_PATH, state)

        if not os.path.exists(state_dir):
            os.mkdir(state_dir)

        path = os.path.join(
            state_dir,
            f"{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}",
        )

        if not os.path.exists(path):
            os.mkdir(path)

        path = os.path.join(
            path,
            f"{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}",
        )

        # Generate grid GeoJSON files
        gen_geojson_from_coords(path, block_coords)
        gen_grids(path, block_coords)

        task_id = convert_geojson_to_fc(state, district, block, path, geom_len)
        if task_id:
            task_id_list = check_task_status([task_id])
            print("task_id_list", task_id_list)

    layer_at_geoserver = crop_grids_lulc(state, district, block)
    return layer_at_geoserver


def get_block_coordinates(state: str, district: str
                                    , block: str) -> list[list[list[float]]]:
    """
    Compute the coordinates for tenhil polygons/multipolygons
    """
    soi = gpd.read_file(SOI_TEHSIL)

    # TODO: Why are the header names in different cases?
    soi = soi[soi["STATE"].isin([state, state.lower(), state.upper()
                                      , state.title()])]
    soi = soi[soi["District"].isin([district, district.lower()
                                    , district.upper(), district.title()])]
    soi = soi[soi["TEHSIL"].isin([block, block.lower(), block.upper()
                                  , block.title()])]

    coordinates = []
    for geometry in soi.geometry:
        # Extract exterior coordinates and convert to list of lists
        if geometry.geom_type == "Polygon":
            poly_coords = [list(coord) for coord in geometry.exterior.coords]
            coordinates.append(poly_coords)
        elif geometry.geom_type == "MultiPolygon": # Handle MultiPolygon geometries
            multi_poly_coords = []
            for polygon in geometry.geoms:
                poly_coords = [list(coord) for coord in polygon.exterior.coords]
                multi_poly_coords.append(poly_coords)
            coordinates.extend(multi_poly_coords)

    return coordinates


def gen_geojson_from_coords(path: str, coords: list[list[list[float]]]) -> None:
    """
    Create GeoJSON data directly without using the Polygon class
    coords is list of polygons where each polygon is list of list of coordinate pairs
    """
    feature = {
        "type": "Feature",
        "properties": {"name": "poly1", "fill": "#FF0000"},
        "geometry": {
            "type": "Polygon",
            "coordinates": coords
        },
    }

    feature_collection = {"type": "FeatureCollection", "features": [feature]}

    with open(path + ".geojson", "w") as f:
        geojson.dump(feature_collection, f)


def gen_grids(path: str, coords_list: list[list[list[float]]]) -> None:
    # Extract the inner coordinate list
    idx = 1
    for coords in coords_list:
        x_ : list[float] = []
        y_ : list[float] = []
        for coord in coords:
            x_.append(coord[0])  # longitude
            y_.append(coord[1])  # latitude

        min_x = min(x_)
        max_x = max(x_)
        min_y = min(y_)
        max_y = max(y_)

        grid_size: float = 0.004
        cover_frac: float = 0.3
        grid: list[list[list[float]]] = []

        curr_x: float = min_x
        curr_y: float = min_y
        while curr_x <= max_x:
            while curr_y <= max_y:
                grid_cell : list[list[float]] = [
                    [curr_x, curr_y],
                    [curr_x + grid_size, curr_y],
                    [curr_x + grid_size, curr_y + grid_size],
                    [curr_x, curr_y + grid_size],
                    [curr_x, curr_y],
                ]
                grid.append(grid_cell)
                curr_y += grid_size
            curr_y = min_y
            curr_x += grid_size

        # Create polygon from original coordinates
        poly2 = geometry.Polygon(coords)

        final_grid: list[list[list[float]]] = []
        features: list[dict[str, Any]] = []

        for box in grid:
            try:
                poly1 = geometry.Polygon(box)
                intersection = poly2.intersection(poly1)
                if (
                    poly1.within(poly2)
                    or intersection.area >= cover_frac * grid_size * grid_size
                ):
                    # Create GeoJSON feature directly
                    features.append(
                        {
                            "type": "Feature",
                            "properties": {},
                            "geometry": {"type": "Polygon", "coordinates": [box]},
                        }
                    )
                    final_grid.append(box)
            except Exception as e:
                print(f"Error processing grid cell: {e}")
                continue

        feature_collection = {"type": "FeatureCollection", "features": features}

        with open(path + "_grids_without_LULC_" + str(idx) + ".geojson", "w") as f:
            json.dump(feature_collection, f)

        idx += 1


def gdf_to_ee_fc(gdf: gpd.GeoDataFrame) ->  list[ee.Feature]:
    """
    Convert a GeoDataFrame to a list of EE Features with their
    geometries and properties.
    """
    features: list[ee.Feature] = []
    for _, row in gdf.iterrows():
        properties: dict[str, Any] = row.drop("geometry").to_dict()
        geometry: ee.Geometry = ee.Geometry(row.geometry.__geo_interface__)
        feature: ee.Feature = ee.Feature(geometry, properties)
        features.append(feature)
    return features


def convert_geojson_to_fc(state: str, district: str, block: str
                                    , path: str
                                    , geom_len: int) -> ee.batch.Task | None:
    """
    Convert GeoJSON looked up via state-district-block to FeatureCollection
    and pushes the feature collection to create a GEE asset
    """
    features : list[ee.Feature] = []
    for idx in range(1, geom_len + 1):
        gdf: gpd.GeoDataFrame = gpd.read_file(path +
                                                "_grids_without_LULC_" +
                                                str(idx) + ".geojson")
        unique_ids : list[str] = []
        for i in range(gdf.shape[0]):
            unique_ids.append(block + "_" + str(i))
        # Create a new GDF columns with unique ids based on the current block
        # and grids shapes from input GeoJSON
        gdf["uid"] = unique_ids
        gdf = gdf.to_crs(CRS_4326)

        ee_fc: list[ee.Feature] = gdf_to_ee_fc(gdf)
        features.extend(ee_fc)
    print("Features' count=", len(features))

    if len(features) > 15000:
        return generate_in_chunks(block, district, features, state)
    else:
        print("Less than 15000 features")
        description = (
            "crop_grid_"
            + valid_gee_text(district.lower())
            + "_"
            + valid_gee_text(block.lower() + "_with_uid_16ha")
        )

        return generate_crop_grid_gee(state, district, block, features, description)


def generate_crop_grid_gee(state: str, district: str, block: str
                                      , features: list[ee.Feature]
                                      , description: str) -> ee.batch.Task | None:
    """
    Export a list of EE Features as a FeatureCollection to a GEE asset.

    The function skips the export if the asset already exists. It returns
    the export task, or None if the asset already exists or the export fails.
    """

    if not is_gee_asset_exists(get_gee_asset_path(state, district, block)
                               + description):
        fc: ee.FeatureCollection = ee.FeatureCollection(features)
        try:
            exported_gee_asset = export_vector_asset_to_gee(
                fc,
                description,
                get_gee_asset_path(state, district, block) + description,
            )
            print("Successfully started the crop_grid")
            return exported_gee_asset
        except Exception as e:
            print(f"Error occurred in running crop_grid task: {e}")
    return None


def generate_in_chunks(block: str, district: str, features: list[ee.Feature]
                                                , state: str) -> ee.batch.Task | None:
    """
    Split features into chunks of 15000 and upload each as a separate GEE asset.

    The function waits for all chunk uploads to complete, then merges them
    into a single crop grid FeatureCollection asset for the given
    state-district-block. It finally returns the merged asset
    (via merge_chunks) or None if the merge fails.
    """

    chunk_size: int = 15000
    print("NOTE: chunk size has more than 15000 features")
    crop_task_list: list[str] = []
    asset_ids: list[str] = []

    for i in range(0, len(features), chunk_size):
        print(i)
        chunk = features[i : i + chunk_size]

        description = (
            "crop_grid_"
            + valid_gee_text(district.lower())
            + "_"
            + valid_gee_text(block.lower())
            + "_with_uid_16ha_"
            + str(i)
        )
        asset_ids.append(get_gee_asset_path(state, district, block) + description)

        crop_task = generate_crop_grid_gee(state, district, block, chunk, description)
        if crop_task:
            crop_task_list.append(crop_task)

    check_task_status(crop_task_list)

    return merge_chunks(state, district, block, asset_ids)


def merge_chunks(state: str, district: str, block: str
                           , asset_ids: list[str]) -> ee.batch.Task | None:
    """
    Merge separately uploaded chunk assets into a single GEE FeatureCollection
    asset.

    Looks up each asset in a list of given asset ids, flattens them into one FeatureCollection,
    and exports it as the final crop grid asset for the given
    state-district-block. It returns the export task, or None if the export fails.
    """
    gen_gee_description = (
        "crop_grid_"
        + valid_gee_text(district.lower()) + "_"
        + valid_gee_text(block.lower() + "_with_uid_16ha")
    )

    fc_assets = []
    for asset_id in asset_ids:
        fc_assets.append(ee.FeatureCollection(asset_id))

    ee_vector_asset = ee.FeatureCollection(fc_assets).flatten()
    gen_asset_id = get_gee_asset_path(state, district, block) + gen_gee_description

    try:
        # Export an ee.FeatureCollection as an Earth Engine asset.
        task = export_vector_asset_to_gee(ee_vector_asset
                                          , gen_gee_description, gen_asset_id)
        print("Successfully started the merge crop grid chunk")
        return task
    except Exception as e:
        print(f"Error occurred in running merge crop grid chunk task: {e}")

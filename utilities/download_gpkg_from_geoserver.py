import requests
import geopandas as gpd
from pathlib import Path

from nrm_app import settings
from utilities.gee_utils import valid_gee_text

"""
from utilities.download_gpkg_from_geoserver import generate_mws_gpkg
generate_mws_gpkg(state="Odisha", district="Srikakulam", block="Bhamini")
"""

BASE_OUTPUT_DIR = Path(
    "/home/cfpt-jedi/developer/shiv/core-stack-backend/data/base_layers/tehsil_watersheds"
)


def build_layer_and_path(state, district, block):
    """
    Create:
    - sanitized names
    - geoserver layer name
    - gpkg output path

    Example Output:
    mws_gpkg/odisha/srikakulam/bhamini.gpkg
    """

    state = valid_gee_text(state.lower())
    district = valid_gee_text(district.lower())
    block = valid_gee_text(block.lower())

    # GeoServer layer
    layer_name = f"mws:mws_{district}_{block}"
    output_dir = BASE_OUTPUT_DIR / state / district
    output_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = output_dir / f"{block}.gpkg"

    return layer_name, gpkg_path, district, block


def read_layer_from_geoserver(layer_name):
    """
    Read GeoServer WFS layer directly into GeoDataFrame.
    """

    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": layer_name,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
    }

    response = requests.get(
        settings.PROD_GEOSERVER_URL,
        params=params,
        auth=None,
        timeout=120,
        verify=False,
    )

    return gpd.read_file(response.text)


def generate_mws_gpkg(state, district, block):
    """
    Generate GPKG for each location.
    """

    # import urllib3

    # urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:

        layer_name, gpkg_path, district, block = build_layer_and_path(
            state, district, block
        )
        gdf = read_layer_from_geoserver(layer_name)

        if gdf.empty:
            print("Layer is empty")
            return None

        # CRS handling
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        else:
            gdf = gdf.to_crs("EPSG:4326")

        # Remove existing gpkg
        if gpkg_path.exists():
            gpkg_path.unlink()

        # Write gpkg
        gdf.to_file(
            gpkg_path,
            layer=f"{district}_{block}",
            driver="GPKG",
        )
        print(f"GPKG file created successfully : {gpkg_path}")
        return str(gpkg_path)

    except Exception as e:
        print(f"FAILED : {e}")
        return None

#!/usr/bin/env python3
"""Create all GeoServer workspaces for a local CoRE Stack installation.

Usage:
    python installation/setup_local_geoserver.py [--url URL] [--username U] [--password P]

Defaults to http://localhost:8080/geoserver with admin/geoserver.
Run this once after starting the GeoServer Docker container.
For style sync, run geoserver_style_bundle.py sync afterwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth

WORKSPACES = [
    "admin_boundaries", "agroecological", "aquifer", "biodiversity", "block_boundaries",
    "canopy_height", "catchment_area_singleflow", "ccd", "change_detection",
    "cite", "clart", "corestack", "crop_grid_layers", "crop_intensity",
    "cropping_drought", "cropping_intensity", "customkml", "dem",
    "distance_nearest_upstream_DL", "drainage", "drainage_density",
    "drainage_lines", "drought", "drought_causality", "equity",
    "factory_csr", "facilities_proximity", "filtered_mws", "green_credit",
    "hamlet_layer", "hhvte", "lcw", "LULC_level_1", "LULC_level_2",
    "LULC_level_3", "lulc_v4", "lulc_vector", "mining", "mws", "mws_centroid",
    "mws_connectivity", "mws_layers", "natural_depression", "ndvi_timeseries",
    "nrega_assets", "nrega_inequity", "nrmapp", "pan_india", "pan_india_asset",
    "panchayat_boundaries", "plantation", "resources", "restoration", "river",
    "canal", "slope_percentage", "soge", "stream_order", "swb", "terrain",
    "terrain_lulc", "testworkspace", "tree_overall_ch", "water_bodies",
    "waterrej", "well_layers", "works", "Zero-Does-Project", "zerodose",
    "zoi_layers",
]


def create_workspaces(base_url: str, username: str, password: str) -> None:
    auth = HTTPBasicAuth(username, password)
    created, existed, failed = 0, 0, 0

    for ws in WORKSPACES:
        check = requests.get(f"{base_url}/rest/workspaces/{ws}.json", auth=auth, timeout=10)
        if check.status_code == 200:
            existed += 1
            continue
        r = requests.post(
            f"{base_url}/rest/workspaces",
            auth=auth,
            headers={"Content-Type": "application/json"},
            json={"workspace": {"name": ws}},
            timeout=10,
        )
        if r.status_code == 201:
            created += 1
        else:
            print(f"  WARNING: failed to create '{ws}' (HTTP {r.status_code})")
            failed += 1

    print(f"Workspaces: {created} created, {existed} already existed, {failed} failed.")


# Workspace-scoped SLD styles to provision from the repo (name -> .sld file under geoserver/styles).
STYLES = {
    "biodiversity": ("biodiversity_mws", "biodiversity_mws.sld"),
}
_STYLES_DIR = Path(__file__).resolve().parent / "geoserver" / "styles"


def create_styles(base_url: str, username: str, password: str) -> None:
    """Idempotently upload repo SLD styles into their workspace.

    `upload_style` only writes the SLD body when the style entry is newly created, so we
    delete any existing entry first, then POST the SLD content (which creates entry + body).
    """
    auth = HTTPBasicAuth(username, password)
    for ws, (name, filename) in STYLES.items():
        sld_path = _STYLES_DIR / filename
        if not sld_path.exists():
            print(f"  WARNING: SLD not found: {sld_path}")
            continue
        requests.delete(
            f"{base_url}/rest/workspaces/{ws}/styles/{name}?purge=true&recurse=true",
            auth=auth, timeout=15,
        )
        r = requests.post(
            f"{base_url}/rest/workspaces/{ws}/styles?name={name}",
            auth=auth,
            headers={"Content-Type": "application/vnd.ogc.sld+xml"},
            data=sld_path.read_bytes(),
            timeout=20,
        )
        print(f"  style '{ws}:{name}': {'OK' if r.status_code in (200, 201) else f'HTTP {r.status_code}'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create CoRE Stack GeoServer workspaces.")
    parser.add_argument("--url", default="http://localhost:8080/geoserver")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="geoserver")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    print(f"Connecting to GeoServer at {base_url} ...")

    try:
        r = requests.get(f"{base_url}/rest/about/version.json",
                         auth=HTTPBasicAuth(args.username, args.password), timeout=10)
        if r.status_code != 200:
            print(f"ERROR: GeoServer not reachable (HTTP {r.status_code}). Is it running?")
            return 1
    except requests.exceptions.ConnectionError:
        print("ERROR: Could not connect to GeoServer. Is the Docker container running?")
        return 1

    create_workspaces(base_url, args.username, args.password)
    create_styles(base_url, args.username, args.password)
    return 0


if __name__ == "__main__":
    sys.exit(main())

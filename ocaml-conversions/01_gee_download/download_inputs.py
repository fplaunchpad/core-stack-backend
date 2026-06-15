#!/usr/bin/env python
"""
① Download per-block inputs from GEE for the chosen location.

GEE is used ONLY as a data tap (project rule 2): we pull the two FeatureCollections that
drainage-density needs — the MWS polygons and the drainage lines — for one block, and
write them as GeoJSON. No computation happens here.

Run inside the project env (corestackenv) so Django settings + GEE creds resolve:

    python 01_gee_download/download_inputs.py --config config/location.json

Outputs:
    data/inputs/<state>_<district>_<block>/mws.geojson
    data/inputs/<state>_<district>_<block>/drainage_lines.geojson
"""
import argparse
import json
import os
import sys

# --- make the Django project importable so we reuse the REAL asset-path convention ---
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", ".."))
sys.path.insert(0, REPO_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nrm_app.settings")

import django  # noqa: E402

django.setup()

import ee  # noqa: E402
from utilities.gee_utils import (  # noqa: E402
    ee_initialize,
    get_gee_asset_path,
    valid_gee_text,
)


def loc_slug(cfg):
    return "_".join(
        valid_gee_text(str(cfg[k]).lower().replace(" ", "_"))
        for k in ("state", "district", "block")
    )


def fetch_fc(asset_id):
    """getInfo() a FeatureCollection → GeoJSON dict."""
    fc = ee.FeatureCollection(asset_id).getInfo()
    if isinstance(fc, str):
        fc = json.loads(fc)
    return fc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg = json.load(open(args.config))
    state, district, block = cfg["state"], cfg["district"], cfg["block"]
    ee_initialize(cfg.get("gee_account_id", 1))

    base = get_gee_asset_path(state, district, block)
    d, b = valid_gee_text(district.lower()), valid_gee_text(block.lower())

    mws_asset = f"{base}filtered_mws_{d}_{b}_uid"
    lines_asset = f"{base}drainage_lines_{d}_{b}"

    out_dir = os.path.join(REPO_ROOT, "ocaml-conversions", "data", "inputs", loc_slug(cfg))
    os.makedirs(out_dir, exist_ok=True)

    print(f"↓ MWS         {mws_asset}")
    mws = fetch_fc(mws_asset)
    json.dump(mws, open(os.path.join(out_dir, "mws.geojson"), "w"))

    # Drainage lines: prefer the precomputed per-block asset; if it doesn't exist, clip the
    # pan-India dataset to the MWS directly (the pipeline does exactly filterBounds + no
    # transform, and ORDER comes from the source) — rule 2: download only what's needed,
    # no asset/DB/GeoServer side effects.
    try:
        print(f"↓ drainage    {lines_asset}")
        lines = fetch_fc(lines_asset)
    except Exception as e:
        print(f"  precomputed asset not found ({type(e).__name__}); clipping pan-India dataset directly")
        from utilities.constants import PAN_INDIA_DRAINAGE_LINES_DATASET

        mws_fc = ee.FeatureCollection(mws_asset)
        lines = (
            ee.FeatureCollection(PAN_INDIA_DRAINAGE_LINES_DATASET)
            .filterBounds(mws_fc.geometry())
            .getInfo()
        )
    json.dump(lines, open(os.path.join(out_dir, "drainage_lines.geojson"), "w"))

    print(
        f"✓ wrote {len(mws.get('features', []))} MWS + "
        f"{len(lines.get('features', []))} drainage lines → {out_dir}"
    )


if __name__ == "__main__":
    main()

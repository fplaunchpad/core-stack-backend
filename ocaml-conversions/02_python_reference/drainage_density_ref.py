#!/usr/bin/env python
"""
② Python REFERENCE for drainage density — the golden output the OCaml port must match.

This is `computing/clart/drainage_density.py::generate_vector` with the GEE/Django coupling
removed: it reads the GeoJSON inputs downloaded by step ① and reproduces the exact math
(see ../../myref/formuale.md §4). Pure geopandas/shapely — no `ee`, no Django.

    python 02_python_reference/drainage_density_ref.py --config config/location.json

Output:
    data/outputs/python/<loc>/dd.geojson   (EPSG:4326, with DD, DD_stream, str_len_km)
"""
import argparse
import json
import os

import geopandas as gpd

# Influence factors for stream orders 1..11 — verbatim from generate_vector()
INFLUENCE_FACTORS = [
    60 / 385, 55 / 385, 50 / 385, 45 / 385, 40 / 385, 35 / 385,
    30 / 385, 25 / 385, 20 / 385, 15 / 385, 10 / 385,
]
CRS_4326 = "EPSG:4326"
CRS_METRIC = 7755  # India projected CRS (metres) — used for length


def loc_slug(cfg):
    return "_".join(
        str(cfg[k]).lower().replace(" ", "_") for k in ("state", "district", "block")
    )


def compute_drainage_density(mws_geojson, lines_geojson):
    watersheds = gpd.GeoDataFrame.from_features(mws_geojson).set_crs(CRS_4326)
    drainage_lines = gpd.GeoDataFrame.from_features(lines_geojson).set_crs(CRS_4326)

    # reproject to metric CRS for length (degrees are not metres)
    drainage_lines = drainage_lines.to_crs(crs=CRS_METRIC)
    watersheds = watersheds.to_crs(crs=CRS_METRIC)
    watersheds["DD"] = None
    watersheds["DD_stream"] = None
    watersheds["str_len_km"] = None

    for index, watershed in watersheds.iterrows():
        clipped = gpd.clip(drainage_lines, watershed.geometry)
        area = watershed["area_in_ha"] / 100  # ha → (used as /100 per source)

        stream_length = {}
        stream_dd = {}
        for order, factor in zip(range(1, 12), INFLUENCE_FACTORS):
            order_lines = clipped[clipped["ORDER"] == order]
            total_len_km = order_lines.geometry.length.sum() / 1000
            dd = total_len_km * factor * 100 / area
            stream_length[order] = total_len_km
            stream_dd[order] = dd

        watersheds.at[index, "DD"] = sum(stream_dd.values())
        # ordered lists [order 1..11] for stable comparison
        watersheds.at[index, "DD_stream"] = [stream_dd[o] for o in range(1, 12)]
        watersheds.at[index, "str_len_km"] = [stream_length[o] for o in range(1, 12)]

    watersheds = watersheds.to_crs(crs=CRS_4326)
    watersheds["DD"] = watersheds["DD"].astype(float)
    return watersheds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg = json.load(open(args.config))
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.abspath(os.path.join(here, ".."))
    slug = loc_slug(cfg)

    in_dir = os.path.join(base, "data", "inputs", slug)
    mws = json.load(open(os.path.join(in_dir, "mws.geojson")))
    lines = json.load(open(os.path.join(in_dir, "drainage_lines.geojson")))

    result = compute_drainage_density(mws, lines)

    out_dir = os.path.join(base, "data", "outputs", "python", slug)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "dd.geojson")

    # Write GeoJSON manually: properties natively support arrays (DD_stream, str_len_km),
    # which fiona/OGR (.to_file) rejects. numpy scalars coerced via .item().
    from shapely.geometry import mapping

    def jsonable(o):
        return o.item() if hasattr(o, "item") else str(o)

    feats = []
    for _, r in result.iterrows():
        props = {k: r[k] for k in result.columns if k != "geometry"}
        feats.append(
            {"type": "Feature", "properties": props, "geometry": mapping(r.geometry)}
        )
    fc = {"type": "FeatureCollection", "features": feats}
    json.dump(fc, open(out_path, "w"), default=jsonable)
    print(f"✓ {len(result)} watersheds → {out_path}")


if __name__ == "__main__":
    main()

"""
Level A — per-MWS species richness (a snapshot).

"How species-rich is this area?" For each micro-watershed (MWS) polygon we compute distinct-species
richness by a POINT-IN-POLYGON join of GBIF occurrences. Richness is computed from the points, never
by averaging a raster (averaging distinct-species counts is meaningless — see README.md §2).

Sampling effort (`occurrence_count`) is always carried next to richness, and under-surveyed MWS are
flagged `data_poor` rather than reported as "0 species" (README.md §3).
"""

import numpy as np
import pandas as pd
import geopandas as gpd

import ee
from utilities.gee_utils import get_gee_asset_path, valid_gee_text

from . import config


def _shannon(counts):
    counts = np.asarray(counts, dtype=float)
    if counts.sum() == 0:
        return 0.0
    p = counts / counts.sum()
    p = p[p > 0]
    return float(-(p * np.log(p)).sum())


def load_mws_polygons(state, district, block):
    """
    Fetch the block's MWS polygons from the GEE asset (same asset change_detection uses) as a
    GeoDataFrame with a 'uid' column. Requires ee_initialize() to have been called by the caller.
    """
    fc = ee.FeatureCollection(
        get_gee_asset_path(state, district, block)
        + "filtered_mws_"
        + valid_gee_text(district.lower())
        + "_"
        + valid_gee_text(block.lower())
        + "_uid"
    )
    geojson = fc.getInfo()
    mws = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    if "uid" not in mws.columns:
        raise KeyError("MWS FeatureCollection has no 'uid' property")
    return mws[["uid", "geometry"]]


def occurrences_to_points(df):
    """Occurrence DataFrame -> GeoDataFrame of points in EPSG:4326."""
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df.decimalLongitude, df.decimalLatitude),
        crs="EPSG:4326",
    )


def aggregate_per_mws(points_gdf, mws_gdf, keep_species_list=True):
    """
    Point-in-polygon join + per-MWS aggregation. Returns a GeoDataFrame keyed on uid with
    species_richness, occurrence_count, shannon_diversity_index, dominant_taxon_group,
    (optional) species_list, and the data_poor flag. MWS with zero records are kept.
    """
    joined = gpd.sjoin(
        points_gdf, mws_gdf, predicate="within", how="inner"
    )

    rows = []
    for uid, g in joined.groupby("uid"):
        counts = g["taxonKey"].value_counts()
        row = {
            "uid": uid,
            "species_richness": int(g["taxonKey"].nunique()),
            "occurrence_count": int(len(g)),
            "shannon_diversity_index": round(_shannon(counts.values), 3),
            "dominant_taxon_group": (
                g["class"].mode().iat[0]
                if "class" in g and not g["class"].mode().empty
                else "NA"
            ),
        }
        if keep_species_list:
            row["species_list"] = ", ".join(sorted(g["species"].dropna().unique()))
        rows.append(row)

    stats = pd.DataFrame(rows)
    out = mws_gdf.merge(stats, on="uid", how="left")
    # MWS with no records: keep them, mark data-poor (never silently drop -> README §3)
    for col in ("species_richness", "occurrence_count", "shannon_diversity_index"):
        if col in out:
            out[col] = out[col].fillna(0)
    out["data_poor"] = out["occurrence_count"] < config.MIN_RECORDS
    return out


def mws_species_richness(clean_df, state, district, block):
    """End-to-end Level A for one block. `clean_df` is the cleaned occurrences DataFrame."""
    pts = occurrences_to_points(clean_df)
    mws = load_mws_polygons(state, district, block)
    return aggregate_per_mws(pts, mws)


# ---------------------------------------------------------------------------------------------
# Optional: coarse snapshot rasters (richness + effort). Deliberately coarse — point data is sparse.
# ---------------------------------------------------------------------------------------------
def build_richness_raster(clean_df, out_tif, effort_tif, res_deg=None, bounds=None):
    """
    Grid distinct-species richness (and, separately, occurrence count = effort) to two GeoTIFFs.
    Coarse resolution by design; a 10 m grid would be ~99.9% empty for point data.
    """
    import rasterio
    from rasterio.transform import from_origin

    res_deg = res_deg or config.RICHNESS_GRID_DEG
    minlon, minlat, maxlon, maxlat = bounds or config.INDIA_BBOX
    ncols = int(np.ceil((maxlon - minlon) / res_deg))
    nrows = int(np.ceil((maxlat - minlat) / res_deg))

    df = clean_df.copy()
    df["col"] = ((df.decimalLongitude - minlon) / res_deg).astype(int)
    df["row"] = ((maxlat - df.decimalLatitude) / res_deg).astype(int)  # row 0 at top

    richness = df.groupby(["row", "col"])["taxonKey"].nunique()
    effort = df.groupby(["row", "col"]).size()

    grid_r = np.zeros((nrows, ncols), dtype="int32")
    grid_e = np.zeros((nrows, ncols), dtype="int32")
    for (r, c), v in richness.items():
        grid_r[r, c] = v
    for (r, c), v in effort.items():
        grid_e[r, c] = v

    transform = from_origin(minlon, maxlat, res_deg, res_deg)
    profile = dict(
        driver="GTiff", height=nrows, width=ncols, count=1, dtype="int32",
        crs="EPSG:4326", transform=transform, nodata=0,
    )
    for path, grid in ((out_tif, grid_r), (effort_tif, grid_e)):
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(grid, 1)
    return out_tif, effort_tif

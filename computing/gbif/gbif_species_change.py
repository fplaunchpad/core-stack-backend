"""
Level B — species change over time (effort-normalized).

Built entirely on Level A. GBIF uploads grow over time, so a RAW richness diff rises almost
everywhere — that is the upload curve, not ecology. Presence-only data also cannot prove
disappearance. So this module:

  1. runs the Level-A per-MWS aggregation for a THEN window and a NOW window,
  2. RAREFIES both to the same record count per MWS (richness at equal effort),
  3. classifies each MWS as richness_gain / richness_loss / stable / data_poor,

and always carries effort (occurrence_count) for both windows next to the change.
See PLAN_B_IMPLEMENTATION.md §5 and SPECIES_CHANGE_DETECTION_FEASIBILITY.md §4.
"""

import numpy as np
import pandas as pd

from . import config
from .gbif_richness import occurrences_to_points, load_mws_polygons

import geopandas as gpd


def _rarefy_richness(taxon_keys, sample_size, n_iter=None, rng=None):
    """
    Expected number of distinct species when drawing `sample_size` records WITHOUT replacement,
    averaged over n_iter random draws. This is Monte-Carlo rarefaction (equal-effort richness).
    """
    keys = np.asarray(taxon_keys)
    if sample_size <= 0 or len(keys) == 0:
        return 0.0
    if sample_size >= len(keys):
        return float(pd.unique(keys).size)  # can't rarefy below the sample -> observed richness

    n_iter = n_iter or config.RAREFACTION_ITERS
    rng = rng or np.random.default_rng(12345)  # fixed seed -> reproducible layers
    counts = [
        np.unique(rng.choice(keys, size=sample_size, replace=False)).size
        for _ in range(n_iter)
    ]
    return float(np.mean(counts))


def _classify(delta, effort_then, effort_now):
    if effort_then < config.MIN_RECORDS_PER_WINDOW or effort_now < config.MIN_RECORDS_PER_WINDOW:
        return "data_poor"
    if delta > 0.5:
        return "richness_gain"
    if delta < -0.5:
        return "richness_loss"
    return "stable"


def _window_join(clean_df, mws_gdf, years):
    """Filter occurrences to `years`, join to MWS, return the joined GeoDataFrame."""
    sub = clean_df[clean_df["year"].isin(years)]
    pts = occurrences_to_points(sub)
    return gpd.sjoin(pts, mws_gdf, predicate="within", how="inner")


def mws_species_change(clean_df, state, district, block, then_years, now_years):
    """
    Per-MWS rarefied richness change between two windows. Returns a GeoDataFrame keyed on uid:
        richness_then, richness_now, rarefied_then, rarefied_now, delta_richness,
        effort_then, effort_now, change_class, data_poor
    """
    if "year" not in clean_df.columns:
        raise KeyError("cleaned occurrences need a 'year' column for Level B")

    mws = load_mws_polygons(state, district, block)
    j_then = _window_join(clean_df, mws, then_years)
    j_now = _window_join(clean_df, mws, now_years)

    then_groups = dict(tuple(j_then.groupby("uid")))
    now_groups = dict(tuple(j_now.groupby("uid")))

    rows = []
    for uid in mws["uid"]:
        gt = then_groups.get(uid)
        gn = now_groups.get(uid)
        keys_t = gt["taxonKey"].values if gt is not None else np.array([])
        keys_n = gn["taxonKey"].values if gn is not None else np.array([])
        effort_t, effort_n = len(keys_t), len(keys_n)

        # rarefy BOTH windows down to the smaller effort so richness is comparable
        sample = min(effort_t, effort_n)
        rare_t = _rarefy_richness(keys_t, sample)
        rare_n = _rarefy_richness(keys_n, sample)
        delta = round(rare_n - rare_t, 3)

        rows.append({
            "uid": uid,
            "richness_then": int(pd.unique(keys_t).size),
            "richness_now": int(pd.unique(keys_n).size),
            "rarefied_then": round(rare_t, 3),
            "rarefied_now": round(rare_n, 3),
            "delta_richness": delta,
            "effort_then": effort_t,
            "effort_now": effort_n,
            "change_class": _classify(delta, effort_t, effort_n),
        })

    change = pd.DataFrame(rows)
    out = mws.merge(change, on="uid", how="left")
    out["data_poor"] = out["change_class"] == "data_poor"
    return out


def split_window(start_year, end_year):
    """
    Default THEN/NOW split, mirroring the LULC change-detection convention (early years = then,
    later years = now). Splits the [start..end] range in half.
    """
    years = list(range(int(start_year), int(end_year) + 1))
    mid = len(years) // 2
    then_years = years[:mid] or years[:1]
    now_years = years[mid:] or years[-1:]
    return then_years, now_years

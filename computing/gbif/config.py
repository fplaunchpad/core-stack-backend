"""
Tunables for the GBIF species pipeline (Plan B).

Everything that a scientist/reviewer might want to adjust lives here so the analysis code stays
declarative. See PLAN_B_IMPLEMENTATION.md for how these are used.
"""

import os

# --- GBIF credentials (Download API needs a registered account) --------------------------------
# Set these in the environment / .env, mirroring how other secrets are handled in the repo.
GBIF_USER = os.environ.get("GBIF_USER")
GBIF_PWD = os.environ.get("GBIF_PWD")
GBIF_EMAIL = os.environ.get("GBIF_EMAIL")

# --- Where cached downloads live (keyed by taxon + window; GBIF data is large & slow) ----------
CACHE_DIR = os.environ.get(
    "GBIF_CACHE_DIR", os.path.join(os.path.dirname(__file__), "_cache")
)

# --- India bounding box (minlon, minlat, maxlon, maxlat) — coarse sanity clip -------------------
INDIA_BBOX = (68.0, 6.5, 97.5, 37.6)

# --- Cleaning thresholds ------------------------------------------------------------------------
MAX_COORD_UNCERTAINTY_M = 10000  # drop points whose stated uncertainty is worse than ~10 km
CENTROID_DUP_THRESHOLD = 1000    # >N identical coords = likely a country/province centroid dump

# --- Richness / data-poor thresholds ------------------------------------------------------------
# MWS with fewer than this many records are flagged data_poor (cannot assess), never "0 species".
MIN_RECORDS = 20

# --- Snapshot raster resolution (degrees). Deliberately COARSE — point data is sparse. ---------
RICHNESS_GRID_DEG = 0.05  # ~5.5 km cells

# --- Level B (change over time) -----------------------------------------------------------------
# Number of random draws used to estimate rarefied (effort-normalized) richness.
RAREFACTION_ITERS = 100
# A cell/MWS must have at least this many records in BOTH windows to be assessed for change.
MIN_RECORDS_PER_WINDOW = 15

# --- basisOfRecord values we keep (real observations/specimens) ---------------------------------
KEEP_BASIS_OF_RECORD = [
    "HUMAN_OBSERVATION",
    "PRESERVED_SPECIMEN",
    "MACHINE_OBSERVATION",
    "OBSERVATION",
]

# --- GeoServer workspace for species layers -----------------------------------------------------
WORKSPACE = "biodiversity"

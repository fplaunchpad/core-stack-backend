# exploGBIF Biodiversity Integration — Implementation Guide

> **What this is:** a step-by-step plan to ingest **GBIF biodiversity occurrence data** end-to-end
> into the CoRE Stack: from a pan-India raster layer, to per-micro-watershed (MWS) statistics,
> to KYL filters, to new sections in the MWS and tehsil reports.
>
> **Who this is for:** a developer who has never touched GBIF. Read it top to bottom. Each step says
> **what** you are doing, **why**, and **how** (with code you can copy).

---

## Table of contents

1. [The big picture (read this first)](#1-the-big-picture-read-this-first)
2. [How GBIF differs from every other layer in this repo](#2-how-gbif-differs-from-every-other-layer-in-this-repo)
3. [⚠️ The one mistake that will ruin the results: sampling bias](#3-️-the-one-mistake-that-will-ruin-the-results-sampling-bias)
4. [Prerequisites](#4-prerequisites)
5. [Folder layout we will build](#5-folder-layout-we-will-build)
6. [Phase 0 — Registry &amp; config](#phase-0--registry--config)
7. [Phase 1 — Download GBIF occurrences for India](#phase-1--download-gbif-occurrences-for-india)
8. [Phase 2 — Clean the occurrences](#phase-2--clean-the-occurrences)
9. [Phase 3 — Build the pan-India raster layer](#phase-3--build-the-pan-india-raster-layer)
10. [Phase 4 — Per-MWS biodiversity statistics (vectorization)](#phase-4--per-mws-biodiversity-statistics-vectorization)
11. [Phase 5 — Excel sheet integration](#phase-5--excel-sheet-integration)
12. [Phase 6 — KYL filters](#phase-6--kyl-filters)
13. [Phase 7 — MWS &amp; tehsil report sections](#phase-7--mws--tehsil-report-sections)
14. [The Celery task that ties it together](#the-celery-task-that-ties-it-together)
15. [Testing &amp; validation](#testing--validation)
16. [Touchpoint checklist](#touchpoint-checklist)
17. [Glossary](#glossary)

---

## 1. The big picture (read this first)

GBIF (the **Global Biodiversity Information Facility**) is a free database of **species occurrence
records**. One record = "this species was seen/collected at this latitude/longitude on this date."
For India there are **tens of millions** of such records (birds, plants, insects, mammals…).

We want to turn those scattered points into the two things the CoRE Stack speaks:

1. **A pan-India raster layer** — a gridded "biodiversity heatmap" (e.g. species richness per grid
   cell) for map visualization. *Required by the brief.*
2. **Per-MWS statistics** — for each micro-watershed: how many distinct species, how many records,
   how diverse — so it can flow into KYL filters and the reports.

Here is the whole pipeline at a glance. The **green** parts are new and GBIF-specific; the **grey**
parts are the standard CoRE Stack chain that every layer already uses.

```
   ┌─────────────────────────── NEW (GBIF-specific, this folder) ───────────────────────────┐
   │                                                                                          │
   │  GBIF Download API           Clean & filter            Aggregate                         │
   │  (country = IN)      ──►      (drop bad coords,  ──►   ┌── grid cells ──► RASTER  ───┐    │
   │  occurrence records           dedupe, species)        │                              │    │
   │  [points: lat/lon,                                    └── MWS polygons ─► per-MWS ───┤    │
   │   species, date]                                          (point-in-polygon)    table│    │
   └───────────────────────────────────────────────────────────────────────────────────┼────┘
                                                                                         │
   ┌──────────────────── STANDARD CoRE Stack chain (same as every layer) ───────────────▼────┐
   │  RASTER ─► GCS ─► GEE asset / GeoServer raster          (map tiles)                      │
   │  per-MWS table ─► GeoServer vector layer ─► save_layer_info_to_db (Dataset/Layer)        │
   │            ─► stats_generator Excel sheet ─► mws_indicators.py (KYL keys)                 │
   │            ─► public_api KYL JSON  +  dpr report sections (mws-report / block-report)     │
   └──────────────────────────────────────────────────────────────────────────────────────┘
```

**Key idea:** once we produce (a) a raster GeoTIFF and (b) a per-MWS GeoJSON, the rest of the
pipeline (Excel → KYL → reports) is **identical** to every other layer. So 80% of the novel work
is in Phases 1–4. Phases 5–7 are the well-trodden path.

---

## 2. How GBIF differs from every other layer in this repo

Every existing layer (LULC, terrain, change-detection, ERA5 climate…) starts from a **raster that
already lives in Google Earth Engine** and uses `reduceRegions()` to summarize it per MWS.

**GBIF has no raster and is not in GEE.** It is *point* data fetched over HTTP. That changes two things:

|                        | Standard layer (e.g. LULC, ERA5) | GBIF                                                                                                                                             |
| ---------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Source                 | GEE`ImageCollection`           | GBIF REST Download API                                                                                                                           |
| Native form            | Raster pixels                    | Points (lat/lon + species)                                                                                                                       |
| Per-MWS method         | `reduceRegions()` in GEE       | **point-in-polygon spatial join in Python (geopandas)**                                                                                    |
| Why not reduceRegions? | —                               | Species**richness = count of *distinct* species**. You cannot get that by averaging a raster. A spatial join preserves species identity. |
| Main tooling           | `ee.*`                         | `pygbif`, `pandas`, `geopandas`, `rasterio`                                                                                              |

So the GBIF module is mostly a **Python ETL job**, not a GEE script. That is expected and fine — the
repo already has the helper functions we need to push the *results* into GEE/GeoServer
(see [`utilities/gee_utils.py`](../../utilities/gee_utils.py): `gdf_to_ee_fc`, `upload_tif_to_gcs`,
`gcs_to_gee_asset_cli`, `sync_raster_gcs_to_geoserver`, and `sync_layer_to_geoserver` in
[`computing/utils.py`](../utils.py)).

---

## 3. ⚠️ The one mistake that will ruin the results: sampling bias

**Read this before writing any analysis code.** GBIF data is *opportunistic*, not a systematic
survey. A grid cell near Bangalore has thousands of bird records because many birdwatchers live
there; a remote cell in Chhattisgarh may have zero records — **not because it has no species, but
because nobody uploaded observations there.**

Therefore:

- **Raw species richness is confounded by sampling effort.** A cell with more *records* will almost
  always show more *species*. Never present richness without also presenting effort.
- **Always compute and store `occurrence_count` (number of records) next to `species_richness`.**
  This is the sampling-effort indicator. The frontend/report must show both so users can judge
  whether "low richness" means "low biodiversity" or "under-surveyed."
- **Flag data-poor MWS explicitly** (e.g. `< 20 records`) rather than reporting a misleading "0
  species." In the tehsil report, a low-data MWS is a *"needs field survey"* finding, not a
  *"low biodiversity"* finding.
- Coordinate errors **inflate** richness in species-poor regions — hence Phase 2 cleaning is not
  optional. (See [CoordinateCleaner](https://ropensci.github.io/CoordinateCleaner/): "Geographic
  inaccuracy affects diversity patterns more than taxonomic uncertainties… overestimating species
  richness in relatively species-poor regions.")

If you remember one thing from this document: **richness and effort travel together, always.**

---

## 4. Prerequisites

1. **A free GBIF account** — register at [https://www.gbif.org/user/profile](https://www.gbif.org/user/profile). You need a username
   and password to use the **Download API** (the only way to pull large/full datasets). Set them as
   environment variables (add to `.env`, mirror how other secrets are handled):
   ```bash
   GBIF_USER="your_gbif_username"
   GBIF_PWD="your_gbif_password"
   GBIF_EMAIL="you@org.org"     # GBIF emails you when a download is ready
   ```
2. **Python libraries** (add to `requirements.txt`):
   ```
   pygbif        # GBIF API client
   geopandas     # spatial joins (points-in-polygons)
   rasterio      # rasterize the grid to GeoTIFF
   shapely       # geometry
   ```

   `pandas`, `numpy` are already present.
3. **Existing repo helpers you will call** (no need to reimplement):
   - [`utilities/gee_utils.py`](../../utilities/gee_utils.py): `ee_initialize`, `gdf_to_ee_fc`,
     `upload_tif_to_gcs`, `gcs_to_gee_asset_cli`, `sync_raster_gcs_to_geoserver`,
     `get_gee_asset_path`, `make_asset_public`.
   - [`computing/utils.py`](../utils.py): `sync_layer_to_geoserver`, `save_layer_info_to_db`,
     `update_layer_sync_status`.
   - The MWS units live in GEE at
     `…/<state>/<district>/<block>/filtered_mws_<district>_<block>_uid` and are also served from
     GeoServer (workspace `mws_layers`). Each MWS polygon has a unique `uid` — **this `uid` is the
     join key for everything downstream.**

---

## 5. Folder layout we will build

```
computing/gbif/
  README.md                  ← this file (the plan)
  __init__.py
  gbif_download.py           # Phase 1: pull occurrences from GBIF
  gbif_clean.py              # Phase 2: clean / filter occurrences
  gbif_raster.py             # Phase 3: grid → richness GeoTIFF → GCS → GEE/GeoServer
  gbif_vector.py             # Phase 4: point-in-polygon → per-MWS stats → GeoServer
  gbif_indicators.py         # Phase 6 helpers: compute KYL keys from the per-MWS sheet
  biodiversity.py            # the Celery task that orchestrates Phases 1–4 + DB save
  config.py                  # tunables: grid resolution, min-records threshold, taxon groups
```

Files **outside** this folder you will edit (Phases 0, 5, 6, 7) are listed in the
[touchpoint checklist](#touchpoint-checklist).

---

## Phase 0 — Registry & config

**Why:** `save_layer_info_to_db()` does `Dataset.objects.get(name=...)` and will crash if the
dataset row is missing. Layers must also be registered for GeoServer/STAC. Do this first.

**How:**

1. **Seed `Dataset` rows** in [`installation/seed/seed_data.json`](../../installation/seed/seed_data.json)
   (it already contains rows like `LULC`, `Hydrology Precipitation`). Add:
   - `Biodiversity Occurrence` — `layer_type: vector`, `workspace: biodiversity`,
     `style_name: biodiversity`.
   - `Biodiversity Richness Raster` — `layer_type: raster`, `workspace: biodiversity`,
     `style_name: richness`.
     Load: `python manage.py loaddata installation/seed/seed_data.json` (or create via Django admin
     in dev).
2. **GeoServer:** create workspace `biodiversity`; add SLD styles under
   [`installation/geoserver/styles/`](../../installation/geoserver/styles/) — a green→red ramp for
   the richness raster (`richness.sld`) and a graduated polygon style for the MWS vector.
3. **STAC registry:** add rows to
   [`data/STAC_specs/input/metadata/layer_mapping.csv`](../../data/STAC_specs/input/metadata/layer_mapping.csv)
   then run `python manage.py load_layer_mappings`. Set `ee_layer_name=GBIF/occurrence`,
   `theme=Biodiversity`, `spatial_resolution_in_meters=` your grid size (Phase 3).

**Done when:** the `Dataset` rows exist and the `biodiversity` GeoServer workspace is created.

---

## Phase 1 — Download GBIF occurrences for India

**What:** pull every (cleanable) occurrence record for India into a local CSV.

**Why the Download API and not the search API:** the search endpoint caps at 100k records; India has
far more. The **Download API** is asynchronous (you request → GBIF prepares a zip → you fetch it) and
has no record cap. Use `SIMPLE_CSV` format (a single tab-delimited file with the columns we need).

**How** — `computing/gbif/gbif_download.py`:

```python
import os, time, zipfile
from pygbif import occurrences as occ

# Predicate keys for the DOWNLOAD API must be UPPER_CASE_WITH_UNDERSCORES.
def request_india_download():
    """Ask GBIF to prepare a download of Indian occurrences. Returns a download key."""
    download_key = occ.download(
        queries=[
            "COUNTRY = IN",
            "HAS_COORDINATE = TRUE",          # we need lat/lon
            "HAS_GEOSPATIAL_ISSUE = FALSE",   # GBIF's own coarse geo filter
            "OCCURRENCE_STATUS = PRESENT",
            # keep records that represent real observations/specimens:
            "BASIS_OF_RECORD in [HUMAN_OBSERVATION, PRESERVED_SPECIMEN, MACHINE_OBSERVATION, OBSERVATION]",
        ],
        format="SIMPLE_CSV",
        user=os.environ["GBIF_USER"],
        pwd=os.environ["GBIF_PWD"],
        email=os.environ["GBIF_EMAIL"],
    )
    return download_key[0] if isinstance(download_key, (list, tuple)) else download_key


def wait_and_fetch(download_key, dest_dir):
    """Poll until GBIF finishes preparing the file, then download & unzip it."""
    while True:
        meta = occ.download_meta(download_key)
        status = meta["status"]                 # PREPARING / RUNNING / SUCCEEDED / KILLED
        if status == "SUCCEEDED":
            break
        if status in ("KILLED", "CANCELLED", "FAILED"):
            raise RuntimeError(f"GBIF download {download_key} ended as {status}")
        time.sleep(60)                          # be polite; large downloads take minutes–hours

    occ.download_get(download_key, path=dest_dir)          # downloads <key>.zip
    zip_path = os.path.join(dest_dir, f"{download_key}.zip")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(os.path.join(dest_dir, download_key))
    # SIMPLE_CSV extracts to <key>.csv (tab-separated)
    return os.path.join(dest_dir, download_key, f"{download_key}.csv")
```

**Notes & gotchas:**

- The download is **slow and large** (GBs). Run it as a one-off / scheduled job, **not** per
  block-request. Cache the CSV (and the `download_key`, which is a citable DOI) under a data dir.
- GBIF requires you to **cite the download DOI** (`meta["doi"]`). Store it; surface it in the report
  footer ("Biodiversity data: GBIF.org, `<DOI>`, accessed `<date>`").
- Columns we will use from `SIMPLE_CSV`: `gbifID`, `species`, `taxonKey`, `kingdom`, `class`,
  `decimalLatitude`, `decimalLongitude`, `coordinateUncertaintyInMeters`, `countryCode`,
  `basisOfRecord`, `eventDate`, `iucnRedListCategory` (when present).

**Done when:** you have a local `occurrences_india.csv`.

---

## Phase 2 — Clean the occurrences

**What:** remove records whose coordinates are wrong/untrustworthy. **Why:** see
[Section 3](#3-️-the-one-mistake-that-will-ruin-the-results-sampling-bias) — dirty coordinates
inflate richness. We replicate the core ideas of *CoordinateCleaner* with plain pandas filters
(dependency-light and explicit).

**How** — `computing/gbif/gbif_clean.py`:

```python
import pandas as pd

def clean_occurrences(csv_path, out_path):
    df = pd.read_csv(csv_path, sep="\t", on_bad_lines="skip",
                     usecols=["gbifID", "species", "taxonKey", "kingdom", "class",
                              "decimalLatitude", "decimalLongitude",
                              "coordinateUncertaintyInMeters", "basisOfRecord",
                              "iucnRedListCategory"])

    # 1. must have a real species and coordinates
    df = df.dropna(subset=["species", "taxonKey", "decimalLatitude", "decimalLongitude"])

    # 2. drop (0,0) and out-of-India-bbox points (cheap sanity box; refine with a polygon if needed)
    df = df[(df.decimalLatitude.between(6.5, 37.6)) & (df.decimalLongitude.between(68.0, 97.5))]
    df = df[~((df.decimalLatitude == 0) & (df.decimalLongitude == 0))]

    # 3. drop imprecise coordinates (> 10 km uncertainty is useless at MWS scale)
    unc = df["coordinateUncertaintyInMeters"]
    df = df[unc.isna() | (unc <= 10000)]

    # 4. drop country/province centroids (a giant pile of records on one exact coordinate is a red flag)
    dup_coord_counts = df.groupby(["decimalLatitude", "decimalLongitude"]).size()
    suspicious = dup_coord_counts[dup_coord_counts > 1000].index
    df = df[~df.set_index(["decimalLatitude", "decimalLongitude"]).index.isin(suspicious)]

    # 5. de-duplicate identical species-at-coordinate records
    df = df.drop_duplicates(subset=["species", "decimalLatitude", "decimalLongitude"])

    df.to_csv(out_path, index=False)
    return out_path
```

> **Optional upgrade:** the R package *CoordinateCleaner* also flags points landing on biodiversity
> institutions (zoos/herbaria) and in the sea. If you need publication-grade cleaning, run the R tool
> once offline, or port its gazetteer checks. For a first integration the filters above are enough.

**Done when:** you have `occurrences_india_clean.csv` and have logged how many records were dropped
(report this — it is part of data provenance).

---

## Phase 3 — Build the pan-India raster layer

**What:** lay a regular grid over India, count distinct species per cell, write a GeoTIFF, and push
it to GEE + GeoServer so it can be drawn as a heatmap. **This satisfies the "pan-India raster" brief.**

**Why a grid (not the raw points):** a raster needs uniform cells. We aggregate points → cells.

**Choosing resolution** — set in `config.py`. Default **0.1° (~11 km)** to match the coarse climate
layers and because GBIF sampling is too sparse for fine cells in much of India. Expose it as a config
so it can be refined later.

**How** — `computing/gbif/gbif_raster.py` (concept):

```python
import numpy as np, pandas as pd, rasterio
from rasterio.transform import from_origin

def build_richness_raster(clean_csv, out_tif, res_deg=0.1,
                          bounds=(68.0, 6.5, 97.5, 37.6)):   # minlon,minlat,maxlon,maxlat
    df = pd.read_csv(clean_csv)
    minlon, minlat, maxlon, maxlat = bounds
    ncols = int(np.ceil((maxlon - minlon) / res_deg))
    nrows = int(np.ceil((maxlat - minlat) / res_deg))

    # assign each point to a cell (col, row)
    df["col"] = ((df.decimalLongitude - minlon) / res_deg).astype(int)
    df["row"] = ((maxlat - df.decimalLatitude) / res_deg).astype(int)   # row 0 at top

    # species RICHNESS per cell = number of distinct taxonKey
    richness = (df.groupby(["row", "col"])["taxonKey"].nunique()
                  .rename("richness").reset_index())
    grid = np.zeros((nrows, ncols), dtype="int32")
    grid[richness.row.values, richness.col.values] = richness.richness.values

    transform = from_origin(minlon, maxlat, res_deg, res_deg)
    with rasterio.open(out_tif, "w", driver="GTiff", height=nrows, width=ncols,
                       count=1, dtype="int32", crs="EPSG:4326", transform=transform,
                       nodata=0) as dst:
        dst.write(grid, 1)
    return out_tif
    # Build a SECOND raster the same way for occurrence COUNT (sampling effort) — see Section 3.
```

**Push it into the stack** (reuse existing helpers — no new infra):

```python
from utilities.gee_utils import upload_tif_to_gcs, gcs_to_gee_asset_cli, sync_raster_gcs_to_geoserver

gcs_name = "biodiversity/india_species_richness"
upload_tif_to_gcs(gcs_name, out_tif)                                  # local GeoTIFF -> GCS
gcs_to_gee_asset_cli(f"gs://<bucket>/{gcs_name}.tif",                 # GCS -> GEE asset
                     asset_id=<asset_path>, gee_account_id=...)
sync_raster_gcs_to_geoserver("biodiversity", gcs_name,               # GCS -> GeoServer raster
                             "india_species_richness", "richness")
```

**Done when:** a species-richness GeoTIFF is visible as a GeoServer raster layer, and (optionally) a
GEE asset exists. **Also build the occurrence-count raster** — you need it for the bias caveat.

---

## Phase 4 — Per-MWS biodiversity statistics (vectorization)

**What:** for each MWS polygon, compute `species_richness`, `occurrence_count`,
`shannon_diversity_index`, and a `dominant_taxon_group`. **Why point-in-polygon and not the raster:**
distinct-species counts must be computed from the *points*, not by averaging the richness raster
(averaging would be wrong — see [Section 2](#2-how-gbif-differs-from-every-other-layer-in-this-repo)).

**How** — `computing/gbif/gbif_vector.py`:

```python
import pandas as pd, geopandas as gpd, numpy as np, requests
from shapely.geometry import Point

def _shannon(counts):
    p = counts / counts.sum()
    return float(-(p * np.log(p)).sum())

def mws_biodiversity(clean_csv, state, district, block, geoserver_url):
    # 1. occurrences -> GeoDataFrame of points
    df = pd.read_csv(clean_csv)
    pts = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df.decimalLongitude, df.decimalLatitude),
        crs="EPSG:4326")

    # 2. MWS polygons for this block, fetched from GeoServer WFS (same way stats_generator does it)
    layer = f"mws_layers:filtered_mws_{district}_{block}_uid"   # adjust to actual layer name
    wfs = (f"{geoserver_url}/mws_layers/ows?service=WFS&version=1.0.0&request=GetFeature"
           f"&typeName={layer}&outputFormat=application/json")
    mws = gpd.read_file(requests.get(wfs).text)                # GeoDataFrame with a 'uid' column

    # 3. POINT-IN-POLYGON join: which MWS does each occurrence fall in?
    joined = gpd.sjoin(pts, mws[["uid", "geometry"]], predicate="within", how="inner")

    # 4. aggregate per MWS uid
    rows = []
    for uid, g in joined.groupby("uid"):
        counts = g["taxonKey"].value_counts()
        rows.append({
            "uid": uid,
            "species_richness": int(g["taxonKey"].nunique()),
            "occurrence_count": int(len(g)),                      # sampling effort — ALWAYS keep
            "shannon_diversity_index": round(_shannon(counts.values), 3),
            "dominant_taxon_group": g["class"].mode().iat[0] if not g["class"].mode().empty else "NA",
        })
    stats = pd.DataFrame(rows)

    # 5. MWS with ZERO records: keep them, mark as data-poor (don't silently drop -> see Section 3)
    out = mws[["uid", "geometry"]].merge(stats, on="uid", how="left")
    out[["species_richness", "occurrence_count"]] = out[["species_richness", "occurrence_count"]].fillna(0)
    out["data_poor"] = out["occurrence_count"] < 20             # tune threshold in config.py
    return out                                                   # a GeoDataFrame -> GeoJSON next
```

**Push to GeoServer + register** (standard helpers):

```python
from computing.utils import sync_layer_to_geoserver, save_layer_info_to_db, update_layer_sync_status

fc_geojson = out.to_json()
layer_name = f"{district}_{block}_biodiversity"
res = sync_layer_to_geoserver(state, json.loads(fc_geojson), layer_name, "biodiversity")
layer_id = save_layer_info_to_db(state, district, block, layer_name=layer_name,
                                 asset_id="not available", dataset_name="Biodiversity Occurrence",
                                 algorithm="GBIF", algorithm_version="1.0")
if res.get("status_code") == 201 and layer_id:
    update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
```

**Done when:** a `biodiversity` GeoServer vector layer exists, one row per MWS, keyed on `uid`, with
the four indicators + `data_poor` flag, and a `Layer` row is registered.

---

## Phase 5 — Excel sheet integration

**What:** turn the GeoServer biodiversity layer into an Excel sheet, because KYL and the reports read
**per-block Excel files** (`{district}_{block}.xlsx`), not GeoServer directly.

**How** — in [`stats_generator/utils.py`](../../stats_generator/utils.py), `get_vector_layer_geoserver()`
dispatches by `workspace` (around L88). Add a branch and a helper:

```python
elif workspace == "biodiversity":
    create_excel_for_biodiversity(geojson_data, xlsx_file, writer)
```

`create_excel_for_biodiversity(...)` (model it on the existing `create_excel_for_*` functions):
flatten the GeoJSON `properties` into a DataFrame with columns
`UID, species_richness, occurrence_count, shannon_diversity_index, dominant_taxon_group, data_poor`
and write it as the sheet **`biodiversity`**.

**Done when:** running the "generate stats excel" endpoint adds a `biodiversity` sheet to each block
file.

---

## Phase 6 — KYL filters

**What:** expose the indicators as KYL filters. **Why it's easy:** KYL has no DB model — it reads the
Excel sheets and emits a JSON dict; the frontend discovers keys dynamically.

**How** — in [`stats_generator/mws_indicators.py`](../../stats_generator/mws_indicators.py):

1. Register the sheet in the `sheets` dict (~L69): `"biodiversity": -1,`
2. In the per-MWS loop, read the row for `specific_mws_id` and pull the values (put the small
   extraction helper in `computing/gbif/gbif_indicators.py` and import it, to keep `mws_indicators.py`
   tidy).
3. Add keys to the `results.append({...})` dict (~L984):
   ```python
   "species_richness": species_richness,
   "occurrence_count": occurrence_count,            # sampling effort (show alongside richness!)
   "shannon_diversity_index": shannon_diversity_index,
   "dominant_taxon_group": dominant_taxon_group,    # categorical filter
   "biodiversity_data_poor": data_poor,             # boolean filter: under-surveyed MWS
   ```

Regenerate: `GET /stats_generator/download_kyl_data/?...&regenerate=true`.

**Done when:** the five keys appear in the KYL filter JSON for every MWS.

---

## Phase 7 — MWS & tehsil report sections

Both report generators read the same Excel and inject context into HTML templates.

**MWS report** ([`dpr/gen_mws_report.py`](../../dpr/gen_mws_report.py)):

- Add `get_biodiversity_data(state, district, block, uid)` → returns a narrative string + a small
  dict (richness, Shannon, dominant group, record count, the `data_poor` caveat, GBIF citation DOI).
  Model on `get_soge_data()`.
- Call it in `generate_mws_report()` in [`dpr/api.py`](../../dpr/api.py); add `biodiversity_desc` /
  `biodiversity_data` to the context.
- Add a `{% if biodiversity_desc %}<section>…</section>{% endif %}` block to
  `templates/mws-report.html` (a bar chart of top taxon groups + the effort caveat note).

**Tehsil/block report** ([`dpr/gen_tehsil_report.py`](../../dpr/gen_tehsil_report.py)):

- Add `get_biodiversity_pattern_data(state, district, block)` → per-MWS `mws_pattern` +
  `mws_intensity`. A useful pattern: **conservation priority = high `shannon_diversity_index` AND
  low human pressure**; and separately, **survey gap = `data_poor == True`**. Model on
  `get_agri_water_stress_data()` (indicator-weighting around L1418).
- Call it in `generate_tehsil_report()`; add context keys.
- Add a `<section>` to `templates/block-report.html` with the MWS-intensity choropleth and summary
  ("X of Y MWS are biodiversity-rich; Z MWS are under-surveyed and need field validation").

**Done when:** a "Biodiversity" section renders in both report types, **always showing record count
next to richness.**

---

## The Celery task that ties it together

`computing/gbif/biodiversity.py` — the entry point, following the repo's task pattern
(`@app.task(bind=True)`, `queue="nrm"`, like `get_change_detection`):

```python
from nrm_app.celery import app
from utilities.gee_utils import ee_initialize

@app.task(bind=True)
def generate_biodiversity(self, state, district, block, gee_account_id):
    ee_initialize(gee_account_id)
    clean_csv = <path to cached cleaned national CSV>     # produced by the one-off Phase 1+2 job
    # Phase 4 (per block):
    out = mws_biodiversity(clean_csv, state, district, block, GEOSERVER_URL)
    # ...sync to GeoServer + save_layer_info_to_db (see Phase 4)...
    return f"biodiversity computed for {district}_{block}"
```

Wire the API in [`computing/api.py`](../api.py) + [`computing/urls.py`](../urls.py) exactly like the
other endpoints:

```python
@api_view(["POST"])
@schema(None)
def generate_biodiversity_layer(request):
    state = request.data.get("state").lower(); district = request.data.get("district").lower()
    block = request.data.get("block").lower(); gee_account_id = request.data.get("gee_account_id")
    generate_biodiversity.apply_async(args=[state, district, block, gee_account_id], queue="nrm")
    return Response({"Success": "biodiversity task initiated"}, status=status.HTTP_200_OK)
```

**Split the work:** Phases 1–3 (download → clean → national raster) are a **one-off / scheduled
national job** (run rarely; GBIF data is large and changes slowly). Phase 4 (per-MWS) is the
**per-block task** above. Do not re-download GBIF on every block request.

---

## Testing & validation

1. **Download smoke test:** request a *small* download first (`COUNTRY = IN` AND a single
   `TAXON_KEY`, e.g. one bird family) so you can iterate in minutes, not hours.
2. **Cleaning:** print before/after record counts; eyeball that dropped fraction is plausible
   (often 10–40%). Confirm no points remain outside the India bbox.
3. **Raster:** open the GeoTIFF in QGIS; richness should be high near known hotspots (Western Ghats,
   Himalayas, big cities = sampling, not just nature) and the occurrence-count raster should look
   *similar* — that visual similarity **is** the sampling-bias story.
4. **Per-MWS:** pick one MWS; manually count distinct species of its contained points in pandas and
   assert it equals the layer's `species_richness`. Confirm zero-record MWS are present with
   `data_poor=True`, not missing.
5. **Excel/KYL/reports:** same as any layer — confirm the `biodiversity` sheet, the five KYL keys,
   and the rendered report sections. Use a **pilot block** end-to-end before any batch run.
6. **Provenance:** confirm the GBIF DOI is stored and shown in the report.

---

## Touchpoint checklist

| Phase | File                                                                          | Change                                                                          |
| ----- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| 0     | `installation/seed/seed_data.json`                                          | add`Biodiversity Occurrence` + `Biodiversity Richness Raster` Dataset rows  |
| 0     | `installation/geoserver/styles/`                                            | add`richness.sld` + MWS vector style; create `biodiversity` workspace       |
| 0     | `data/STAC_specs/input/metadata/layer_mapping.csv`                          | add LayerMapping rows; run`load_layer_mappings`                               |
| 0     | `requirements.txt`, `.env`                                                | add`pygbif/geopandas/rasterio/shapely`; add `GBIF_USER/GBIF_PWD/GBIF_EMAIL` |
| 1     | `computing/gbif/gbif_download.py`                                           | **new** — download India occurrences (one-off job)                       |
| 2     | `computing/gbif/gbif_clean.py`                                              | **new** — coordinate cleaning/filtering                                  |
| 3     | `computing/gbif/gbif_raster.py`, `config.py`                              | **new** — grid → richness + count GeoTIFFs → GCS → GEE/GeoServer      |
| 4     | `computing/gbif/gbif_vector.py`                                             | **new** — point-in-polygon → per-MWS stats → GeoServer + DB            |
| —    | `computing/gbif/biodiversity.py`                                            | **new** — Celery task orchestrating per-block compute                    |
| —    | `computing/api.py`, `computing/urls.py`                                   | add`generate_biodiversity_layer` endpoint + route + import                    |
| 5     | `stats_generator/utils.py`                                                  | `workspace == "biodiversity"` branch + `create_excel_for_biodiversity()`    |
| 6     | `stats_generator/mws_indicators.py`, `computing/gbif/gbif_indicators.py`  | add sheet + 5 KYL keys                                                          |
| 7     | `dpr/gen_mws_report.py`, `dpr/api.py`, `templates/mws-report.html`      | `get_biodiversity_data()` + section                                           |
| 7     | `dpr/gen_tehsil_report.py`, `dpr/api.py`, `templates/block-report.html` | `get_biodiversity_pattern_data()` + section                                   |

**Suggested order:** 0 → (1+2+3 as a national one-off) → 4 → 5 → 6 → 7. Land Phases 0–6 first (that
already gives working KYL filters), then Phase 7 (reports).

---

## Glossary

- **GBIF** — Global Biodiversity Information Facility; free aggregator of species occurrence records.
- **Occurrence record** — one observation/specimen of a species at a lat/lon and time.
- **taxonKey / species** — GBIF's identifiers for *which* species a record is.
- **basisOfRecord** — how the record was made (human observation, preserved specimen, …).
- **Species richness** — number of *distinct* species in an area. The headline biodiversity metric.
- **Shannon diversity index** — richness weighted by how evenly individuals are spread across
  species; higher = more diverse and even.
- **Sampling effort** — number of records; a proxy for how well an area was surveyed. Must always be
  reported with richness (see [Section 3](#3-️-the-one-mistake-that-will-ruin-the-results-sampling-bias)).
- **MWS / `uid`** — micro-watershed unit; `uid` is its unique id and the join key across the whole stack.
- **DwC-A / SIMPLE_CSV** — GBIF download formats; we use SIMPLE_CSV (one tab-delimited table).

---

### Sources

- [pygbif occurrences module](https://pygbif.readthedocs.io/en/latest/modules/occurrence.html) ·
  [GBIF API Downloads](https://techdocs.gbif.org/en/data-use/api-downloads) ·
  [GBIF download formats](https://techdocs.gbif.org/en/data-use/download-formats)
- [CoordinateCleaner — cleaning GBIF data](https://ropensci.github.io/CoordinateCleaner/articles/Cleaning_GBIF_data_with_CoordinateCleaner.html)
  (sampling-bias / richness-inflation rationale)

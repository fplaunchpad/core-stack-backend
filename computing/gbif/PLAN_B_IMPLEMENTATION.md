# Plan B — Species Layer Implementation Guide

> **Decision (settled):** Plan A (merging species into the LULC raster) is dropped — it's not
> feasible (see [`Species-Plan.md`](Species-Plan.md)). We build **Plan B: a separate species
> pipeline** that produces its own rasters + per-MWS vectors, shown *alongside* the LULC layers.
>
> **Build order:** **Level A first** (species richness of a selected area — a snapshot), then
> **Level B** (species change over time, with effort normalization) on top of it.
>
> This document is the concrete build guide: the module layout, what each file does, the function
> signatures, which existing helpers each step calls, and how it all wires into the API. Untested
> code is marked ⚠️ — dependencies and credentials are not yet installed (see [§9](#9-environment--dependencies)).

---

## Table of contents

1. [Architecture at a glance](#1-architecture-at-a-glance)
2. [Module layout](#2-module-layout)
3. [Data flow (Level A → Level B)](#3-data-flow-level-a--level-b)
4. [Level A — per-MWS species richness](#4-level-a--per-mws-species-richness)
5. [Level B — species change over time](#5-level-b--species-change-over-time)
6. [Existing functions we call (reuse map)](#6-existing-functions-we-call-reuse-map)
7. [API + task wiring](#7-api--task-wiring)
8. [Registry / GeoServer / STAC (Phase 0)](#8-registry--geoserver--stac-phase-0)
9. [Environment & dependencies](#9-environment--dependencies)
10. [Build phases & checklist](#10-build-phases--checklist)
11. [Testing & validation](#11-testing--validation)

---

## 1. Architecture at a glance

Species data is **sparse points from GBIF over HTTP**, not a dense GEE raster. So the novel work is a
small **Python ETL + spatial-join** stage; everything after the per-MWS table is the **standard CoRE
Stack chain** every layer already uses.

```
   ┌──────────────── NEW (species-specific, this folder) ─────────────────┐
   │  pygbif download        clean            spatial join / grid          │
   │  (taxon + area +  ──►  (bbox, coord ──►  ┌ point-in-polygon → per-MWS ─┼─► per-MWS table
   │   year window)         uncertainty,     │  richness + effort          │   (keyed on uid)
   │  [lat/lon, species,    dedupe)          └ coarse grid → richness .tif ─┼─► raster
   │   date]                                                                │
   └───────────────────────────────────────────────────────────────────────┘
                                                                             │
   ┌──────────── STANDARD chain (identical to every other layer) ───────────▼──┐
   │  raster ─► GCS ─► GEE asset / GeoServer raster           (map tiles)       │
   │  per-MWS table ─► sync_layer_to_geoserver ─► save_layer_info_to_db         │
   │            ─► stats Excel ─► KYL keys ─► mws/tehsil report sections        │
   └────────────────────────────────────────────────────────────────────────────┘
```

**Level A** stops after producing the per-MWS richness table + snapshot raster.
**Level B** runs Level A's per-MWS aggregation for **two year windows**, rarefies to equal effort,
and diffs → a per-MWS *change* table + change raster.

---

## 2. Module layout

```
computing/gbif/
  README.md                       ← original static-layer plan (Phases 0–7 background)
  Species-Plan.md                 ← plans + feasibility + mentor message
  SPECIES_CHANGE_DETECTION_FEASIBILITY.md
  PLAN_B_IMPLEMENTATION.md         ← THIS FILE
  __init__.py

  config.py                        ← tunables (grid res, thresholds, India bbox, cache dir)
  gbif_download.py                 ← pull occurrences for a taxon + area + year window (cached)
  gbif_clean.py                    ← coordinate cleaning / filtering
  gbif_richness.py                 ← LEVEL A: per-MWS richness (point-in-polygon) + snapshot raster
  gbif_species_change.py           ← LEVEL B: two-window rarefied richness diff → change table/raster
  species_task.py                  ← Celery tasks: generate_species_richness / generate_species_change
```

Files edited **outside** this folder: `computing/api.py`, `computing/urls.py`,
`installation/environment.yml`, plus Phase-0 registry files (see [§8](#8-registry--geoserver--stac-phase-0)).

---

## 3. Data flow (Level A → Level B)

Both levels take the **same inputs the change-detection endpoints already take** —
`state, district, block, start_year, end_year, gee_account_id` — plus a **`taxon_key`** (which
species/group). This means the frontend reuses the existing location + year-window picker.

```
Level A (snapshot):
  download(taxon, block-bbox, [start..end])  →  clean  →  fetch MWS polygons (uid)
     →  point-in-polygon join  →  per-MWS { species_richness, occurrence_count, shannon, data_poor }
     →  sync_layer_to_geoserver + save_layer_info_to_db
     →  (optional) coarse richness raster → GCS → GEE/GeoServer

Level B (change over time):
  split the window into THEN (early years) and NOW (late years)
     →  run Level A aggregation for each window  →  two per-MWS tables
     →  rarefy both to equal effort per MWS       →  comparable richness
     →  classify per MWS: richness_gain / richness_loss / stable / data_poor
     →  sync_layer_to_geoserver + save_layer_info_to_db (separate dataset)
```

---

## 4. Level A — per-MWS species richness

**File:** `gbif_richness.py`. This is the defensible first deliverable: "how species-rich is this area."

### 4.1 Core function

```python
def mws_species_richness(clean_csv, state, district, block, taxon_key):
    """
    Point-in-polygon join of GBIF occurrences onto the block's MWS polygons.
    Returns a GeoDataFrame with one row per MWS uid:
        uid, species_richness, occurrence_count, shannon_diversity_index,
        dominant_taxon_group, species_list, data_poor
    Richness is computed from the POINTS (distinct taxonKey), never by averaging a raster.
    """
```

Key points:
- `occurrence_count` (sampling effort) is stored **next to** `species_richness` — always. Richness is
  meaningless without effort (see [`README.md`](README.md) §3).
- MWS with `< MIN_RECORDS` (config, default 20) are **kept** and flagged `data_poor = True`, not
  dropped and not shown as "0 species."
- MWS polygons come from GeoServer WFS (`filtered_mws_<district>_<block>_uid`) — the same `uid` join
  key as every other layer.

### 4.2 Optional snapshot raster

`build_richness_raster(clean_csv, out_tif, res_deg=RICHNESS_GRID_DEG)` lays a **coarse** grid
(default 0.05°) over the block, counts distinct `taxonKey` per cell, writes a GeoTIFF, and also
writes a companion **effort** (occurrence-count) raster. Coarse resolution is deliberate — a 10 m grid
would be ~99.9% empty for point data.

---

## 5. Level B — species change over time

**File:** `gbif_species_change.py`. Built entirely on Level A.

### 5.1 The effort problem (why we can't just diff)

GBIF uploads grow over time, so a **raw** richness diff rises almost everywhere — that's the upload
curve, not ecology. Presence-only data also **can't prove disappearance**. So Level B must:

1. **Rarefy** — down-sample both windows to the **same record count per MWS** before counting species
   ("richness at equal effort").
2. **Report effort alongside change** (`Δrichness` and `Δoccurrence_count` together).
3. Only classify where **both windows have adequate effort**; otherwise `data_poor = "cannot assess"`.

### 5.2 Core functions

```python
def _rarefy_richness(taxon_keys, sample_size, n_iter=RAREFACTION_ITERS):
    """Expected species count when sampling `sample_size` records (mean over n_iter draws)."""

def mws_species_change(clean_csv, state, district, block, taxon_key, then_years, now_years):
    """
    Run the Level-A per-MWS aggregation for the THEN and NOW windows, rarefy both to the
    per-MWS min(effort_then, effort_now), diff, and classify each MWS uid into:
        richness_gain / richness_loss / stable / data_poor
    Returns a GeoDataFrame keyed on uid with:
        richness_then, richness_now, rarefied_then, rarefied_now, delta_richness,
        effort_then, effort_now, change_class, data_poor
    """
```

`then_years` / `now_years` default to the same split the LULC change detection uses (early years =
then, later years = now), configurable.

---

## 6. Existing functions we call (reuse map)

**Reused as-is (no changes to these files):**

| Function | File | Step |
| --- | --- | --- |
| `ee_initialize` | `utilities/gee_utils.py` | task startup |
| `get_gee_asset_path` | `utilities/gee_utils.py` | asset paths |
| `valid_gee_text` | `utilities/gee_utils.py` | sanitize district/block names |
| `upload_tif_to_gcs` | `utilities/gee_utils.py` | local richness `.tif` → GCS |
| `gcs_to_gee_asset_cli` | `utilities/gee_utils.py` | GCS → GEE asset |
| `sync_raster_to_gcs` / `sync_raster_gcs_to_geoserver` | `utilities/gee_utils.py` | raster → GeoServer |
| `export_raster_asset_to_gee` | `utilities/gee_utils.py` | export change raster |
| `gdf_to_ee_fc` | `utilities/gee_utils.py` | (GEE-native option) points → FC |
| `check_task_status`, `make_asset_public` | `utilities/gee_utils.py` | task polling / perms |
| `save_layer_info_to_db(state, district, block, layer_name, asset_id, dataset_name, misc=...)` | `computing/utils.py` | register layer |
| `sync_layer_to_geoserver(state_name, fc, layer_name, workspace)` | `computing/utils.py` | per-MWS vector → GeoServer |
| `update_layer_sync_status(layer_id, sync_to_geoserver=True)` | `computing/utils.py` | mark synced |

**Reused as a *pattern* (copied, not imported):** the `reduceRegions()`-over-`filtered_mws_..._uid`
loop from [`change_detection_vector.py`](change_detection_vector.py) `generate_vector()`.

**Genuinely new:** `config.py`, `gbif_download.py`, `gbif_clean.py`, `gbif_richness.py`,
`gbif_species_change.py`, `species_task.py`, the two API endpoints, and registry rows.

---

## 7. API + task wiring

Mirror the existing `change_detection` endpoint (`computing/api.py:685`) exactly.

**Tasks** (`species_task.py`), following `get_change_detection`'s `@app.task(bind=True)` / `queue="nrm"`:

```python
@app.task(bind=True)
def generate_species_richness(self, state, district, block, taxon_key,
                              start_year, end_year, gee_account_id): ...

@app.task(bind=True)
def generate_species_change(self, state, district, block, taxon_key,
                            start_year, end_year, gee_account_id): ...
```

**Endpoints** (`computing/api.py`), same shape as `change_detection`, plus `taxon_key`:

```python
@api_view(["POST"])
@schema(None)
def species_richness(request):
    state = request.data.get("state").lower(); district = request.data.get("district").lower()
    block = request.data.get("block").lower()
    taxon_key = request.data.get("taxon_key")
    start_year = request.data.get("start_year"); end_year = request.data.get("end_year")
    gee_account_id = request.data.get("gee_account_id")
    generate_species_richness.apply_async(
        args=[state, district, block, taxon_key, start_year, end_year, gee_account_id],
        queue="nrm")
    return Response({"Success": "species_richness task initiated"}, status=status.HTTP_200_OK)

# species_change(request) — identical, calls generate_species_change
```

**URLs** (`computing/urls.py`, next to the change-detection routes):

```python
path("species_richness/", api.species_richness, name="species_richness"),
path("species_change/",   api.species_change,   name="species_change"),
```

---

## 8. Registry / GeoServer / STAC (Phase 0)

Same one-time setup as any layer (see [`README.md`](README.md) Phase 0):

- **`Dataset` rows** in `installation/seed/seed_data.json`: `Species Richness` (vector) and
  `Species Change` (vector); optionally `Species Richness Raster` (raster). `save_layer_info_to_db`
  does `Dataset.objects.get(name=...)` and will crash if missing.
- **GeoServer:** workspace `biodiversity` (or reuse an existing one); SLD styles — graduated ramp for
  richness, diverging ramp (loss↔gain) for change.
- **STAC:** rows in `data/STAC_specs/input/metadata/layer_mapping.csv`; run `load_layer_mappings`.

---

## 9. Environment & dependencies

Dependencies are managed by **conda** in [`installation/environment.yml`](../../installation/environment.yml)
(there is no `requirements.txt`). Add:

```yaml
  - geopandas
  - rasterio
  - shapely
  - pip:
      - pygbif
```

`pandas` / `numpy` are already present. GBIF credentials go in the environment (mirror how other
secrets are handled):

```bash
GBIF_USER=...    GBIF_PWD=...    GBIF_EMAIL=...
```

⚠️ **Current state:** these are **not yet installed** in this environment (`import geopandas` fails),
and GBIF/GEE credentials are not configured — so the scaffolded code compiles and is review-ready but
has **not been run end-to-end**. First run must happen after `conda env update` + credentials.

---

## 10. Build phases & checklist

| Phase | Deliverable | Files |
| ----- | ----------- | ----- |
| 0 | Registry + GeoServer workspace/styles + STAC + deps | `seed_data.json`, GeoServer, `environment.yml` |
| 1 | Taxon+area+window download (cached) | `config.py`, `gbif_download.py` |
| 2 | Coordinate cleaning | `gbif_clean.py` |
| 3 | **Level A** per-MWS richness + snapshot raster | `gbif_richness.py` |
| 4 | Level A task + endpoint, end-to-end on a pilot block | `species_task.py`, `api.py`, `urls.py` |
| 5 | **Level B** rarefied two-window change | `gbif_species_change.py` |
| 6 | Level B task + endpoint | `species_task.py`, `api.py`, `urls.py` |
| 7 | Excel / KYL / report sections (separate "Species" section) | `stats_generator/*`, `dpr/*` |

**Land Phases 0–4 first** (working richness layer + KYL), then 5–6 (change), then 7 (reports).

---

## 11. Testing & validation

1. **Download smoke test:** one small taxon (e.g. a single bird family) + a short window, one block —
   iterate in minutes, not hours. Cache the CSV + the download DOI (for citation).
2. **Cleaning:** log before/after record counts; confirm no points outside the block bbox.
3. **Level A:** pick one MWS, manually count distinct `taxonKey` of its contained points in pandas and
   assert it equals the layer's `species_richness`. Confirm zero-record MWS appear with `data_poor=True`.
4. **Level B rarefaction:** confirm that when effort is equal in both windows, rarefied ≈ raw; and that
   a big effort jump does **not** by itself produce a `richness_gain` (the whole point).
5. **Standard chain:** confirm the GeoServer layers, `Layer` DB rows, and (later) the Excel sheet + KYL
   keys + report section — always showing effort next to richness.

---

### Reference docs

- [`Species-Plan.md`](Species-Plan.md) — the decision (Plan A dropped, Plan B chosen) + mentor message.
- [`SPECIES_CHANGE_DETECTION_FEASIBILITY.md`](SPECIES_CHANGE_DETECTION_FEASIBILITY.md) — deeper feasibility + sampling-bias analysis.
- [`README.md`](README.md) — original static-layer plan (Phase 0 registry, cleaning, raster helpers).
- Existing pipeline pattern: [`change_detection_vector.py`](change_detection_vector.py) (`generate_vector` / reduceRegions).
</content>

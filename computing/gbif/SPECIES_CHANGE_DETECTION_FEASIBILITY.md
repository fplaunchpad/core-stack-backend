# GBIF Species Change Detection — Feasibility & Design Note

> **Audience:** MWS / FPL team (decision-makers) + the developer who will build it.
> **Purpose:** assess whether we can do *change detection on species* the same way we do it for
> water / forest / built-up, review the proposed plan, flag the risks, and specify the route that
> actually works — reusing the functions we already have.
>
> **TL;DR:**
> - The "**merge a species band into the LULC `.tif` and run the existing change detection**" idea
>   (Plan A) **will not work** — the change-detection code is hard-wired to LULC's categorical class
>   scheme. Feeding it a species band produces meaningless output.
> - The "**keep species as its own raster, run a species-specific change detection, vectorize
>   separately**" idea (Plan B, which you also proposed) **is the correct and feasible route.** Build this.
> - **One scientific caveat gates the whole feature:** GBIF change over time mostly measures *who
>   uploaded observations*, not *what actually changed on the ground*. This is manageable but must be
>   designed for from day one, and stated in every output.

---

## Table of contents

1. [What we're trying to build](#1-what-were-trying-to-build)
2. [How the current change detection actually works](#2-how-the-current-change-detection-actually-works)
3. [Plan A — merge species into LULC and reuse change detection: **not feasible**](#3-plan-a--merge-species-into-lulc-and-reuse-change-detection-not-feasible)
4. [The scientific landmine: GBIF change ≠ ecological change](#4-the-scientific-landmine-gbif-change--ecological-change)
5. [Plan B — the route that works](#5-plan-b--the-route-that-works)
6. [Which existing functions we reuse (and which are new)](#6-which-existing-functions-we-reuse-and-which-are-new)
7. [Proposed phased implementation](#7-proposed-phased-implementation)
8. [Open decisions for the team](#8-open-decisions-for-the-team)

---

## 1. What we're trying to build

**The vision (as proposed):** the user picks a **taxon key** (a species or group in GBIF). We download
its occurrences for the **same year window the user picks for MWS change detection**, turn them into a
raster, and then run change detection so the report shows *how that species' presence changed over
time* — sitting next to the existing water / forest / built-up / cropping-intensity change layers.

Two variants were proposed:

- **Plan A (merge):** build a species `.tif`, align it to the LULC `.tif` by lat/lon, merge them into
  one raster that "contains species + water + forest + built-up", and feed that to the **existing**
  change detection.
- **Plan B (separate):** build our **own** species `.tif`, run change detection on it, vectorize it,
  and show it as a **separate** layer.

This note evaluates both against how the pipeline really works.

---

## 2. How the current change detection actually works

Read this first — it's why Plan A fails and Plan B works.
Source: [`change_detection.py`](change_detection.py) and [`change_detection_vector.py`](change_detection_vector.py).

The current pipeline is **100% Google Earth Engine + LULC**. It never touches raw points or local
`.tif` files as input:

1. **Input = annual LULC rasters already in GEE.** For each year it loads
   `…_<district>_<block>_<year>-07-01_<year+1>-06-30_LULCmap_10m` — a **single-band categorical
   raster** where every 10 m pixel holds **one LULC class code** (1=built-up, 2=water, 3=tree/forest,
   … 12=cropland, etc.).
2. **"Then" vs "now".** It takes `ee.ImageCollection(first 3 years).mode()` as *then* and
   `mode(later years)` as *now* — the statistical mode of a **discrete class label**.
3. **Transition classification.** Each detector (`built_up`, `change_degradation`,
   `change_deforestation`, `change_afforestation`, `change_cropping_intensity`) does a
   `.remap([1,2,3,4,6,7,8,9,10,11,12], …)` to collapse classes, then encodes specific
   **class→class transitions** (e.g. `then==3 (forest) AND now==1 (built-up)` → "deforestation to
   built-up"). The output raster's pixel values are **transition codes**.
4. **Vectorization.** `generate_vector()` masks each transition code and runs
   `reduceRegions(collection=MWS, reducer=sum, scale=10)` over the `filtered_mws_…_uid`
   FeatureCollection — producing **per-MWS area (ha) of each transition type**, keyed on `uid`.

**The load-bearing assumption:** *every pixel is exactly one mutually-exclusive land-cover class, and
change = a class label flipping to another class label.* The entire remap/mode/transition machinery
depends on that.

---

## 3. Plan A — merge species into LULC and reuse change detection: **not feasible**

Plan A breaks the load-bearing assumption in three independent ways. Any one of them is fatal.

**3.1 Species data is not a mutually-exclusive land-cover class.**
LULC says "this pixel *is* forest." Species data says "someone *recorded* taxon X here (and probably
recorded nothing about taxa Y, Z…)." Absence in GBIF ≠ "not present" — it means "not observed." You
cannot slot a species into the 1–12 class scheme, and you cannot `mode()` it the way you mode a land
class.

**3.2 The detectors are hard-coded to LULC codes.**
`built_up()`, `change_degradation()`, etc. literally remap the fixed list `[1,2,3,4,6,7,8,9,10,11,12]`
and test `then.eq(3).And(now.eq(1))` and friends. A merged raster with an extra "species band" is
simply **not read** by these functions — they select a single band (`"constant"` / `"predicted_label"`)
and interpret it as LULC. Merging changes nothing about what the code looks at; you'd need entirely
different transition logic anyway. So "merge then reuse" gives you no reuse — you're writing new logic
regardless.

**3.3 Resolution mismatch makes a merged 10 m raster dishonest.**
LULC is a dense 10 m wall-to-wall product. GBIF is **sparse points** — most 10 m pixels in a block
have **zero** records, and most MWS have **zero records in any single year**. Rasterizing points to a
10 m grid produces a raster that is ~99.9% nodata with occasional lonely pixels. Aligning that to LULC
by lat/lon (which is trivial mechanically — a reproject to a common grid) does not fix the fact that
there's almost no signal per pixel. Species data is only meaningful at **coarse cells** (≈0.05–0.1°,
km-scale) and **aggregated over multi-year windows**, which is a different grid from LULC.

**Verdict:** don't merge into LULC and don't route through the existing detectors. Build species change
detection as its own thing (Plan B), and show it as a **companion layer** next to the LULC change
layers in the report — visually side-by-side, not physically merged in one raster.

> Note: a merged multi-band raster *for visualization/export only* is fine later if the frontend wants
> one file. That's a packaging choice at the very end — it has nothing to do with how change is
> computed.

---

## 4. The scientific landmine: GBIF change ≠ ecological change

**This is the single most important thing to tell the MWS team before committing.** The static-layer
README ([`README.md`](README.md) §3) already warns that GBIF richness is confounded by *sampling
effort*. For **change over time this is much worse**, because effort itself changes over time:

- **A species "appearing" between then and now is almost always "someone finally uploaded a record,"**
  not a real range expansion. GBIF uploads for India have grown enormously year on year (eBird,
  iNaturalist, digitized herbaria). A naive then/now diff will light up "new species everywhere" —
  that's the upload curve, not nature.
- **You can never prove disappearance from presence-only data.** "0 records now" could mean the
  species is gone, or that nobody looked. So a raw "species loss" layer is scientifically
  indefensible on its own.
- **Per-MWS, per-year data is extremely sparse.** Unlike LULC (a value in every pixel every year),
  most MWS have too few records in a given year to say anything. The 3-year-window trick from LULC
  helps but doesn't rescue genuinely unsurveyed areas.

**What makes it defensible (design requirements, not optional):**

1. **Always carry sampling effort next to the signal.** Compute and store `occurrence_count` (and
   ideally distinct-observer / distinct-day counts) per cell/MWS per window. Never show a change
   number without the effort behind it.
2. **Report change as effort-aware categories, not raw deltas.** Recommended per-MWS classes:
   - `newly_recorded` — present in *now* window, absent in *then* (⚠️ likely effort-driven; label it
     as "newly recorded", not "arrived").
   - `no_longer_recorded` — present *then*, absent *now* **only where effort now ≥ effort then**
     (otherwise it's a survey gap, not a loss).
   - `persistent` — present in both.
   - `never_recorded` / `data_poor` — effort below a threshold in either window → **explicitly "cannot
     assess"**, not "absent."
3. **Prefer coarse cells + multi-year windows** (mirror the "then = early window, now = late window"
   idea from LULC, but at ≈0.05–0.1° and 3-year windows).
4. **Consider effort normalization** (e.g. richness per unit effort, or a rarefied/estimator-based
   richness) if the team wants a defensible richness-change number rather than presence flags.

If the team is only comfortable with **presence/occupancy change of a chosen taxon** (Plan B core),
that's the most defensible first deliverable. Effort-normalized richness change is a v2.

---

## 5. Plan B — the route that works

This mirrors the existing pipeline's *shape* (then/now → classify → reduceRegions → per-MWS vector)
while respecting that species data is sparse points, not dense LULC.

```
  taxon_key + [start_year..end_year]   (same window the user picks for LULC change detection)
        │
        ▼
  (1) DOWNLOAD    pygbif occ.download(TAXON_KEY, COUNTRY=IN, HAS_COORDINATE, YEAR range)
        │         → cache CSV per (taxon, window).  DOI stored for citation.
        ▼
  (2) CLEAN       reuse gbif_clean.py filters (bbox, uncertainty, dedupe)   [from README Phase 2]
        │
        ▼
  (3) GRID + SPLIT BY WINDOW
        │   lay a coarse grid (≈0.05–0.1°) over the block/basin
        │   THEN window (early years) → presence(0/1) + effort raster
        │   NOW  window (late years)  → presence(0/1) + effort raster
        ▼
  (4) SPECIES CHANGE RASTER   (NEW logic — NOT built_up/change_degradation)
        │   classify each cell: newly_recorded / no_longer_recorded / persistent /
        │   never_recorded(data_poor)  — using the effort mask from §4
        ▼
  (5) PUSH RASTER   upload_tif_to_gcs → gcs_to_gee_asset_cli / sync_raster_gcs_to_geoserver
        │           (identical to how every raster reaches GEE/GeoServer today)
        ▼
  (6) VECTORIZE PER MWS   reduceRegions over filtered_mws_…_uid  (same pattern as
        │                 change_detection_vector.generate_vector) → per-MWS class areas/counts
        ▼
  (7) REGISTER + SYNC   save_layer_info_to_db + sync_layer_to_geoserver + update_layer_sync_status
        │               (dataset "Species Change Detection", separate from LULC change)
        ▼
  Report / KYL: a SEPARATE "Species change" section, shown ALONGSIDE the LULC change layers,
                always with the effort/data-poor caveat.
```

Two ways to build the "then/now presence" step (4), pick per team preference:

- **Python/geopandas (recommended first):** rasterize points to the coarse grid with `rasterio`, do
  presence + effort in `numpy`. Simplest, no GEE round-trip for the point→grid step. Matches the
  README's existing `gbif_raster.py` design.
- **GEE-native:** upload cleaned points as a FeatureCollection (`gdf_to_ee_fc`), reduce to an image on
  a coarse grid, and classify with `ee` — keeps everything in GEE like the LULC path. More consistent
  with the current stack but more moving parts for sparse data.

Either way, **step (6) reuses the exact `reduceRegions`-over-MWS pattern** from
`change_detection_vector.generate_vector()`, so per-MWS output is keyed on `uid` and flows into the
existing Excel → KYL → report chain unchanged.

---

## 6. Which existing functions we reuse (and which are new)

**Reuse as-is (no changes):**

| Function | File | Used for |
| --- | --- | --- |
| `ee_initialize` | [`utilities/gee_utils.py`](../../utilities/gee_utils.py) | GEE auth in the Celery task |
| `get_gee_asset_path` | `utilities/gee_utils.py` | build asset paths per state/district/block |
| `upload_tif_to_gcs` | `utilities/gee_utils.py` | local species `.tif` → GCS |
| `gcs_to_gee_asset_cli` | `utilities/gee_utils.py` | GCS → GEE asset |
| `sync_raster_to_gcs` / `sync_raster_gcs_to_geoserver` | `utilities/gee_utils.py` | raster → GeoServer tiles |
| `gdf_to_ee_fc` | `utilities/gee_utils.py` | (GEE-native option) points → FeatureCollection |
| `export_raster_asset_to_gee` | `utilities/gee_utils.py` | export the species-change raster to GEE |
| `check_task_status`, `make_asset_public` | `utilities/gee_utils.py` | task polling / permissions |
| `save_layer_info_to_db`, `update_layer_sync_status` | [`computing/utils.py`](../utils.py) | register the layer |
| `sync_layer_to_geoserver` | `computing/utils.py` | per-MWS vector → GeoServer |
| `filtered_mws_…_uid` FeatureCollection + `reduceRegions` **pattern** | `change_detection_vector.py` | per-MWS aggregation (copy the pattern, not the LULC labels) |

**Reuse with light adaptation:** the README's `gbif_download.py` / `gbif_clean.py` (§Phase 1–2) —
add a `taxonKey` predicate and a `YEAR` range predicate so we pull *one taxon over the chosen window*
instead of all-India-all-taxa.

**Genuinely new (must be written):**

- `gbif_species_change.py` — the then/now presence + effort classification (step 4). This is the
  **replacement** for `built_up`/`change_degradation`/etc. It is *not* a reuse of those; species
  transitions are a different scheme (see §3, §4).
- The Celery task `generate_species_change(state, district, block, taxon_key, start_year, end_year,
  gee_account_id)` — orchestrates 1→7, following the exact shape of `get_change_detection`.
- API endpoint + URL in [`computing/api.py`](../api.py) / [`computing/urls.py`](../urls.py), modeled on
  the existing change-detection endpoints (which already take `start_year`/`end_year`/`gee_account_id`).
- New `Dataset` rows ("Species Change Detection Raster" / "…Vector"), a GeoServer style, and STAC
  registry entries (same Phase-0 steps as the README).

**Net:** ~1 new analysis module + 1 task + 1 endpoint + registry rows. Everything around it is existing
plumbing. That's a small, well-bounded build — *because* we chose Plan B over Plan A.

---

## 7. Proposed phased implementation

Suggested order (each phase independently testable):

1. **Phase 0 — Registry.** Dataset rows, GeoServer workspace/style, STAC entries (as README Phase 0).
2. **Phase 1 — Taxon+window download.** Extend `gbif_download.py` with `TAXON_KEY` + `YEAR` predicates;
   cache CSV per `(taxon_key, start_year, end_year)`. Smoke-test with one small taxon + short window.
3. **Phase 2 — Clean.** Reuse `gbif_clean.py`; log dropped counts.
4. **Phase 3 — Then/now presence + effort rasters** at coarse resolution. Verify: effort raster and
   presence raster look plausible; most cells honestly nodata.
5. **Phase 4 — Species change classification** (`newly_recorded` / `no_longer_recorded` /
   `persistent` / `data_poor`) with the effort mask. This is the heart; review with the science lead.
6. **Phase 5 — Push raster** to GCS→GEE→GeoServer (reused helpers).
7. **Phase 6 — Per-MWS vectorize** via `reduceRegions` (reuse the `generate_vector` pattern) →
   `save_layer_info_to_db` + `sync_layer_to_geoserver`.
8. **Phase 7 — Report/KYL section** — a **separate** "Species change (taxon X)" block, always paired
   with effort + a `data_poor` caveat, shown next to the LULC change layers.

Run one **pilot block + one taxon** end-to-end before any batch/multi-taxon run.

---

## 8. Open decisions for the team

1. **Deliverable scope for v1:** presence/occupancy change of a *chosen taxon* (defensible, simple) vs
   effort-normalized *richness* change of a group (harder, needs rarefaction). Recommend the former first.
2. **Taxon input model:** a single `taxon_key` per run, a curated shortlist (e.g. key indicator
   species/pollinators/threatened taxa), or a whole group (birds, all plants)?
3. **Resolution & windows:** confirm coarse grid (≈0.05° vs 0.1°) and window definition (reuse LULC's
   "first-3-years = then, rest = now"? or fixed early/late windows?).
4. **Data-poor policy:** the record-count threshold below which a cell/MWS is "cannot assess" (README
   uses `<20` for static; change detection likely needs a per-window threshold).
5. **Where the point→grid step runs:** Python/geopandas (simpler) vs GEE-native (stack-consistent).
6. **Do we ever ship a merged multi-band raster** for the frontend (visual only), or keep species
   strictly as its own layer? (Recommendation: separate layer; merge only for display if the FE asks.)

---

### Sources

- Existing pipeline: [`change_detection.py`](change_detection.py),
  [`change_detection_vector.py`](change_detection_vector.py),
  [`CHANGE_DETECTION_FLOW_EXPLAINED.md`](../change_detection/CHANGE_DETECTION_FLOW_EXPLAINED.md).
- Existing GBIF static-layer plan: [`README.md`](README.md) (Phases 0–7, helper inventory, sampling-bias §3).
- Repo helpers: [`utilities/gee_utils.py`](../../utilities/gee_utils.py), [`computing/utils.py`](../utils.py).
- [pygbif occurrences](https://pygbif.readthedocs.io/en/latest/modules/occurrence.html) ·
  [GBIF API downloads](https://techdocs.gbif.org/en/data-use/api-downloads) ·
  [CoordinateCleaner (sampling-bias rationale)](https://ropensci.github.io/CoordinateCleaner/articles/Cleaning_GBIF_data_with_CoordinateCleaner.html)
</content>
</invoke>

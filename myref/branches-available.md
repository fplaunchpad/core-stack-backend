# Branches Available for the OCaml Conversion

> Companion to `branches-flow-connection.md` (architecture) and `convert.md` (procedure).
> This catalogs every branch in the **geospatial-computation cluster** as Branch 1…15.
> For each: the `{Input} → Work → {Output}` flow, then the **LOCAL part** and the **GEE part**.
>
> Verified against actual branch diffs on 2026-06-13.

## GEE policy (applies to every branch below)

1. **GEE computation → avoid entirely; reimplement in OCaml.** You cannot send OCaml to
   GEE. So a branch's "GEE part" is **not** something to leave in the cloud — it is OCaml
   work, ported from the `formuale.md` spec and checked against a GEE oracle.
2. **GEE data → download only, for the one block you're working on.** A few MB of clipped
   GeoTIFF/GeoJSON per layer; never pan-India, never a standing pipeline.
3. **Local computation → OCaml, no-brainer.** Straight port.

So in the per-branch notes below, **"100% GEE" does not mean "skip it"** — it means the
whole branch is GEE *computation* that must be reimplemented in OCaml, fed by a per-block
data download. The only thing the difficulty rating reflects is **how much input data** a
branch needs (vector joins = tiny; multi-sensor raster stacks = large), which is why the
data-heavy ones are *sequenced* late — not kept in GEE.

## Quick verdict table

| # | Branch | Local part | GEE part | OCaml difficulty |
|---|--------|-----------|----------|------------------|
| 1 | `feature/local-compute-station` | ★ large (the `*_local.py` suite) | small remainder | **Easiest start — recommended base** |
| 2 | `feature/local_compute_by_shiv` | large (more algorithms) | some | Medium (messy code, mine it) |
| 3 | `making_terrain_local` | 100% local | none | Easy |
| 4 | `feature/dem_excel_and_filter` | 100% local | none | Easy (but little geo-math) |
| 5 | `feature/ndvi_timeseries_data_in_excel` | 100% local | none | Easy (but little geo-math) |
| 6 | `feature/mws_intersects_swb` | local overlay math | none (WFS fetch) | Easy–medium |
| 7 | `feature/forest_additionality` | large (ML + raster ops) | map generation | Medium (hybrid) |
| 8 | `features/swb_catchment_area_fix` | 1-line geometry fix | — | Trivial (not a project) |
| 9 | `feature/mws_connectivity_pipeline` | none | 100% GEE | Medium (spatial join, needs data download) |
| 10 | `features/dem_river_canal_pipeline` | none | 100% GEE | Medium |
| 11 | `feature/dem-canal-feature` | none | 100% GEE | Medium–hard (4-case clip logic) |
| 12 | `feature/tree_health_pipeline_recompute` | none | 100% GEE | Hard (many years of per-block raster) |
| 13 | `feature/ET_downscaling` | none | 100% GEE | Hard (physics formulas OK, data heavy) |
| 14 | `feature/hls_ndvi` | none | 100% GEE | Hardest (multi-sensor harmonization) |
| 15 | `features/wb_ndvi` | none | 100% GEE | Hard (depends on Branch 14) |

---

## Branch 1 — `feature/local-compute-station` ⭐ RECOMMENDED BASE

**Flow:**
```
{ INPUT:  downloaded base rasters — LULC v3 GeoTIFFs (2017–2025), FABDEM terrain raster,
          AEZ shapefile, Microwatershed geojson, Aquifer geojson, SOI tehsil geojson
          (all declared in computing/config.yaml with Google-Drive/WFS sources) }
   → WORK: per-tehsil local computation with rasterio/numpy/geopandas — LULC clip,
           LULC vector stats, terrain TPI + 11-class landforms + K-means clusters,
           change detection, cropping intensity, LULC×terrain clusters, aquifer yield,
           soil health, SPEI drought (R script + CHIRPS download)
   → { OUTPUT: GeoTIFFs + GeoPackage vector layers → pushed to GeoServer + Layer rows in DB }
```

- **LOCAL part (→ OCaml directly):** the whole `*_local.py` suite — `local_compute_helper.py` (~870 lines of shared clip/mask/zonal logic), `lulc_v3_local.py`, `lulc_vector_local.py`, `terrain_*_local.py`, `change_detection_*_local.py`, `cropping_intesity_local.py`, `aquifer_vector_local.py`, `lulc_on_{plain,slope}_cluster_local.py`, soil-health modules. No `ee.*` calls in the math. This is the single largest ready-to-port surface in the repo.
- **GEE part (→ OCaml via formulas + exported inputs):** whatever the branch still defers to main's pipelines (hydrology P/Q/ET/ΔG chain, MWS delineation, drainage). Not blocking — port the local suite first.
- **Note:** the SPEI/drought module shells out to an **R script** — a second porting source, not GEE.

---

## Branch 2 — `feature/local_compute_by_shiv` 📦 PARTS BIN

**Flow:**
```
{ INPUT:  GeoPackages downloaded from GeoServer (utilities/download_gpkg_from_geoserver.py),
          DEM rasters, pan-India facility/feature layers }
   → WORK: local versions of DEM analysis, drainage density/lines, catchment delineation,
           river/canal layers, slope, facilities proximity, natural depression, restoration,
           soge, mws centroid + connectivity, mining, green credit
   → { OUTPUT: vector/raster layers → GeoServer + DB }
```

- **LOCAL part (→ OCaml directly):** broadest *algorithm coverage* of any branch — DEM/drainage/catchment/connectivity math that Branch 1 lacks. Port the algorithms, not the code.
- **GEE part:** some modules still pull inputs from GEE assets.
- **⚠ Caveats:** hardcoded `/home/cfpt-jedi/...` paths, no config loader, committed `.installation_state` artifacts, noisy history. **Mine it for algorithms; never base on it.**

---

## Branch 3 — `making_terrain_local`

**Flow:**
```
{ INPUT:  FABDEM raster (30 m, clipped per block), LULC v3 rasters, tehsil watershed
          GeoPackages, Aquifer geojson, AEZ shapefile }
   → WORK: terrain clustering (TPI → landforms → K-means with hardcoded centroids),
           LULC×terrain, cropping intensity, aquifer yield, change-detection vectors —
           all clip/mask/aggregate with rasterio + geopandas
   → { OUTPUT: GeoPackage layers + clipped FABDEM GeoTIFFs → GeoServer + DB }
```

- **LOCAL part (→ OCaml directly):** **100% local — zero GEE calls.** Effectively an earlier/parallel cut of Branch 1's terrain work, with the same helper-function pattern (`compute_terrain_properties_for_watersheds()`, `clip_raster_with_roi()`).
- **GEE part:** none.
- **Use:** cross-reference with Branch 1's terrain modules; port whichever version is cleaner.

---

## Branch 4 — `feature/dem_excel_and_filter`

**Flow:**
```
{ INPUT:  MWS stats layers fetched from GeoServer via WFS (terrain, cropping intensity,
          SWB, NREGA, drought, change detection), MWS geometry GeoJSON }
   → WORK: pandas aggregation + filtering of KYL (Know Your Landscape) indicators
           per district/block; assembles multi-sheet workbook
   → { OUTPUT: Excel .xlsx (one sheet per dataset) + KYL geojson for dashboards }
```

- **LOCAL part (→ OCaml directly):** 100% local (pandas/openpyxl). Portable, **but it's tabular plumbing, not geospatial math** — low research value for the OCaml core.
- **GEE part:** none.

---

## Branch 5 — `feature/ndvi_timeseries_data_in_excel`

**Flow:**
```
{ INPUT:  GeoServer WFS vector layers carrying NDVI_<year> JSON properties
          (date → NDVI value time-series per feature) }
   → WORK: parse GeoJSON properties, extract time-series arrays, tabulate per layer
   → { OUTPUT: Excel .xlsx per district/block with NDVI_2017…NDVI_2024 columns }
```

- **LOCAL part (→ OCaml directly):** 100% local. Same caveat as Branch 4 — JSON/Excel plumbing, minimal geo-math.
- **GEE part:** none (the NDVI values themselves were computed upstream by Branch 14/15).

---

## Branch 6 — `feature/mws_intersects_swb`

**Flow:**
```
{ INPUT:  village boundaries, MWS polygons, SWB (surface water body) features —
          all fetched from GeoServer via WFS }
   → WORK: vector overlay — intersect MWS × SWB per village, count + area aggregation
           (shapely/pandas in village_indicators.py)
   → { OUTPUT: Excel .xlsx with an mws_intersect_swb sheet per district/block }
```

- **LOCAL part (→ OCaml directly):** the intersection/aggregation math — a clean, small polygon-overlay workload, good early GEOS-binding exercise.
- **GEE part:** none (data comes over WFS HTTP, which OCaml can also do).

---

## Branch 7 — `feature/forest_additionality` (HYBRID)

**Flow:**
```
{ INPUT:  GLC-FCS30D annual forest-cover rasters 1985–2022 (GEE), Landsat/Dynamic World
          (optional), state-level auxiliary data (forest type, climate, soil, population)
          downloaded to data/forest_additionality/<state>/ }
   → WORK: GEE generates pre/mid/post forest-cover maps → exported to GCS → downloaded;
           THEN local Python: afforestation mask (numpy classification), random-forest /
           MCT risk modeling, vulnerability raster generation (gdal + scipy, ~2000 LOC)
   → { OUTPUT: GeoTIFF afforestation mask + vulnerability map, CSV area estimates }
```

- **LOCAL part (→ OCaml directly):** the entire modeling half — raster classification, risk/vulnerability map math. Substantial and genuinely local. (The RF/MCT model fitting would need an OCaml ML story or pre-trained model export.)
- **GEE part (→ OCaml via exported inputs):** only the forest-map preparation (filter + threshold + export) — simple to reproduce from downloaded GLC-FCS30D tiles.
- **Use case relevance:** carbon-credit additionality — a strong open-source story if your research wants an applied flagship.

---

## Branch 8 — `features/swb_catchment_area_fix`

```
{ INPUT: SWB geometries } → WORK: geometry.buffer(0) validity fix in computing/utils.py
                          → { OUTPUT: same layer, corrected areas }
```

- One-line bug fix touching both GEE and local codepaths. **Not a conversion target** — but note `buffer(0)` is exactly the validity-fix primitive your OCaml core must provide.

---

## Branch 9 — `feature/mws_connectivity_pipeline`

**Flow:**
```
{ INPUT:  pan-India MWS-connectivity GEE asset + local MWS ROI (filtered_mws_*_uid) }
   → WORK: GEE server-side: filterBounds → ee.Join.saveFirst → attach watershed UID
           to each connectivity feature
   → { OUTPUT: GEE FeatureCollection <district>_<block>_mws_connectivity → GeoServer + DB }
```

- **LOCAL part:** none today.
- **GEE part (→ OCaml):** a plain **spatial join** — entirely expressible as GEOS `intersects` + attribute copy. To port: export the pan-India connectivity dataset once (or per-state clips) as GeoPackage, then the OCaml job is `{connectivity gpkg + mws gpkg} → spatial join → gpkg`. One of the easiest *GEE-side* ports because there is no raster math at all.

---

## Branch 10 — `features/dem_river_canal_pipeline`

**Flow:**
```
{ INPUT:  pan-India River + Canal GEE assets, local MWS ROI }
   → WORK: GEE: filterBounds → ee.Join.saveAll (find ALL watersheds each river/canal
           crosses) → duplicate feature per intersecting watershed → attach uid/area
   → { OUTPUT: two GEE FeatureCollections (<d>_<b>_river_vector, <d>_<b>_canal_vector)
              → GeoServer + DB }
```

- **LOCAL part:** none today.
- **GEE part (→ OCaml):** spatial join with one-to-many expansion + clip. Same recipe as Branch 9: download pan-India river/canal vectors once, then pure GEOS work. Medium because of the feature-duplication semantics — fix the contract first (`convert.md` Phase 1).

---

## Branch 11 — `feature/dem-canal-feature`

**Flow:**
```
{ INPUT:  pan-India Canal asset, MWS ROI + outer ROI boundary polygon }
   → WORK: GEE 4-case logic — (1) canal in one watershed → clip+uid; (2) canal in many
           → one feature per watershed; (3) canal inside outer boundary but no watershed
           match → clip to boundary; (4) outside ROI → drop. Carries canal attributes
           (canname, cancode, prjname …)
   → { OUTPUT: GEE FeatureCollection of clipped canal segments → GeoServer + DB }
```

- **LOCAL part:** none today.
- **GEE part (→ OCaml):** refinement of Branch 10 with conditional clipping. All GEOS-expressible, but the 4-case semantics make the contract/verification work the bulk of the job.

---

## Branch 12 — `feature/tree_health_pipeline_recompute`

**Flow:**
```
{ INPUT:  pan-India canopy-height raster collection (CH_RASTER, per year), MWS ROI,
          start/end years }
   → WORK: GEE: filterBounds → mean → clip per year; refactors CCD/overall-change logic
   → { OUTPUT: GEE Image assets ch_raster_<d>_<b>_<year> → GeoTIFF → GeoServer + STAC/DB }
```

- **LOCAL part:** none today.
- **GEE part (→ OCaml):** the math is trivial (clip + per-year mean + the §9 masks in `formuale.md`). The full canopy-height series is TB-scale pan-India in GEE, but under rule 2 you only download the **one block's** clipped rasters per year — small. Reimplement the math in OCaml; sequence late only because it touches many years of raster input, not because it stays in GEE.

---

## Branch 13 — `feature/ET_downscaling`

**Flow:**
```
{ INPUT:  Landsat-8 TOA (B2–B7), GLDAS reanalysis (rain/temp/wind/soil moisture),
          MOD17 BPLUT biome table, MCD12Q1 land cover, AEZ raster, tehsil ROI }
   → WORK: GEE physics formulas, monthly: AET, PET, GPP, RWDI, Kc, WUE = GPP/AET —
           13-band stacks (12 monthly + annual) at 30 m
   → { OUTPUT: 7 GEE Image assets per year → COG GeoTIFF → GeoServer + DB }
```

- **LOCAL part:** none today (local code only submits/monitors GEE tasks).
- **GEE part (→ OCaml):** the formulas are deterministic pixel physics — very portable *math* (numpy-style → `owl`/Bigarray). The cost is **data**: monthly Landsat+GLDAS stacks. Under rule 2 you download only the one block's monthly stacks for the year being computed — bounded, not a pan-India ingestion pipeline. Reimplement the physics in OCaml; sequence late for data volume, not because GEE keeps the compute.

---

## Branch 14 — `feature/hls_ndvi`

**Flow:**
```
{ INPUT:  Landsat-7 TOA, Landsat-8 TOA, Sentinel-2 Harmonized TOA, ROI, date range }
   → WORK: GEE: per-sensor cloud masking → Chastain cross-sensor harmonization
           regression → NDVI → gap-filled time-series (gapfilled_NDVI_lsc)
   → { OUTPUT: GEE ImageCollection of harmonized NDVI (consumed by Branch 15,
              plantation suitability, ZOI modules) }
```

- **LOCAL part:** none today.
- **GEE part (→ OCaml):** hardest in the catalog — cloud-mask + Chastain regression chains, reimplementable in OCaml, but the inputs are multi-sensor L7/L8/S2 imagery. Under rule 2 you still pull only the one block's scenes for the date range (not hundreds of GB pan-India), so it stays bounded — just the largest per-block download here. Sequence it last; it is not a reason to keep NDVI computation in GEE.

---

## Branch 15 — `features/wb_ndvi`

**Flow:**
```
{ INPUT:  ZOI suitability FeatureCollection (with UID) + HLS-interpolated NDVI from
          Branch 14, start/end years }
   → WORK: GEE: reduceRegions — per-feature NDVI time-series per year, attached as
           NDVI_<year> JSON property
   → { OUTPUT: per-year GEE FeatureCollections, merged → GeoServer + DB
              (then Branch 5 turns them into Excel) }
```

- **LOCAL part:** none today.
- **GEE part (→ OCaml):** the operation itself is just **zonal statistics** (raster × polygons → per-feature series) — easy OCaml work *if* the NDVI rasters exist locally, which chains it behind Branch 14 (or behind "export NDVI from GEE per block").

---

## Recommended conversion order (ties the catalog together)

1. **Branch 1** local suite (with Branch 3 as cross-reference) — the core engine: clip, mask, zonal stats, classification tables, K-means assignment.
2. **Branch 6** overlay math + **Branch 9** spatial join — first vector-only GEE replacements; small data, pure GEOS.
3. **Branch 2** algorithms (drainage/DEM/catchment) folded in module by module.
4. **Branches 10–11** river/canal joins — same machinery as 9, more contract care.
5. **Branch 7** local modeling half — if the research wants the carbon-credit flagship.
6. **Branches 12, 13, 15, 14** — data-heavy GEE ports, in that order, only after the engine is proven; per-block GEE exports for testing throughout (golden-file method, `convert.md` Phase 4).

Branches 4, 5, 8 need no porting (plumbing / one-liner); keep them in Python.

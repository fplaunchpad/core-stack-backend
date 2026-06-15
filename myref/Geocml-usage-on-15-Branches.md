# geocaml repos → resolving each branch's calculations (local + cloud)

> Maps the **geocaml** libraries (surveyed in `Geocaml-exploring.md`) onto the **LOCAL** and
> **GEE/cloud** computations of each branch (catalogued in `gee-computations.md`). For every
> branch: which geocaml repo or **combination** resolves its math once ported to OCaml.
>
> **Conventions:**
> 🟢 ready · 🟡 early · 🔴 WIP/contribute · ✚ direct ctypes bind if geocaml not ready.
> "GEE/cloud" math is reimplemented locally in OCaml (you cannot send OCaml to GEE) and fed
> by per-block GEE data exports — geocaml provides the *engine*, not a GEE connection.
>
> **The recurring core stack** (used almost everywhere): `ocaml-geojson` (I/O) +
> `ocaml-proj` (CRS) + geometry ops via `ocaml-gdal`/OGR 🔴 or **✚ ctypes GEOS** +
> raster via `ocaml-gdal`🔴 / `yirgacheffe`🔴 (or `ocaml-tiff` read + `Owl`). Branch entries
> below name only the *additional or dominant* pieces beyond this core.

---

## Branch 0 — `main` (core pipeline) — the big one

**LOCAL math** (drainage density, rasterize, geometry utils):
- Drainage density → `ocaml-geojson` (read MWS/lines) + `ocaml-proj` (→7755) + **geometry
  length & clip** via `ocaml-gdal`/OGR 🔴 or ✚ctypes GEOS; sum by `ORDER` in plain OCaml.
- Rasterize vector → **raster write with affine+CRS**: `ocaml-gdal` 🔴 (or ✚ctypes GDAL;
  `ocaml-tiff` can't write georef yet).
- `buffer(0)`, point-in-polygon → geometry ops (OGR/GEOS).

**GEE/cloud math** (hydrology, CLART, drought, LULC, terrain, cropping, SWB, change, tree-health):
- All of it is **raster map-algebra + zonal stats** → **`yirgacheffe`** 🔴 (map-algebra) over
  rasters read by `ocaml-gdal` 🔴; per-MWS aggregation via `ocaml-rtree` (index) + zonal sum;
  CRS via `ocaml-proj`; classification/transition tables = plain OCaml `match`.
- This branch is where `yirgacheffe` + `ocaml-gdal` maturity matters most.

**Combination:** `ocaml-geojson` + `ocaml-proj` + `ocaml-gdal`🔴 + `yirgacheffe`🔴 +
`ocaml-rtree` + (✚ctypes GEOS for clip/length until OGR is ready).

---

## Branch 1 — `feature/local-compute-station` (porting reference)

**LOCAL math** (the `*_local.py` suite: LULC, terrain/TPI, change-detection, cropping,
aquifer, soil-health, SPEI):
- Raster reclassify/mask/focal(TPI)/zonal → **`yirgacheffe`** 🔴 (or `Owl`/`Bigarray`) over
  `ocaml-gdal`🔴-read rasters; K-means cluster assignment = plain OCaml (fixed centroids,
  Euclidean) ; aquifer/vector parts → `ocaml-geojson` + geometry ops.
- SPEI's R-script step → reimplement the SPEI math in OCaml (`Owl` stats), not a geocaml repo.

**GEE/cloud math:** residual data pulls only → per-block export; no extra engine.

**Combination:** `ocaml-gdal`🔴 + `yirgacheffe`🔴 + `Owl` + `ocaml-geojson`.

---

## Branch 2 — `feature/local_compute_by_shiv` (parts bin)

**LOCAL math** (DEM, drainage density/lines, catchment, river/canal, connectivity, slope, …):
- DEM/slope/catchment → raster ops (`ocaml-gdal`🔴 + `yirgacheffe`🔴 / `Owl`); slope = focal
  gradient.
- drainage/river/canal/connectivity vectors → `ocaml-geojson` + `ocaml-rtree` + geometry ops
  (OGR/✚GEOS).

**GEE/cloud math:** some input pulls → per-block export.

**Combination:** full core stack; emphasis on `ocaml-gdal`🔴 (DEM rasters) + `ocaml-rtree`.

---

## Branch 3 — `making_terrain_local` (100% local)

**LOCAL math** (TPI → 11-class landforms → 4-cluster K-means; LULC×terrain; cropping; aquifer):
- TPI = elevation − focal-mean → **`yirgacheffe`** 🔴 / `Owl` focal ops over FABDEM read by
  `ocaml-gdal`🔴; landform classes & K-means = plain OCaml; aquifer/vector → `ocaml-geojson`.

**GEE/cloud math:** none.

**Combination:** `ocaml-gdal`🔴 + `yirgacheffe`🔴/`Owl` + `ocaml-geojson` (+ `ocaml-proj`).

---

## Branch 4 — `feature/dem_excel_and_filter` (tabular)

**LOCAL math:** pandas/openpyxl aggregation of MWS indicators → Excel.
- → **`pruck`** 🔴 (dataframe) + `ocaml-parquet`/CSV; WFS fetch is HTTP (cohttp/eio), not geocaml.
- ⚠ Excel `.xlsx` writing has no geocaml repo — use a separate OCaml xlsx lib or emit CSV.

**GEE/cloud math:** none.

**Combination:** `pruck`🔴 (+ non-geocaml xlsx/CSV + HTTP).

---

## Branch 5 — `feature/ndvi_timeseries_data_in_excel` (tabular)

**LOCAL math:** parse `NDVI_<year>` JSON from WFS → time-series Excel.
- → `ocaml-geojson` (parse feature properties) + **`pruck`** 🔴 (tabulate) + CSV/xlsx.

**GEE/cloud math:** none.

**Combination:** `ocaml-geojson` + `pruck`🔴.

---

## Branch 6 — `feature/mws_intersects_swb` (GEOS overlay)

**LOCAL math:** intersect MWS × SWB per village, count/area.
- → `ocaml-geojson` (I/O) + **`ocaml-rtree`** (candidate index) + **geometry intersection/area**
  via `ocaml-gdal`/OGR 🔴 or ✚ctypes GEOS; tally with `pruck`/records.

**GEE/cloud math:** none.

**Combination:** `ocaml-geojson` + `ocaml-rtree` + (OGR🔴 / ✚GEOS). *Good first GEOS exercise.*

---

## Branch 7 — `feature/forest_additionality` (hybrid)

**LOCAL math** (afforestation mask, vulnerability map, RF/MCT models, area CSV):
- raster classification/mask/risk → **`yirgacheffe`** 🔴 / `Owl` over `ocaml-gdal`🔴 rasters;
  area sums → records/`pruck`.
- ⚠ **Random-forest / MCT ML** has no geocaml repo — use `Owl` ML or export a pre-trained model;
  this is the one branch needing an ML story beyond geocaml.

**GEE/cloud math:** GLC-FCS30D forest-map prep/export → per-block raster export.

**Combination:** `ocaml-gdal`🔴 + `yirgacheffe`🔴 + `Owl`(ML) + `pruck`.

---

## Branch 8 — `features/swb_catchment_area_fix` (trivial)

**LOCAL math:** `geometry.buffer(0)` validity fix.
- → single geometry op via `ocaml-gdal`/OGR 🔴 or ✚ctypes GEOS. (Not a real port target.)

**GEE/cloud math:** none.

**Combination:** OGR🔴 / ✚GEOS `buffer(0)`.

---

## Branch 9 — `feature/mws_connectivity_pipeline` (easiest GEE port)

**LOCAL math:** none today.

**GEE/cloud math:** spatial join (connectivity ↔ MWS), attach UID.
- → `ocaml-geojson` (read both, downloaded once) + **`ocaml-rtree`** (index) + `intersects`
  predicate (OGR🔴/✚GEOS) + attribute copy. **No raster, no `yirgacheffe` needed.**

**Combination:** `ocaml-geojson` + `ocaml-rtree` + (OGR🔴/✚GEOS). *Lowest-effort GEE-side port.*

---

## Branch 10 — `features/dem_river_canal_pipeline`

**LOCAL math:** none today.

**GEE/cloud math:** 1→many spatial join (river/canal × all intersecting watersheds) + uid/area.
- → same as Branch 9 (`ocaml-geojson` + `ocaml-rtree` + intersects) plus **length/area** via
  `ocaml-proj`(→7755) + geometry ops; emit one feature per match.

**Combination:** `ocaml-geojson` + `ocaml-rtree` + `ocaml-proj` + (OGR🔴/✚GEOS).

---

## Branch 11 — `feature/dem-canal-feature`

**LOCAL math:** none today.

**GEE/cloud math:** 4-case conditional canal clip (single/multi/inside-boundary/outside).
- → Branch-10 stack + **clip/intersection** (OGR🔴/✚GEOS) for the boundary cases; the 4-case
  logic = plain OCaml branching. Contract care (define the cases) > library need.

**Combination:** `ocaml-geojson` + `ocaml-rtree` + `ocaml-proj` + (OGR🔴/✚GEOS).

---

## Branch 12 — `feature/tree_health_pipeline_recompute` (data-heavy)

**LOCAL math:** none today.

**GEE/cloud math:** canopy-height per-year clip + mean + CCD/overall-change masks (IndiaSAT fusion).
- → raster read (`ocaml-gdal`🔴) + **`yirgacheffe`** 🔴 map-algebra (mean, mask = LULC==6,
  fusion codes) ; trivial math, the cost is per-block multi-year raster download (rule 2).

**Combination:** `ocaml-gdal`🔴 + `yirgacheffe`🔴.

---

## Branch 13 — `feature/ET_downscaling` (data-heavy)

**LOCAL math:** none today.

**GEE/cloud math:** monthly physics (AET/PET/GPP/RWDI/Kc/WUE), 13-band stacks from
Landsat-8+GLDAS+MOD17+MCD12Q1+AEZ.
- → pure pixel arithmetic over aligned monthly rasters → **`yirgacheffe`** 🔴 (ideal: this is
  textbook map-algebra) over `ocaml-gdal`🔴 reads; BPLUT lookup = OCaml table.

**Combination:** `ocaml-gdal`🔴 + `yirgacheffe`🔴 (+ `Owl` for regressions if any).

---

## Branch 14 — `feature/hls_ndvi` (hardest)

**LOCAL math:** none today.

**GEE/cloud math:** multi-sensor cloud masking (L7/L8/S2) → Chastain harmonization regression →
NDVI → gap-fill.
- → raster read (`ocaml-gdal`🔴) + **`yirgacheffe`** 🔴/`Owl` for per-pixel masking + the
  Chastain linear regression + `normalizedDifference`; largest per-block imagery download.

**Combination:** `ocaml-gdal`🔴 + `yirgacheffe`🔴 + `Owl`.

---

## Branch 15 — `features/wb_ndvi` (chains on 14)

**LOCAL math:** none today.

**GEE/cloud math:** zonal stats — Branch-14 NDVI rasters reduced over ZOI features per year.
- → **`ocaml-rtree`** (index features) + raster read (`ocaml-gdal`🔴) + zonal mean
  (`yirgacheffe`🔴/`Owl`) → write `NDVI_<year>` onto features (`ocaml-geojson`).

**Combination:** `ocaml-gdal`🔴 + `ocaml-rtree` + `yirgacheffe`🔴 + `ocaml-geojson`.

---

## Master mapping table

| Branch | Dominant need | geocaml combination | Blocking dependency |
|---|---|---|---|
| 0 `main` | hydrology/CLART/drought (raster + zonal) + drainage (vector) | geojson + proj + **gdal🔴 + yirgacheffe🔴** + rtree + ✚GEOS | gdal, yirgacheffe, GEOS |
| 1 local-compute-station | LULC/terrain/change (raster) | **gdal🔴 + yirgacheffe🔴** + Owl + geojson | gdal, yirgacheffe |
| 2 by_shiv | DEM/drainage/catchment | gdal🔴 + yirgacheffe🔴 + rtree + geojson + ✚GEOS | gdal, GEOS |
| 3 making_terrain_local | TPI/landforms/clusters | **gdal🔴 + yirgacheffe🔴/Owl** + geojson | gdal, yirgacheffe |
| 4 dem_excel_and_filter | tabular | **pruck🔴** + CSV/xlsx | pruck (+ xlsx lib) |
| 5 ndvi_timeseries_excel | tabular | geojson + **pruck🔴** | pruck |
| 6 mws_intersects_swb | polygon overlay | geojson + **rtree** + ✚GEOS | GEOS/OGR |
| 7 forest_additionality | raster + ML | gdal🔴 + yirgacheffe🔴 + **Owl(ML)** | gdal, ML story |
| 8 swb_catchment_area_fix | buffer(0) | ✚GEOS/OGR | GEOS/OGR |
| 9 mws_connectivity | spatial join | geojson + **rtree** + ✚GEOS | GEOS/OGR |
| 10 dem_river_canal | 1→many join + length | geojson + rtree + proj + ✚GEOS | GEOS/OGR |
| 11 dem-canal-feature | conditional clip | geojson + rtree + proj + ✚GEOS | GEOS/OGR |
| 12 tree_health_recompute | clip+mean+mask | **gdal🔴 + yirgacheffe🔴** | gdal, yirgacheffe |
| 13 ET_downscaling | monthly map-algebra | **gdal🔴 + yirgacheffe🔴** | gdal, yirgacheffe |
| 14 hls_ndvi | cloud-mask + regression | gdal🔴 + **yirgacheffe🔴/Owl** | gdal, yirgacheffe |
| 15 wb_ndvi | zonal stats | gdal🔴 + rtree + yirgacheffe🔴 + geojson | gdal, yirgacheffe |

## What this tells us (strategy)

- **Two repos unlock the most branches:** `ocaml-gdal` (raster+vector I/O, OGR geometry) and
  `yirgacheffe` (raster map-algebra). Both are 🔴 WIP. **Hardening/contributing to these two is
  the highest-leverage investment** — they cover Branches 0,1,2,3,7,12,13,14,15 (all the raster math).
- **The ready trio carries the vector/tabular branches now:** `ocaml-geojson` + `ocaml-rtree`
  (+ `ocaml-proj`) already enable Branches 6, 9, 10, 11 (joins/overlays) — *if* geometry ops
  are supplied. So the **GEOS gap** (OGR via gdal, or ✚ctypes) is the immediate unblocker for
  the cheapest GEE-side ports.
- **Two needs have no geocaml repo:** `.xlsx` writing (Branches 4,5) and ML (Branch 7) — source
  those outside geocaml.
- **Sequencing fit with `convert.md`:** start where geocaml is *ready* — Branch 6/9 (geojson +
  rtree + a thin GEOS) and Branch 0's drainage-density/rasterize — then move into the raster
  branches as `ocaml-gdal`/`yirgacheffe` mature (contributing upstream as we go, per `motive.md`).

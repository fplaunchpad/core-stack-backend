# GEE & Local Computations, by Branch

> Companion to `branches-available.md` (per-branch I/O), `formuale.md` (the math), and
> `convert.md` (the port procedure). This file lists, **for every branch in the
> geospatial-compute cluster, first the LOCAL computation, then the GEE computation** —
> so you can see exactly what must be reimplemented in OCaml (GEE part) vs. straight-ported
> (local part), per the project's GEE policy.
>
> **GEE policy reminder:** GEE *computation* is reimplemented in OCaml (you cannot send
> OCaml to GEE); GEE is used only as a per-block *data* download; local Python computation
> ports straight across.
>
> Verified against branch diffs + source on 2026-06-15.

---

## Legend — notation used in this file

### Computation tags
| Tag | Meaning | Port action |
|---|---|---|
| 🟢 **LOCAL** | Pure-Python math (geopandas/shapely/rasterio/numpy) — no `ee.*` in the math | Straight port to OCaml |
| ☁️ **GEE** | Server-side Earth Engine math (`ee.*`) | Reimplement in OCaml from `formuale.md`; feed per-block GEE export; verify against GEE oracle |
| 🔌 **GEE-IO** | GEE used only to read/clip/export *data* (no real math) | Keep as a thin per-block download |
| — **none** | No computation of that kind on the branch | — |

### GEE operators (what each `ee.*` call does)
| Operator | Meaning |
|---|---|
| `.filterBounds(roi)` | Keep features/images that intersect the region of interest |
| `.filterDate(a,b)` | Keep images in the time window |
| `.clip(roi)` | Crop a raster to the region |
| `.reduce(ee.Reducer.sum())` / `.sum()` | Pixel-wise **sum** across an image collection (e.g. total rainfall) |
| `.reduce(ee.Reducer.mode())` | Most-frequent value per pixel (e.g. annual LULC composite) |
| `.mean()` | Temporal/collection **mean** per pixel |
| `.expression("…")` | Per-pixel **arithmetic** — this is where most formulae live |
| `.where(cond, val)` | Conditional per-pixel assignment (classification / branching) |
| `.reduceRegion(reducer, …)` | Aggregate a raster over **one** polygon → a number/dict (zonal stat) |
| `.reduceRegions(fc, reducer, scale)` | Aggregate a raster over **many** polygons → per-feature value |
| `reduceNeighborhood` / focal mean (kernel) | Neighbourhood average over a moving window (used for TPI) |
| `ee.Terrain.slope(dem)` | Slope (degrees) from a DEM |
| `ee.Image.pixelArea()` | Per-pixel **area** raster in m² (used for area sums) |
| `ee.Join.saveFirst` / `saveAll` | Relational **join** between two FeatureCollections (1→1 / 1→many) |
| `.normalizedDifference([a,b])` | `(a−b)/(a+b)` — used for NDVI, NDWI, MNDWI |
| `.reproject(crs, scale)` | Force a CRS + pixel scale |
| `.unmask(v)` | Replace masked (no-data) pixels with `v` |
| `.getInfo()` | Pull a server-side result down to the Python client (becomes GeoJSON) |
| `Export.image/table.to*` | Write result to a GEE asset / Drive / Cloud Storage |

### Datasets (full forms + source links)

> Links point to the **GEE Earth Engine Data Catalog** page (canonical landing page) or the
> data provider. Exact GEE asset IDs/versions can differ per collection — confirm the
> precise ID in the catalog when you wire up a per-block download. The two marked
> **custom/internal** have no public catalog page (they're project-uploaded GEE assets).

| Short | Full form / use | Source link |
|---|---|---|
| **JAXA GSMaP** (`JAXA_PPT`) | JAXA Global Satellite Mapping of Precipitation — hourly rainfall | [GEE: JAXA/GPM_L3/GSMaP](https://developers.google.com/earth-engine/datasets/catalog/JAXA_GPM_L3_GSMaP_v6_operational) · [JAXA GSMaP](https://sharaku.eorc.jaxa.jp/GSMaP/) |
| **FLDAS** | NASA Famine Early Warning Land Data Assimilation System — evapotranspiration | [GEE: NASA/FLDAS](https://developers.google.com/earth-engine/datasets/catalog/NASA_FLDAS_NOAH01_C_GL_M_V001) |
| **MODIS** | Moderate Resolution Imaging Spectroradiometer — NDVI/NDWI/ET/PET | [GEE MODIS catalog](https://developers.google.com/earth-engine/datasets/catalog/modis) · NDVI [MOD13Q1](https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD13Q1) · ET/PET [MOD16A2](https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD16A2) |
| **CHIRPS** | Climate Hazards Group InfraRed Precipitation w/ Stations (1981→) — SPI baseline | [GEE: UCSB-CHG/CHIRPS/DAILY](https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHG_CHIRPS_DAILY) · [CHC CHIRPS](https://www.chc.ucsb.edu/data/chirps) |
| **SRTM / FABDEM** | Shuttle Radar Topography Mission / Forest-And-Buildings-removed DEM (elevation) | SRTM [GEE: USGS/SRTMGL1_003](https://developers.google.com/earth-engine/datasets/catalog/USGS_SRTMGL1_003) · FABDEM [GEE (sat-io)](https://gee-community-catalog.org/projects/fabdem/) · [FABDEM source (U. Bristol)](https://data.bris.ac.uk/data/dataset/25wfy0f9ukoge2gs7a5mqpq2j7) |
| **Dynamic World** | Google/WRI 10 m near-real-time land cover | [GEE: GOOGLE/DYNAMICWORLD/V1](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1) · [dynamicworld.app](https://dynamicworld.app) |
| **CGWB** | Central Ground Water Board — principal-aquifer polygons (specific yield) | **custom/internal** GEE asset · provider [cgwb.gov.in](http://cgwb.gov.in/) |
| **GLC-FCS30D** | Global Land Cover, Fine Classification 30 m, Decadal — forest cover series | [GEE (sat-io)](https://gee-community-catalog.org/projects/glc_fcs/) |
| **Landsat-7/8, Sentinel-2** | Optical satellite imagery (NDVI harmonization, ET downscaling) | [GEE Landsat](https://developers.google.com/earth-engine/datasets/catalog/landsat) · [GEE Sentinel-2 SR](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED) |
| **GLDAS** | Global Land Data Assimilation System — reanalysis (ET downscaling) | [GEE: NASA/GLDAS/V021/NOAH](https://developers.google.com/earth-engine/datasets/catalog/NASA_GLDAS_V021_NOAH_G025_T3H) |
| **MOD17 BPLUT** | MODIS GPP/NPP Biome Properties Look-Up Table (ET downscaling) | GPP [GEE: MOD17A2H](https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD17A2H) · [BPLUT / NTSG MOD17](https://www.ntsg.umt.edu/project/modis/mod17.php) |
| **IndiaSAT** | India-specific LULC classification (tree-health change fusion) | **custom/internal** GEE asset (no public catalog page) |

### Math symbols (full definitions in `formuale.md`; summarised here)
| Symbol | Meaning (units) |
|---|---|
| $P$ | Precipitation (mm) |
| $Q$ | Surface runoff (mm) — SCS-CN |
| $ET$ / $PET$ | (Potential) Evapotranspiration (mm) |
| $\Delta G$ / $G$ | Net groundwater recharge / cumulative storage (mm) |
| $S_y$ / $wd$ | Aquifer specific yield (fraction) / well-depth fluctuation (m) |
| $CN_2,CN_{2a},…$ | (SCS) Curve Numbers; AMC = Antecedent Moisture Condition |
| $S$ (`sr`), $M$ | Potential max retention / antecedent moisture amount (mm) |
| $VCI,MAI,SPI$ | Vegetation Condition / Moisture Adequacy / Standardized Precipitation Index |
| $NDVI,NDWI$ | Normalized Difference Vegetation / Water Index |
| $DD$ | Drainage density; $TPI$ = Topographic Position Index |
| $CI$ | Cropping Intensity (1–3); $rp$ = recharge potential; $RIF$ = Recharge Infiltration Factor |
| $sp$ | Slope percentage; LULC = Land Use/Land Cover; MWS = Microwatershed; ROI = Region of Interest |
| CRS | EPSG:4326 (WGS84 lat/lon, degrees) · EPSG:7755 (projected, metres — for lengths/areas) |

---

## Branch 0 — `main` (the core production pipeline)

This is where the bulk of GEE computation lives. Local post-processing is minimal; almost
all the math in `formuale.md §1–3, 5–11` runs server-side here.

**🟢 LOCAL computation**
- **Drainage density** (`computing/clart/drainage_density.py`): after pulling MWS + drainage
  lines via `.getInfo()`, reproject to **EPSG:7755**, `gpd.clip`, sum stream length by
  `ORDER`, apply influence factors → `DD` (geopandas/shapely). *(formuale §4)*
- **Rasterize vector** (`computing/clart/rasterize_vector.py`): burn an attribute (e.g. `DD`)
  into a 30 m GeoTIFF (`rasterio.features.rasterize`, affine `from_origin`). *(no GEE)*
- **Format/geometry utils** (`computing/utils.py`): GeoJSON↔shapefile↔gpkg, `buffer(0)`
  validity fix, point-in-polygon settlement↔MWS joins.

**☁️ GEE computation**
- **Hydrology / water budget** (`computing/mws/*`): `JAXA_PPT.filterDate().reduce(sum)` → $P$;
  FLDAS `.expression("ET>0?86400*ET:0")` → $ET$; Dynamic World `.reduce(mode)` + soil →
  $CN_2$ lookup via `.expression`/`.where`, slope-adjust via `ee.Terrain.slope`, the SCS-CN
  chain (`CN_3,CN_{2a},CN_{1a},CN_{3a}, sr, M, Q`) all in `.expression`, then
  $\Delta G=P-Q-ET$, cumulative $G$, and $wd=\Delta G/(S_y\cdot1000)$ with `ee.Join`/
  `reduceRegions` over CGWB aquifers. *(formuale §1)*
- **CLART** (`computing/clart/clart.py`): `sp = tan(slope·π/180)·100`; `rp = dd·lin·lith`
  scores; recommendation classes — all `.expression`/`.where`. *(formuale §3)*
- **Drought** (`computing/drought/generate_layers.py`): MODIS `.normalizedDifference` →
  NDVI/NDWI; $VCI=\min(…)\cdot100$, $MAI=ET/PET\cdot100$, $SPI=(P-\mu)/\sigma$ over CHIRPS
  baselines; per-MWS via `reduceRegions`. *(formuale §2)*
- **LULC / terrain / cropping / SWB / change / tree-health** (`computing/lulc`, `terrain_descriptor`,
  `cropping_intensity`, `surface_water_bodies`, `change_detection`, `tree_health`):
  classification, TPI focal means, area sums via `ee.Image.pixelArea()`, K-means cluster
  assignment, transition matrices — all `ee.*`. *(formuale §5–11)*

---

## Branch 1 — `feature/local-compute-station` ⭐ (porting reference)

The branch that **already moved the GEE math to local Python** — the trusted read-only
reference (IIT-D confirmation pending) for what the OCaml port should reproduce.

**🟢 LOCAL computation**
- The `*_local.py` suite rewrites Branch-0 GEE math with rasterio/numpy/geopandas over
  **downloaded base rasters**: `lulc_v3_local.py`, `lulc_vector_local.py`,
  `terrain_*_local.py` (TPI, 11-class landforms, K-means clusters), `change_detection_*_local.py`,
  `cropping_intesity_local.py`, `aquifer_vector_local.py`,
  `lulc_on_{plain,slope}_cluster_local.py`, soil-health, SPEI drought (+ an R script).
- `local_compute_helper.py` (~870 lines): shared clip / mask / zonal-stat / push helpers.
- `config.yaml` + `config_loader.py`: path/dataset resolution (no GEE in the math).

**☁️ GEE computation**
- Residual: whatever hydrology / MWS-delineation / drainage inputs the branch still pulls
  from GEE assets. The *math* of the ported modules is GEE-free.

---

## Branch 2 — `feature/local_compute_by_shiv` 📦 (parts bin)

**🟢 LOCAL computation**
- Broadest algorithm coverage made local: DEM analysis, drainage density/lines, catchment
  delineation, river/canal layers, slope, facilities proximity, natural depression,
  restoration, SOGE, MWS centroid + connectivity, mining, green-credit — geopandas/rasterio.
  ⚠ hardcoded paths; mine algorithms, don't base on it.

**☁️ GEE computation**
- Some modules still read inputs from GEE assets (data pulls), not core math.

---

## Branch 3 — `making_terrain_local`

**🟢 LOCAL computation**
- 100% local: terrain clustering (TPI → 11-class landforms → 4-cluster K-means with fixed
  centroids), LULC×terrain, cropping intensity, aquifer yield, change-detection vectors —
  rasterio + geopandas over FABDEM + LULC + watershed GeoPackages. *(formuale §5–6, 11)*

**☁️ GEE computation**
- None. (An earlier/parallel cut of Branch 1's terrain work.)

---

## Branch 4 — `feature/dem_excel_and_filter`

**🟢 LOCAL computation**
- 100% local: pandas/openpyxl aggregation + filtering of KYL (Know-Your-Landscape) MWS
  indicators into Excel; data fetched from GeoServer WFS. (Tabular, minimal geo-math.)

**☁️ GEE computation**
- None.

---

## Branch 5 — `feature/ndvi_timeseries_data_in_excel`

**🟢 LOCAL computation**
- 100% local: parse `NDVI_<year>` JSON properties from GeoServer WFS layers → time-series
  Excel (pandas/openpyxl). (The NDVI values themselves were computed upstream — Branch 14/15.)

**☁️ GEE computation**
- None.

---

## Branch 6 — `feature/mws_intersects_swb`

**🟢 LOCAL computation**
- Vector overlay: intersect MWS × SWB per village, count/area aggregation
  (shapely/pandas in `village_indicators.py`); inputs via GeoServer WFS → Excel sheet.

**☁️ GEE computation**
- None. (A clean polygon-overlay workload — good early GEOS-binding exercise.)

---

## Branch 7 — `feature/forest_additionality` (hybrid)

**🟢 LOCAL computation**
- The modeling half (~2000 LOC, gdal/scipy/numpy): download GLC-FCS30D forest maps from GCS
  → afforestation **mask** (numpy classification), **vulnerability map** (risk raster ops),
  random-forest / MCT classifiers, area-estimate CSV.

**☁️ GEE computation**
- Forest-cover map preparation: filter GLC-FCS30D by state, threshold forest/non-forest,
  export pre/mid/post maps to GCS (`Export.image`). Simple — reproducible from downloaded tiles.

---

## Branch 8 — `features/swb_catchment_area_fix`

**🟢 LOCAL computation**
- One-line `geometry.buffer(0)` validity fix in `computing/utils.py` (affects both paths).
  Not a computation to port — but note `buffer(0)` is a primitive the OCaml core must provide.

**☁️ GEE computation**
- None new.

---

## Branch 9 — `feature/mws_connectivity_pipeline`

**🟢 LOCAL computation**
- None today.

**☁️ GEE computation**
- Pure **spatial join**: `India_mws_connectivity.filterBounds(roi)` → `ee.Join.saveFirst`
  with local MWS ROI → attach watershed UID. → OCaml as GEOS `intersects` + attribute copy
  on a downloaded pan-India vector. *(no raster math — easiest GEE-side port)*

---

## Branch 10 — `features/dem_river_canal_pipeline`

**🟢 LOCAL computation**
- None today.

**☁️ GEE computation**
- River + Canal pan-India assets `.filterBounds(roi)` → `ee.Join.saveAll` (all watersheds
  each line crosses) → one feature per intersecting watershed + uid/area. → OCaml as a
  one-to-many spatial join (GEOS).

---

## Branch 11 — `feature/dem-canal-feature`

**🟢 LOCAL computation**
- None today.

**☁️ GEE computation**
- Refined canal clipping: 4-case conditional logic (single-match / multi-match /
  inside-outer-boundary-no-match / outside-ROI) with `.filterBounds`, conditional `.clip`,
  attribute carry. → OCaml as GEOS clip + the 4-case rules (contract care needed).

---

## Branch 12 — `feature/tree_health_pipeline_recompute`

**🟢 LOCAL computation**
- None today.

**☁️ GEE computation**
- Canopy-height series `CH_RASTER.filterBounds(roi).mean().clip(roi)` per year + CCD /
  overall-change masks ($\text{tree\_mask}=(LULC=6)$, IndiaSAT fusion). *(formuale §9)*
  → OCaml: trivial math; download only the one block's per-year rasters (rule 2).

---

## Branch 13 — `feature/ET_downscaling`

**🟢 LOCAL computation**
- None today (local code only submits/monitors GEE tasks).

**☁️ GEE computation**
- Monthly physics in `.expression`: AET, PET, GPP, RWDI, Kc, WUE (= GPP/AET) at 30 m, from
  Landsat-8 + GLDAS + MOD17 BPLUT + MCD12Q1 + AEZ → 13-band stacks. → OCaml: deterministic
  pixel physics (numpy → `owl`/Bigarray); download per-block monthly stacks (rule 2).

---

## Branch 14 — `feature/hls_ndvi`

**🟢 LOCAL computation**
- None today.

**☁️ GEE computation**
- Multi-sensor NDVI: per-sensor cloud masking (L7/L8/S2) → Chastain cross-sensor
  harmonization regression → `.normalizedDifference` NDVI → gap-filled time-series
  (`gapfilled_NDVI_lsc`). → OCaml: cloud-mask + regression; largest per-block download.

---

## Branch 15 — `features/wb_ndvi`

**🟢 LOCAL computation**
- None today.

**☁️ GEE computation**
- ZOI NDVI: `.reduceRegions` of the Branch-14 harmonized NDVI over ZOI features → per-feature
  `NDVI_<year>` series. → OCaml: **zonal statistics** (raster × polygons) once NDVI rasters
  exist locally; chains behind Branch 14.

---

## At-a-glance: where the GEE computation is

| Branch | 🟢 LOCAL math | ☁️ GEE math | Port note |
|---|---|---|---|
| 0 `main` | drainage density, rasterize, utils | hydrology, CLART, drought, LULC, terrain, … | the bulk of GEE math |
| 1 local-compute-station | the `*_local.py` suite | residual data pulls | porting reference |
| 2 by_shiv | DEM/drainage/catchment/connectivity algos | some data pulls | parts bin |
| 3 making_terrain_local | terrain/LULC×terrain/cropping/aquifer | none | 100% local |
| 4 dem_excel_and_filter | Excel/KYL aggregation | none | tabular |
| 5 ndvi_timeseries_excel | NDVI→Excel | none | tabular |
| 6 mws_intersects_swb | MWS×SWB overlay | none | GEOS overlay |
| 7 forest_additionality | ML risk/vulnerability rasters | forest-map prep/export | hybrid |
| 8 swb_catchment_area_fix | buffer(0) fix | none | trivial |
| 9 mws_connectivity | none | spatial join | easiest GEE port |
| 10 dem_river_canal | none | 1→many spatial join | medium |
| 11 dem-canal-feature | none | 4-case clip join | medium–hard |
| 12 tree_health_recompute | none | clip + mean + masks | data-heavy |
| 13 ET_downscaling | none | monthly physics (13-band) | data-heavy |
| 14 hls_ndvi | none | cloud-mask + harmonization | hardest |
| 15 wb_ndvi | none | zonal stats | chains on 14 |

> Reading order for the port (`convert.md`): local-only branches first (3, 4, 5, 6 + the
> Branch-1 suite), then vector-only GEE joins (9, 10, 11), then the data-heavy GEE raster
> math (12, 13, 15, 14). Branch 0's hydrology/CLART/drought are the core target, ported
> from `formuale.md` and verified against GEE per block.

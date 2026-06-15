# Exploring `geocaml` — OCaml geospatial libraries for our port

> Org: **https://github.com/geocaml** — a collection of (mostly pure-)OCaml geospatial
> libraries. Surveyed all **22 repos** on 2026-06-15 for use in the CoRE Stack →
> OCaml conversion (`convert.md`, `branches-flow-connection.md`, `gee-computations.md`).
>
> **Honest headline:** geocaml is the single most relevant ecosystem for this project — it
> already has the I/O and CRS pieces we need. BUT most repos are **early-stage / WIP**
> (low commit counts, "experimental", not released to opam — need pinning), and there is
> **one critical gap: no geometry-operations binding** (no `ocaml-geos`; the `geo` repo
> does *not* cover clip/intersection/length/area/buffer). That gap is the crux of our
> Phase-2 stack decision (see end). Adopting geocaml realistically means **using its mature
> bits + contributing to its WIP bits** — which fits our open-source motive (`motive.md`).

---

## How to read "Impact %"

**Impact % = our estimate of how much of the OCaml-port effort this repo could cover or
accelerate *if adopted*** — weighting (a) relevance to the calculations we must port and
(b) how reusable it is across the 15 branches. It is **not** a guarantee of fit; pair it
with **Readiness** (current usability), which is separate. A high-impact / low-readiness
repo (e.g. `ocaml-gdal`) means "central, but we'd have to harden it / contribute."

**Readiness scale:** 🟢 usable now · 🟡 functional but early (pin, expect gaps) ·
🔴 WIP/experimental (few commits, may not build for our use).

---

## Tier 1 — Core to the port (high impact)

### 1. `ocaml-geojson` — 42★ 🟢🟡
- **Speciality:** parse / create / manipulate **GeoJSON** (RFC 7946). Most-starred geo lib here.
- **Our usage:** GeoJSON is *the* interchange format across the whole pipeline — GEE
  `getInfo()` → features, admin boundaries, MWS/drainage/SWB vectors, `public_api` outputs.
  This is the ingest/emit layer for nearly every branch.
- **How:** read downloaded per-block FeatureCollections, build OCaml geometry records, write
  results back as GeoJSON for GeoServer. Replaces `gpd.GeoDataFrame.from_features` / `to_json`.
- **Impact: ~85%** — touches almost every branch's I/O.

### 2. `ocaml-proj` — 1★ 🟡
- **Speciality:** bindings to **PROJ** — coordinate reference system **reprojection**
  (`proj.opam`, `proj_c.opam`, `proj_js.opam`; 17 commits).
- **Our usage:** the mandatory **EPSG:4326 ↔ EPSG:7755** transform before every length/area
  measurement (drainage density, areas, all metric math — see `formuale.md` §4, §13).
  Without correct reprojection, every length/area is wrong.
- **How:** wrap as the `geo.reproject` primitive in `corestack-geocompute`; transform
  coordinates before `length`/`area`. Directly satisfies `convert.md` Phase-2 "PROJ via ctypes."
- **Impact: ~70%** — a hard dependency for all metric outputs; early but functional.

### 3. `ocaml-gdal` — 5★ 🔴
- **Speciality:** **GDAL** bindings (raster + vector I/O, and OGR — which itself wraps GEOS
  for geometry ops). Marked **"WIP and Highly Experimental"**, only 4 commits, `gdal.opam` present.
- **Our usage:** *potentially the backbone* — read/write GeoTIFF, read shapefiles/GeoPackage,
  and (via OGR) the geometry operations we otherwise lack. This is the repo that, if matured,
  closes the geometry-ops gap.
- **How:** read base rasters (LULC/DEM/rainfall exports), write layer GeoTIFFs, do OGR
  clip/intersection. **Realistically we'd contribute to it** to reach the needed coverage.
- **Impact: potential ~80% / current ~20%** — highest leverage, lowest readiness.

### 4. `yirgacheffe` — 2★ 🔴
- **Speciality:** OCaml port of the Python **yirgacheffe** raster **map-algebra** library
  (lazy, windowed raster arithmetic over aligned layers). Depends on `ocaml-gdal` +
  `ocaml-tiff` submodules. WIP, 3 commits.
- **Our usage:** conceptually *exactly* our cloud-math need — the hydrology/drought/LULC
  pixel arithmetic (`formuale.md` §1–2, §5–11) is map-algebra over aligned rasters, which is
  what yirgacheffe expresses. The Python yirgacheffe is a proven model for this style.
- **How:** if it matures, our SCS-CN / VCI / MAI / change-detection layers become readable
  raster expressions instead of hand-rolled `Bigarray` loops.
- **Impact: potential ~75% / current ~15%** — the most strategically aligned repo, earliest stage.

---

## Tier 2 — Strong supporting role (medium impact)

### 5. `ocaml-rtree` — 26★ 🟢🟡
- **Speciality:** pure-OCaml **R-tree** spatial index.
- **Our usage:** accelerates **spatial joins / overlays** — MWS×SWB (Branch 6), MWS
  connectivity (Branch 9), river/canal joins (Branches 10–11), drainage-line clipping
  (Branch 0). Replaces the implicit indexing geopandas/GEE do for us.
- **How:** index the candidate set, query by bbox, then do exact predicate on hits.
- **Impact: ~45%** — central to all the vector-join branches; mature enough to rely on.

### 6. `ocaml-tiff` — 18★ 🟡 (read-only)
- **Speciality:** pure-OCaml **TIFF reader** (BYO IO, e.g. Eio). 145 commits — the most
  active repo. **Read-only**, and **no documented GeoTIFF geo-referencing tags / affine /
  writing** — and not yet released (pin to use).
- **Our usage:** reading raster *inputs* without a GDAL dependency. **Gap:** we also need to
  *write* GeoTIFF outputs **with** CRS + affine transform (for `rasterize_vector` and every
  raster layer) — ocaml-tiff doesn't document that yet.
- **How:** input reading now; for georeferenced writing, either extend ocaml-tiff (contribute
  GeoTIFF tags) or use `ocaml-gdal`.
- **Impact: ~40%** — covers raster *reads*; the write+georef half is unmet here.

### 7. `pruck` — 4★ 🔴
- **Speciality:** typed **dataframe** (CSV read, typed columns; Parquet WIP). 6 commits,
  experimental. **No groupby/join/filter/aggregation documented yet.**
- **Our usage:** the **attribute-table** side of geopandas — per-MWS/village stats, zonal-stat
  result tables, the Excel/stat pipelines (Branches 4, 5; the `*_vector` area tables).
- **How:** hold per-feature computed values, export to CSV. Aggregation we'd add ourselves.
- **Impact: ~35%** — useful for the tabular outputs; immature, missing the aggregation verbs.

### 8. `ocaml-wkt` — 7★ 🟡
- **Speciality:** non-blocking **Well-Known Text** codec.
- **Our usage:** geometry interchange/debugging, and the bridge to **PostGIS** (geoadmin
  lat/lon lookups speak WKT/EWKB) if we ever round-trip geometries to the DB.
- **Impact: ~20%** — handy glue, not on the critical path.

---

## Tier 3 — Marginal / situational (low impact)

| Repo | ★ | Speciality | Our usage | Impact |
|---|---|---|---|---|
| `ocaml-topojson` | 11 | TopoJSON codec | CoRE Stack uses GeoJSON/shp, not TopoJSON — only if a frontend wants compact topology | ~10% |
| `ocaml-parquet` | 0 | Parquet format | Optional columnar export for large stat tables (pairs with `pruck`) | ~10% |
| `ocaml-arrow` | 0 | Apache Arrow | Same — in-memory columnar; only if stats get large | ~10% |
| `ocaml-optics` | 18 | Optics/lenses | Dev ergonomics for nested geometry/record updates — not geo-specific | ~10% |
| `osm_xml` | 0 | OSM XML parser | Marginal — admin boundaries here are SOI shapefiles, not OSM | ~5% |
| `ISO3166` | 9 | Country codes | Marginal — project is India-only | ~5% |
| `ocaml-h3` | 0 | Uber H3 hex indexing | Not used by CoRE Stack (no hex grids) | ~5% |
| `geo` | 4 | Basic primitives (Coord, LineString, Chaikin smoothing) on Owl — **no CRS, no area/length/intersection/buffer** | Naming/structure inspiration only; **not** a GEOS substitute | ~15%* |
| `geo-uri` | 5 | `geo:` URI parser | Marginal | ~3% |

\* `geo`'s impact is "inspiration" not "leverage" — it's too minimal to do our geometry math.

## Tier 4 — Not relevant to this project (0%)

- `ocaml-las` (2★) + `laserver` (1★) — **LAS/LAZ point-cloud / LiDAR**. CoRE Stack has no LiDAR.
- `carbon-intensity` (25★) — carbon-intensity **HTTP API client**. Unrelated domain.
- `ocaml-wav` (7★) — **WAV audio**. Unrelated.
- `.github` — org meta files.

---

## The critical gap — geometry operations (GEOS)

The calculations need **clip / intersection / length / area / buffer(0) / point-in-polygon**
(drainage density, MWS×SWB, river/canal joins, geometry-validity fixes). **geocaml has no
GEOS binding**, and `geo` doesn't cover these. Three ways to close it:

1. **`ocaml-gdal`'s OGR** — OGR wraps GEOS, so a matured `ocaml-gdal` gives both I/O *and*
   geometry ops in one dependency. Best long-term; needs hardening (we'd contribute).
2. **Bind GEOS directly via ctypes** — our `convert.md` Phase-2 fallback; keeps numeric parity
   with shapely (which also wraps GEOS), so verification stays tight.
3. **Native OCaml** for the few simple ops (length/area on reprojected coords are trivial;
   only clip/intersection genuinely need GEOS).

---

## Recommended stack (what to actually adopt)

| Need | Primary | Fallback |
|---|---|---|
| GeoJSON I/O (interchange) | **`ocaml-geojson`** 🟢 | — |
| CRS reprojection (4326↔7755) | **`ocaml-proj`** 🟡 | ctypes→libproj |
| Raster read/write + vector I/O | **`ocaml-gdal`** 🔴 (contribute) | `ocaml-tiff` (read) + ctypes→libgdal |
| Raster map-algebra (cloud math) | **`yirgacheffe`** 🔴 (contribute) | `Owl`/`Bigarray` directly |
| Geometry ops (clip/area/length/buffer) | `ocaml-gdal` OGR 🔴 | **ctypes→libgeos** |
| Spatial index for joins | **`ocaml-rtree`** 🟢 | brute-force bbox |
| Attribute tables / stats | `pruck` 🔴 + `ocaml-parquet` | plain records + CSV |
| WKT / PostGIS bridge | `ocaml-wkt` 🟡 | — |

**Bottom line for our project:** geocaml gets us the **I/O + CRS + indexing** layer largely
for free (`ocaml-geojson`, `ocaml-proj`, `ocaml-rtree`), and gives us a **head start +
upstream-contribution target** for the hard parts (`ocaml-gdal`, `yirgacheffe`). The
**geometry-ops gap** is the one thing geocaml does not hand us — resolve it via OGR
(mature ocaml-gdal) or a direct ctypes GEOS binding. This refines `convert.md` Phase 2:
*don't bind everything from scratch — adopt geocaml where it's ready, contribute where it's
not, and only hand-bind GEOS if ocaml-gdal/OGR isn't ready when we need it.*

See `Geocml-usage-on-15-Branches.md` for the per-branch repo mapping.

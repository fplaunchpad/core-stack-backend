# Procedure: Port the local geospatial-calculation layer from Python → OCaml

> Goal: extract the **local** geospatial calculations (the Python code that produces the
> vectors/rasters published to GeoServer) into a **standalone, open-source OCaml
> component**, while leaving GEE (cloud) and the GeoServer publish step as thin boundaries.
> Read `branches-flow-connection.md` first — it explains the data flow, the
> cloud-vs-local split, and why `feature/local-compute-station` is the base branch.

---

## Operating principles — the GEE policy

Three rules govern how this port treats Google Earth Engine. **They override any
"leave it in GEE" / "keep in Python" language elsewhere in the older notes.**

1. **GEE computation → avoid entirely; reimplement in OCaml.** You **cannot** send OCaml
   to GEE — GEE only runs its own server-side operators, so there is no "delegate the hard
   math to the cloud" option. Every GEE *calculation* (`ee.Image` algebra,
   `ee.Terrain.slope`, `ee.Image.pixelArea`, focal kernels, reducers, `reduceRegions`)
   becomes OCaml that runs locally. The formulas in `formuale.md` are the spec.
2. **GEE data → download only, scoped to one block.** Use GEE purely as a data *source*,
   and pull only the inputs for the single region/tehsil/block you are working on — a few
   MB of clipped GeoTIFF / GeoJSON per layer. No pan-India downloads, no standing ingestion
   pipeline. The download is a thin step at the edge; everything after it is OCaml.
3. **Local computation → OCaml, no-brainer.** The existing local Python math (`*_local.py`,
   geopandas/rasterio/shapely) ports straight across — no GEE involved.

### Rule 2 in practice — the per-block golden-file workflow

For any module that used GEE computation, do this per test block (e.g. `assam/baksa/baksa`):

- **Export its inputs from GEE once**, clipped to the block, at the pipeline's CRS/scale
  (`EPSG:4326`, `scale=30`) — the rainfall sums, soil raster, DEM/slope, LULC composite, etc.
- **Export GEE's own output** for the same block — this is your **oracle** (expected answer).
- **Run the OCaml reimplementation** on the downloaded inputs and diff against the oracle
  (Phase 4). Aligning CRS, scale, and pixel grid on export is mandatory — otherwise GEE's
  on-the-fly resampling shows up as phantom numerical error.

Each formula validated this way is then provably correct when you later swap the input
source from "GEE export" to "direct provider download" — same fixtures, different supplier.

## Guiding principles (method)

1. **Verify numerically, layer by layer.** Each ported module must produce
   bit-or-tolerance-equal output (same GeoTIFF values / same shapefile attributes) versus
   the reference (Python local result, or the GEE oracle above) before it's considered
   done. Geospatial bugs are silent.
2. **One module at a time, easiest first.** Start with `rasterize_vector.py` (≈50 lines,
   no GEE, single clear contract), then `drainage_density.generate_vector`, then the
   `*_local.py` raster modules, then the GEE-computation modules (reimplemented per rule 1).
3. **Keep GeoServer as a boundary.** `utilities/geoserver_utils.py` is a REST client, not
   a calculation. Reimplement it in OCaml later (or keep calling it over HTTP) — it is not
   on the critical path for the math.

---

## Phase 0 — Set up the working branch

**Team decision (FPL #fpl-esg, 2026-06-13; Sanjay Karanth + Alina Banerjee):** work lives
on the **org repo** `fplaunchpad/core-stack-backend`, **not** a personal fork. Base off
**`main`** — Alina: *"working off `main` is far stabler than a remote branch [`dev`] in
which commits may or may not be updated."* Branch name uses the **`wip-feature-<name>`**
convention (other teams prefix with `feature`; `wip-feature` marks work-in-progress) →
**`wip-feature-ocaml-geocompute`**. The OCaml work is a new subdirectory.
`feature/local-compute-station` is used as a read-only **reference** for the already-local
Python modules — but its maintenance/trust status is **to be confirmed with the IIT-D
team** (Alina), so treat it as a porting hint, not gospel, until verified.

```bash
cd /home/snaveen/Desktop/core-stack-backend
git fetch origin
# Base the working branch on the org repo's main branch (stable; mergeable for upstream PR)
git checkout -b wip-feature-ocaml-geocompute origin/main
# Push as its OWN branch on the ORG repo (NOT shared main/dev), and track it
git push -u origin wip-feature-ocaml-geocompute
# The OCaml port lives in its own subdirectory, added on this branch
mkdir -p corestack-geocompute
```

Reading the porting reference without merging it (the `*_local.py` modules live on the
trusted `feature/local-compute-station` branch, which is stale — 212 commits behind main —
so we read, not merge):

```bash
git show origin/feature/local-compute-station:computing/local_compute_helper.py | less
git show origin/feature/local-compute-station:computing/clart/rasterize_vector.py | less
# config seam to mirror in corestack-geocompute:
git show origin/feature/local-compute-station:computing/config.yaml | less
git show origin/feature/local-compute-station:computing/config_loader.py | less
```

Why option (b) off `main`: basing on current `main` keeps the OCaml subproject mergeable
for the eventual upstream PR and on the most stable, up-to-date tree (Alina's point that
`dev` commits "may or may not be updated"). The local-compute math is pulled in by
*reading* the `*_local.py` files as the porting spec. See `branches-flow-connection.md` §6.

Deliverable: branch `wip-feature-ocaml-geocompute` on `origin` (org repo), based on
`main`, with an empty `corestack-geocompute/` subdirectory ready for the OCaml project.

---

## Phase 1 — Inventory and freeze the contracts

For each candidate module (start with the §4 list in `branches-flow-connection.md`),
write down its **contract** so the OCaml version can be checked against it:

For every target function record:

- **Inputs**: file formats + CRS (e.g. shapefile EPSG:4326, attribute column `DD`).
- **Operation**: the exact transform (reproject → clip → length-sum → formula).
- **Outputs**: file format, CRS, dtype, resolution, nodata/fill value.
- **Numeric formula**: copy it verbatim. Example, drainage density:
  `DD = total_length_stream_order_km × influence_factor × 100 / area_in_ha/100`,
  with `influence_factors = [60/385, 55/385, …, 10/385]` for stream orders 1–11.

Priority order (lowest risk first):

1. `computing/clart/rasterize_vector.py` — vector+attribute → 30 m GeoTIFF.
2. `computing/clart/drainage_density.py::generate_vector` — the DD math.
3. `computing/utils.py` format/geometry helpers (geojson↔shp↔gpkg, `buffer(0)`).
4. `plans/build_layer.py` — CSV lat/lon → point shapefile.
5. The `*_local.py` raster modules (lulc, terrain, change detection) — larger, do last.

Deliverable: `myref/contracts/<module>.md` per module (or one table), plus a saved
reference output for each (the Python result on a fixed test tehsil, e.g. assam/baksa/baksa).

---

## Phase 2 — Choose the OCaml geospatial stack

The OCaml core must cover the primitives in `branches-flow-connection.md` §4. Candidate
libraries (verify availability/maturity when you start — confirm via opam, don't assume):

| Need                                           | OCaml option(s)                                         | Fallback                           |
| ---------------------------------------------- | ------------------------------------------------------- | ---------------------------------- |
| GeoTIFF / raster I/O                           | bindings to GDAL (`ocaml-gdal` if maintained)         | FFI to libgdal via `ctypes`      |
| Vector I/O (shp/gpkg/geojson)                  | GDAL/OGR via the same binding;`geojson` libs for JSON | shell out to `ogr2ogr` initially |
| CRS reprojection                               | PROJ via `ctypes` FFI to libproj                      | precompute transforms              |
| Geometry ops (clip, length, area, buffer, PIP) | a GEOS binding via `ctypes` to libgeos                | port simple ops natively           |
| Numeric arrays / raster math                   | `owl` (numpy-equivalent)                              | `bigarray`                       |
| YAML config (mirror config.yaml)               | `yaml` (ocaml)                                        | `ezjsonm`                        |

Recommended pragmatic path: **wrap the C libraries everyone already uses (GDAL, GEOS,
PROJ) via OCaml `ctypes`**, rather than reimplementing geometry algorithms. This keeps
numeric results identical to the Python/`shapely`/`rasterio` reference (which also wrap
GEOS/GDAL), so Phase 4 verification can hit tight tolerances.

Deliverable: a new opam project (e.g. `corestack-geocompute/`) with a `dune-project`,
a `geo` library exposing read/write/reproject/clip/length/area/rasterize, and a smoke
test that round-trips a GeoJSON.

---

## Phase 3 — Port module by module

For each module, in priority order:

1. **Implement** the OCaml equivalent behind a small CLI (e.g.
   `corestack-geocompute rasterize --in x.shp --col DD --out x.tif --res 0.000278`).
   Keep the same resolution/transform/fill conventions as the Python
   (`from_origin(minx, maxy, res, res)`, `all_touched=true`, fill `0`, dtype float32).
2. **Match the formula exactly.** For drainage density: same EPSG:7755 reprojection for
   length, same per-stream-order loop, same influence factors, same `/1000` km and
   `/100` ha conversions.
3. **Keep the file contract identical** (CRS tags, band count, nodata) so downstream
   `geoserver_utils.py` can publish the OCaml output unchanged.

Deliverable: one OCaml CLI subcommand (or library function) per ported module.

---

## Phase 4 — Verify against the Python reference

This is the gate; do not skip it.

1. Run the Python module and the OCaml module on the **same fixed input** (the test
   tehsil from Phase 1).
2. Compare outputs:
   - **Rasters**: compare with `gdalcompare.py` or read both with rasterio and assert
     `np.allclose(a, b, atol=…)`; check shape, transform, CRS, nodata.
   - **Vectors**: compare attribute tables (e.g. `DD`, `str_len_km`) within tolerance and
     geometry equality (GEOS `equals_exact` with a small tolerance).
3. Record max/mean absolute difference per layer. Tighten until differences are
   floating-point noise (CRS/clip ops on the same GEOS/GDAL should match closely).

Deliverable: a `verify/` script + a results table (module → max abs diff → pass/fail).

---

## Phase 5 — Wire the OCaml core back into the pipeline

You have two integration options; pick per how much you want to keep in Python short-term:

- **Option A (subprocess, lowest risk):** the Celery task in
  `computing/<theme>/<script>.py` shells out to the OCaml CLI for the math step, then
  hands the produced file to the existing `utilities/geoserver_utils.py` publish step.
  Minimal Django changes; the config seam from `feature/local-compute-station` already
  resolves the paths.
- **Option B (service):** expose the OCaml core as a small HTTP service; Django calls it.
  Better for the standalone open-source story, more work.

Keep in Python only the **thin edges**: the per-block GEE *data download*
(`utilities/gee_utils.py` reduced to an exporter — rule 2, no computation) and the
PostgreSQL metadata writes (`computing/utils.py::save_layer_info_to_db`). These are
integration glue, not geospatial math. **The GEE computation those tasks used to trigger
is gone — it now runs in OCaml (rule 1).**

Deliverable: at least one end-to-end layer (drainage density recommended) generated
through the OCaml core and published to a local GeoServer, matching the Python output.

---

## Phase 6 — Package as a standalone open-source component

1. **Extract** the OCaml project (`corestack-geocompute/`) into its own repository, with
   no Django dependency — it reads inputs from disk (paths/config mirroring
   `computing/config.yaml`) and writes standard GeoTIFF/shapefile/GeoPackage.
2. **License**: match or stay compatible with the parent repo's `LICENSE`; add
   `LICENSE`, `README`, and a `CONTRIBUTING` to the new repo.
3. **Document the contracts** ported in Phase 1 as the public API spec.
4. **CI**: build via dune + run the Phase 4 verification fixtures as regression tests.
5. **Reference it back** from core-stack-backend (git submodule, opam pin, or released
   binary) so the Django app consumes it as Option A/B above.

Deliverable: a public OCaml repo that, given the same base layers, reproduces the local
geospatial layers CoRE Stack currently computes in Python.

---

## Scope — what the OCaml core replaces vs. what stays at the edge

**Reimplement in OCaml (in scope — this is the whole point):**
- ✅ **All GEE computation** — `ee.Image` algebra, `ee.Terrain.slope`, `ee.Image.pixelArea`,
  focal kernels, reducers, `reduceRegions`. Per rule 1 none of this stays in GEE; it is
  reimplemented locally from the `formuale.md` spec and verified against the GEE oracle.
- ✅ All existing local Python geospatial math (`*_local.py`, geopandas/rasterio/shapely).

**Stays at the edge (thin; out of scope for the math core):**
- 🔌 The per-block GEE **data download** — a thin Python (or OCaml HTTP) exporter that pulls
  the clipped inputs for one block (rule 2). GEE the *data tap* stays; GEE the *calculator*
  goes.
- 🚫 `utilities/geoserver_utils.py` / `geoserver_styles.py` — REST publishing + SLD, not
  calculation. Keep as the publish boundary; port last, if at all.
- 🚫 Django models, Celery wiring, JWT/API-key auth, STAC catalog, DPR/ODK — application
  glue, out of scope for the math component.

## Suggested first commit

Port `computing/clart/rasterize_vector.py` to an OCaml CLI, verify byte/tolerance-equal
GeoTIFF on assam/baksa/baksa, and document the contract. It is the smallest fully-local,
GEE-free, single-responsibility module — the ideal proof of concept before tackling
drainage density and the larger `*_local.py` raster modules.

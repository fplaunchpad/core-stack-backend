# Literature & Learning Path — CoRE Stack + Python→OCaml Geospatial Port

> Companion to `branches-flow-connection.md` (architecture) and `convert.md` (procedure).
> This is the **literature-research roadmap**: what to read/watch, in what order, and how
> each item maps to a concrete part of this project.
>
> **Note on links:** book/doc names and channel names are stable; exact video URLs rot.
> Where I give a YouTube item, search the **title + channel** rather than a URL.

---

## How to use this document

Each phase has: **Goal → Required → Targeted readings → Videos → Checkpoint**.
"Required" = read/skim before moving on. "Targeted" = open when you hit that topic in
the code. The checkpoint tells you when you know enough — don't aim for mastery per
phase; aim to pass the checkpoint and move.

Suggested time allocation (part-time): Phases 1–2 ≈ 2 weeks, 3–4 ≈ 2 weeks,
5 ≈ 1 week, 6 ≈ 3–4 weeks (OCaml is the long pole), 7 ≈ ongoing.

---

## Phase 1 — GIS fundamentals (the domain language)

**Goal:** understand vector vs raster, CRS/projections, and why the code keeps
reprojecting EPSG:4326 ↔ EPSG:7755 before measuring lengths/areas.

**Required**
- *A Gentle Introduction to GIS* — free, part of the official QGIS documentation
  (docs.qgis.org). Short; covers vector, raster, CRS, map production. The single best
  zero-to-domain primer.
- *Essentials of Geographic Information Systems* (Campbell & Shin) — free/open textbook;
  chapters on data models and projections only.
- **epsg.io** — look up the actual CRSs in this repo: `EPSG:4326` (WGS84 lat/lon,
  degrees — never measure in it) and `EPSG:7755` (projected CRS for India, meters —
  that's why `drainage_density.py` converts before `.length`).

**Targeted readings**
- Wikipedia/PROJ docs on *map projections* and *geographic vs projected CRS* — enough to
  explain "degrees aren't meters".
- ESRI shapefile technical description (the format's quirks: 10-char field names, .shp/.shx/.dbf
  sidecars) — explains `computing/utils.py`'s zip-packaging dance.
- GeoJSON spec **RFC 7946** — short, readable; the interchange format used everywhere in
  this repo (GEE → `gpd.GeoDataFrame.from_features`).

**Videos**
- Search: **"Map projections explained" — Vox** ("Why all world maps are wrong") — intuition for projections in 6 min.
- Search: **"What is GIS" + "vector vs raster"** — any short explainer; you need the
  vocabulary, not depth.

**Checkpoint:** you can explain (a) why `drainage_lines.to_crs(7755)` precedes
`geometry.length.sum()`, and (b) what the six sidecar files of a shapefile are.

---

## Phase 2 — The Python geospatial stack (what you are porting *from*)

**Goal:** read any `*_local.py` module in `computing/` and name what each call does —
because your OCaml port must reproduce these semantics exactly.

**Required**
- **Shapely user manual** (shapely.readthedocs.io) — geometry objects, `buffer(0)` as
  the validity fix, predicates (`intersects`, `within`), set ops (clip = intersection).
- **GeoPandas user guide** (geopandas.org) — GeoDataFrame, `to_crs`, `clip`, spatial
  joins, file I/O. This is the API surface of most local calculations here.
- **Rasterio documentation** (rasterio.readthedocs.io) — especially the topics:
  *Georeferencing* (affine transforms — `from_origin(minx, maxy, res, res)` in
  `rasterize_vector.py`), *features.rasterize*, and dataset profiles (dtype, nodata, CRS
  tags). Your first OCaml port target is a translation of exactly these concepts.
- *Automating GIS Processes* — free University of Helsinki course
  (autogis-site.readthedocs.io). Hands-on geopandas/shapely; do the vector lessons.

**Targeted readings**
- *Geographic Data Science with Python* (Rey, Arribas-Bel, Wolf) — free online book;
  chapters on spatial data and spatial weights as needed.
- **GDAL/OGR documentation** (gdal.org) + the *GDAL/OGR Python cookbook* — because
  shapely/rasterio/geopandas are wrappers over **GEOS/GDAL/PROJ**, and `convert.md`
  Phase 2 recommends binding those same C libraries from OCaml. Understand the layering:
  `geopandas → fiona/pyogrio → GDAL/OGR`, `shapely → GEOS`, `rasterio → GDAL`,
  `pyproj → PROJ`.
- **JTS Technical Specifications** (Martin Davis) — only if you end up reimplementing a
  geometry op natively instead of binding GEOS; JTS is the algorithmic reference GEOS
  is ported from.

**Videos**
- Channel: **Spatial Thoughts (Ujaval Gandhi)** — free, structured "Python Foundation for
  Spatial Analysis" / geopandas materials; India-centric examples (matches this repo's domain).
- Search: **"GeoPandas introduction" — SciPy conference talks** (the maintainers' tutorial
  recordings are long but authoritative).
- Search: **"rasterio tutorial"** — any recent walkthrough covering transforms + rasterize.

**Checkpoint:** you can write, from scratch, a 20-line Python script that reads a
shapefile, reprojects it, clips it by a polygon, sums lengths, and rasterizes an
attribute to GeoTIFF — i.e., a miniature of `drainage_density.py` + `rasterize_vector.py`.
(This script becomes your Phase-4 verification oracle in `convert.md`.)

---

## Phase 3 — The backend architecture (Django, Celery, PostGIS)

**Goal:** trace a request through `computing/api.py → apply_async(queue="nrm") → Celery
worker → save_layer_info_to_db` without getting lost. You're not porting this layer, but
you must read it fluently to find the seams.

**Required**
- **Official Django tutorial** (docs.djangoproject.com, parts 1–5) — models, views, URLs.
- **Django REST Framework docs** — just *Views/ViewSets*, *Serializers*, *Authentication*
  (the repo uses JWT via SimpleJWT).
- **Celery docs: "First Steps with Celery" + "Next Steps"** — tasks, `apply_async`,
  queues/routing. Then re-read `nrm_app/celery.py` and one task like
  `computing/lulc/lulc_v3.py`.
- **RabbitMQ tutorials 1–2** (rabbitmq.com) — just enough AMQP to understand the broker.

**Targeted readings**
- **PostGIS documentation** (postgis.net) — intro + `geometry` type; the repo's
  `geoadmin` lat/lon lookups use it.
- *PostGIS in Action* (Obe & Hsu) — reference book if you go deeper into spatial SQL.
- **django-celery-beat docs** — the scheduled tasks in `gee_computing/tasks.py`.

**Videos**
- Search: **"Django Celery RabbitMQ tutorial" — Pretty Printed** or **Dennis Ivy** —
  short practical wiring videos matching this repo's exact stack.
- Search: **"PostGIS introduction" — Paul Ramsey (FOSS4G / Crunchy Data)** — Ramsey's
  talks are the canonical PostGIS intros; also his blog *cleverelephant.ca*.

**Checkpoint:** given any endpoint in `computing/urls.py`, you can find its task function,
say which queue it runs on, and list the side effects (GEE asset? GCS file? DB row?
GeoServer layer?).

---

## Phase 4 — GeoServer & OGC standards (the publish boundary)

**Goal:** understand what GeoServer does (serve, not compute) and the REST/OGC surface
that `utilities/geoserver_utils.py` wraps — so you keep it as a boundary, per `convert.md`.

**Required**
- **GeoServer User Manual** (docs.geoserver.org) — sections: *Web administration
  interface*, *Data management* (workspaces, stores, layers), *REST API*. Map each REST
  section to the corresponding method in `geoserver_utils.py` (workspace CRUD,
  coveragestore = raster, datastore/featurestore = vector).
- **OGC service concepts**: WMS (rendered map images) vs WFS (raw features) vs WCS
  (raw rasters). The GeoServer manual's overview pages suffice; skim the actual OGC
  specs only if needed.
- **SLD (Styled Layer Descriptor)** — GeoServer's *Styling* section. Then read
  `utilities/geoserver_styles.py` and see it's just SLD-XML templating.

**Targeted readings**
- **GeoSolutions GeoServer training** (free online training material by the GeoServer
  maintainers) — the structured deep-dive if the manual feels scattered.
- **STAC specification** (stacspec.org) — short spec; explains the `LayerMapping` model
  and `/stac/...` endpoints in `computing/api.py`.

**Videos**
- Search: **"GeoServer beginner tutorial"** and **"GeoServer REST API"** — practical
  walkthroughs of workspace/store/layer creation (what the Python wrapper automates).
- **FOSS4G conference channel** on YouTube — talks tagged GeoServer; also good for
  ecosystem context generally.

**Checkpoint:** you can manually publish one GeoTIFF and one shapefile to your local
docker GeoServer through the web UI *and* through `curl` REST calls, and explain which
`geoserver_utils.py` methods do the same.

---

## Phase 5 — Google Earth Engine (the part you will NOT port)

**Goal:** read the `ee.*` code well enough to draw the line between cloud math (stays)
and local math (ports), and understand the base layers the local-compute branch downloads
to replace GEE inputs.

**Required**
- **Gorelick et al. (2017), "Google Earth Engine: Planetary-scale geospatial analysis for
  everyone"**, *Remote Sensing of Environment* — THE citable paper for your literature
  review; explains the computation model (lazy, server-side, tiled).
- **Earth Engine documentation** (developers.google.com/earth-engine) — *Get Started*,
  *Image*, *FeatureCollection*, *Export* guides. Enough to read `utilities/gee_utils.py`.

**Targeted readings**
- *Cloud-Based Remote Sensing with Google Earth Engine* (Cardille, Crowley, Saah,
  Clinton, eds.) — free/open-access Springer book (eefabook.org); chapter-level dips for
  LULC classification, terrain analysis concepts behind `computing/lulc/`,
  `computing/terrain_descriptor/`.
- Datasets used by this repo (look them up in the EE Data Catalog as you meet them):
  CHIRPS (precipitation, used by SPEI/drought), FABDEM (the terrain raster on the
  local-compute branch), Dynamic World / LULC sources.

**Videos**
- Channel: **Qiusheng Wu** (creator of `geemap`) — the best practical GEE Python series.
- Channel: **Google Earth Engine / Geo for Good** — official talks on the platform model.
- Channel: **Spatial Thoughts** — "End-to-End Google Earth Engine" free course.

**Checkpoint:** for any file in `computing/`, you can mark each function **CLOUD** (calls
`ee.*` → not portable) or **LOCAL** (geopandas/rasterio → portable), and explain how
`feature/local-compute-station` converts the former into the latter (downloaded base
rasters + numpy).

---

## Phase 6 — OCaml (what you are porting *to*) — the long pole

**Goal:** working OCaml fluency plus the specific skills the port needs: project setup
with dune, and C-library binding with ctypes (for GDAL/GEOS/PROJ).

**Required — language**
- ***OCaml Programming: Correct + Efficient + Beautiful*** (Cornell CS 3110 textbook,
  free online, cs3110.github.io/textbook) — the best structured OCaml course.
- **YouTube: Michael Ryan Clarkson — "OCaml Programming" playlist** — the recorded
  CS 3110 lectures matching the book chapter-by-chapter. This is your primary video
  resource for the whole project.
- ***Real World OCaml*** (Minsky, Madhavapeddy — free at dev.realworldocaml.org) — read
  after/alongside CS 3110; especially the chapters on **error handling**, **testing**,
  and **Foreign Function Interface (ctypes)** — the FFI chapter is directly your
  Phase-2/3 work in `convert.md`.
- **ocaml.org/docs** — official tutorials; the *Your First OCaml Program* + dune intro.

**Required — tooling & FFI**
- **dune documentation** (dune.readthedocs.io) — project layout, libraries, executables,
  tests; you'll create `corestack-geocompute/` with it.
- **opam** basics (opam.ocaml.org) — switches, pins, publishing (needed for Phase 6–7 of
  `convert.md`).
- **ocaml-ctypes** (github.com/yallop/ocaml-ctypes) — README + examples; the mechanism
  for binding libgdal/libgeos/libproj. Pair with the RWO FFI chapter.

**Targeted readings**
- **GEOS C API documentation** (libgeos.org) — the *stable* C API (`GEOSIntersection`,
  `GEOSLength`, `GEOSBuffer`...) you'd bind; mirrors shapely's backend 1:1, which is why
  numeric verification can be tight.
- **GDAL C API** (gdal.org/api) — raster dataset open/read/write, OGR vector API.
- **PROJ documentation** (proj.org) — `proj_create_crs_to_crs` style transforms.
- Search opam (ocaml.org/packages) for existing bindings before writing your own —
  check current state of any `gdal`, `geos`, `proj` packages and judge maintenance
  freshness yourself; assume nothing.
- *Owl* (numerical OCaml library, ocaml.xyz) — numpy-equivalent arrays for raster math;
  fallback is plain `Bigarray`.

**Videos**
- Clarkson's playlist (above) — the backbone.
- Search: **"OCaml dune tutorial"** and **"ocaml ctypes"** — conference talks/workshops
  (e.g. OCaml Workshop talks on the **ICFP / OCaml Workshop** recordings) for FFI patterns.

**Checkpoint (graduated):**
1. Finish CS 3110 chapters through modules + functors; solve a few exercises per chapter.
2. Build a dune project that parses a GeoJSON file into OCaml types and prints feature
   counts (pure OCaml, e.g. with `yojson`).
3. Bind ONE GEOS function via ctypes (e.g. `GEOSGeomFromWKT` + `GEOSLength`) and verify
   the length of a known WKT linestring matches shapely's answer.
   → Passing #3 means you're ready for `convert.md` Phase 2 for real.

---

## Phase 7 — Open-source practice & verification literature (ongoing)

**Goal:** package the OCaml component as a credible open-source project and defend the
correctness claim in your research write-up.

**Required**
- This repo's `LICENSE` — your extracted component must be license-compatible with it
  (check before choosing MIT/Apache-2.0/GPL for the new repo).
- **choosealicense.com** + the **REUSE specification** — practical licensing hygiene.
- **Keep a Changelog** + **Semantic Versioning** (keepachangelog.com, semver.org).
- **opam packaging guide** — how OCaml projects are released.

**Targeted readings (for the research angle)**
- *Producing Open Source Software* (Karl Fogel) — free online; governance, community,
  release practice. Skim relevantly.
- Literature on **differential / oracle testing** (testing a port against a reference
  implementation) — search terms: "differential testing", "oracle problem in software
  testing" (Barr et al., *The Oracle Problem in Software Testing*, IEEE TSE 2015). This
  is the academic framing for `convert.md` Phase 4 and is citable in your research.
- **CoRE Stack's own docs** — docs.core-stack.org (developer + pipeline docs) and the
  repo wiki *DB Design* page — primary sources about this codebase.
- The pinned integration guide linked from `README.md` ("Integrating custom pipelines on
  CoREStack") — Google Doc; primary source on pipeline conventions.

**Checkpoint:** your new repo has LICENSE, README with the ported-contract table,
CHANGELOG, CI running the verification fixtures, and you can cite (Gorelick 2017) for
GEE and (Barr 2015) for your verification methodology in the write-up.

---

## Phase ↔ project mapping (one glance)

| Learn phase | Unlocks in this project |
|---|---|
| 1 GIS basics | Reading any geometry/CRS code; understanding EPSG:4326↔7755 |
| 2 Python geo stack | Auditing `computing/**/*_local.py`, `computing/utils.py` — the port source |
| 3 Django/Celery/PostGIS | Tracing `api.py → queue("nrm") → task`; finding the seams (convert.md Phase 0–1) |
| 4 GeoServer/OGC | Keeping `geoserver_utils.py` as boundary; manual publish for E2E test (convert.md Phase 5) |
| 5 Earth Engine | Drawing the CLOUD/LOCAL line; base-layer inputs on `feature/local-compute-station` |
| 6 OCaml + FFI | Writing the port (convert.md Phases 2–4) |
| 7 OSS + verification lit | Packaging + research-grade correctness argument (convert.md Phases 4, 6) |

## Minimal "if I only had two weeks" cut

1. *A Gentle Introduction to GIS* (Phase 1) — 1 day.
2. Shapely + GeoPandas + Rasterio doc topics listed above (Phase 2) — 3 days, while
   re-reading `drainage_density.py` and `rasterize_vector.py`.
3. Celery "First Steps" + trace one endpoint end-to-end (Phase 3) — 1 day.
4. GeoServer manual data-management + manual publish exercise (Phase 4) — 1 day.
5. CS 3110 textbook + Clarkson videos, chapters 1–5, then the ctypes GEOS-length
   experiment (Phase 6) — the rest.

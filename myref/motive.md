# Motive

> Companion to `branches-flow-connection.md` (architecture), `convert.md` (procedure),
> `branches-available.md` (which branch to port), `formuale.md` (the math), and
> `basics-learn.md` (the literature path). This file answers the *why* — first the
> existing project's purpose, then our own purpose layered on top of it.

---

## Part 1 — The motive of the existing project (CoRE Stack)

**CoRE Stack** is a Django + Celery + PostGIS + GeoServer platform for **Natural Resource
Management (NRM)** in India. Its purpose is to turn raw satellite and geographic data into
**decision-ready maps and indicators** for water, land, agriculture, and ecology — at the
scale of administrative units (State → District → Block/Tehsil → Gram Panchayat /
Microwatershed).

### Why it exists (the real-world problem)
Planning for watershed development, drought relief, groundwater recharge, afforestation,
and rural employment schemes (e.g. MGNREGA) needs **location-specific, quantitative
evidence** — how much water a microwatershed recharges, where runoff collects, which land
is degrading, how cropping intensity is changing, where to place a check-dam or farm pond.
Field surveys for all of India are impossible to do repeatedly. CoRE Stack replaces that
with **automated, satellite-driven computation** that any planner can query per location.

### What it actually does
- Ingests global earth-observation datasets (rainfall, evapotranspiration, NDVI, DEM,
  land cover, soil, aquifers) via **Google Earth Engine (GEE)**.
- Computes a stack of geospatial layers — the math catalogued in `formuale.md`:
  - **Hydrology / water budget:** precipitation → runoff (SCS-CN) → evapotranspiration →
    net groundwater recharge (ΔG) → well-depth fluctuation, per microwatershed.
  - **CLART:** recharge-potential + slope → recommended water-harvesting structures
    (check-dams, farm ponds, contour trenches, gully plugs).
  - **Drought:** VCI / MAI / SPI indices → weekly drought severity & causality.
  - **Land:** LULC classification, cropping intensity, change detection
    (built-up/deforestation/degradation), terrain landforms, surface water bodies,
    tree-health / canopy change, drainage density.
- **Publishes** the results as map layers (GeoServer WMS/WFS) and APIs (`public_api`,
  STAC) consumed by frontends, dashboards, DPRs (Detailed Project Reports), and a
  WhatsApp bot for community engagement.

### Who it serves
Government planners, NGOs, watershed organisations, and rural communities — and the
research/academic teams (CoRE Stack org / IIT-Delhi and partners such as FPL) who maintain
and extend the pipelines. The end goal of the platform is **better, evidence-based,
ground-level natural-resource planning**.

### In one line
> CoRE Stack converts satellite data into per-location water/land/drought indicators so
> NRM decisions can be made on evidence instead of guesswork.

---

## Part 2 — Our motive (the OCaml geospatial-compute port)

Our work is **not** to rebuild CoRE Stack. It is to extract and reimplement its
**computation core** as a **standalone, open-source OCaml component** that runs the same
geospatial math **without depending on Google Earth Engine for computation**.

### The problem we are solving
Today, CoRE Stack's calculations are entangled with **GEE** — a proprietary, cloud-hosted
service that requires a registered Google Cloud project, a service-account key, and
network access, and whose free tier is restricted to noncommercial use. This creates real
limits:
- **Dependency & lock-in:** the computation can't run without Google's cloud and a valid
  GEE entitlement.
- **Not open / not reproducible:** you cannot ship the compute engine as open-source
  software that anyone can run locally, because the math lives inside GEE calls.
- **Cost & quota:** heavy or operational use draws on GEE quota/billing and terms.

### What we are building
A clean **`corestack-geocompute/`** OCaml project that:
1. **Reimplements the computation in OCaml** — both the already-local Python math
   (`*_local.py`: LULC, terrain, change-detection, cropping intensity, …) and the math
   currently done inside GEE (hydrology, drought indices, etc.), ported from the
   `formuale.md` spec. *(We cannot send OCaml to GEE, so GEE computation must be
   re-created locally.)*
2. **Uses GEE only as an optional data tap** — download only the inputs needed for the one
   region/tehsil/block being worked on; never as a compute service.
3. **Keeps GeoServer as the thin publish boundary** — we port the calculation, not the
   serving/storage glue.
4. **Verifies numerically** — every ported layer must match the Python/GEE reference
   output for a fixed test block (golden-file method) before it's accepted.

### Why OCaml
A statically-typed, compiled, memory-safe language gives a fast, dependable,
self-contained compute engine. By binding the same C libraries the Python stack already
wraps (GDAL / GEOS / PROJ via `ctypes`), numeric results can match the reference closely,
making the correctness argument tight.

### Why it matters (the payoff)
- **Open-source & reproducible:** a compute engine anyone can run with **zero Google
  credentials** — strengthening CoRE Stack as a public good.
- **No GEE lock-in for computation:** the math becomes portable, auditable, and free of
  proprietary-cloud dependency.
- **Research contribution:** a rigorously verified Python/GEE → OCaml port is a citable
  piece of work (differential/oracle-testing methodology), and the eventual aim is to
  contribute it **back upstream** to the CoRE Stack project.

### Scope honesty (what we are *not* doing)
- Not replacing GEE's **data archive** — we still download inputs from GEE (or, later,
  original providers); we only replace GEE's **computation**.
- Not rewriting Django, Celery, auth, STAC, DPR, or the bot — those stay in Python.
- Not porting everything at once — easiest-first, one verified module at a time
  (`rasterize_vector` → `drainage_density` → the `*_local.py` raster modules → the
  GEE-computation modules), per `convert.md`.

### In one line
> Take CoRE Stack's GEE-bound geospatial computation and re-create it as an open-source,
> credential-free OCaml engine that reproduces the same layers and can be verified
> against the originals — making the science portable, auditable, and free of
> proprietary-cloud lock-in.

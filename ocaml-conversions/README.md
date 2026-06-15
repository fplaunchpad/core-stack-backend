# ocaml-conversions

End-to-end workspace to **port CoRE Stack geospatial computation to OCaml/geocaml and
verify it against the Python reference**, one location at a time.

> First target module: **drainage density** (Branch 0 `main` —
> `computing/clart/drainage_density.py::generate_vector`). See `../myref/convert.md`,
> `../myref/Geocml-usage-on-15-Branches.md`, `../myref/formuale.md` §4.

## The workflow (what you run)

```
 choose location (config/location.json)
        │
        ▼
 ① 01_gee_download/download_inputs.py     →  data/inputs/<loc>/{mws.geojson, drainage_lines.geojson}
        │                                       (GEE used only as a per-block data tap — rule 2)
        ├───────────────┬───────────────────┐
        ▼               ▼                 
 ② 02_python_reference  ③ 03_geocaml       
   drainage_density_ref.py   (OCaml CLI)   
        │               │                 
        ▼               ▼                 
 data/outputs/python/<loc>/dd.geojson   data/outputs/geocaml/<loc>/dd.geojson
        └───────────────┴───────────────────┐
                                             ▼
 ④ 04_verify/compare_geojson.py  →  per-feature DD diff, max/mean abs error, PASS/FAIL
```

## Folders

| Folder                   | What it does                                                       | Language                          |
| ------------------------ | ------------------------------------------------------------------ | --------------------------------- |
| `config/`              | choose the location (`location.json`)                            | JSON                              |
| `01_gee_download/`     | download MWS + drainage-line GeoJSON for the chosen block from GEE | Python (`ee`)                   |
| `02_python_reference/` | run the**reference** drainage-density math → golden GeoJSON | Python (geopandas)                |
| `03_geocaml/`          | the**OCaml** port (dune project) → GeoJSON                  | OCaml                             |
| `04_verify/`           | compare the two GeoJSONs, report similarity                        | Python                            |
| `data/`                | `inputs/` (downloaded) + `outputs/python                         | geocaml/` (results) — gitignored |

## Toolchain (one-time)

The OCaml side needs an opam switch with `dune` + `yojson`. The drainage port is **pure
OCaml** (its own ellipsoidal LCC projection + clip + length), so it needs **no**
GDAL/GEOS/PROJ system libs. Set up exactly what was used here:

```bash
opam init -y --bare --disable-sandboxing            # once per machine
opam switch create geocompute 4.14.2 -y             # builds a clean compiler
eval "$(opam env --switch=geocompute)"
opam install -y dune yojson
```

## How to run (drainage density)

```bash
conda activate corestackenv     # the env with geopandas (for ② and ④)
cd ocaml-conversions

# Verified end-to-end on the bundled synthetic fixture (no GEE needed):
./run_verify.sh config/location_synthetic.json
#   → ② python ref → ③ ocaml port → ④ compare → ✓ PASS (max abs diff ~1e-12)

# For a REAL block (needs its GEE assets + creds):
cp config/location.example.json config/location.json   # edit to your block
python 01_gee_download/download_inputs.py --config config/location.json   # ① download
./run_verify.sh config/location.json                                      # ②③④
```

Manual ③ (what `run_verify.sh` does):
```bash
( cd 03_geocaml && eval "$(opam env --switch=geocompute)" && dune build \
  && dune exec ./bin/main.exe -- drainage \
       --mws ../data/inputs/<loc>/mws.geojson \
       --lines ../data/inputs/<loc>/drainage_lines.geojson \
       --out ../data/outputs/geocaml/<loc>/dd.geojson )
```

## Command-by-command — what each does + expected output

### Toolchain (one-time)

| Command | What it does | Expected output |
|---|---|---|
| `opam init -y --bare --disable-sandboxing` | initialise opam | config created; `[WARNING] Shell not updated in non-interactive mode` is harmless |
| `opam switch create geocompute 4.14.2 -y` | build an isolated OCaml 4.14.2 | after a few min, ends with the switch listed. If you see `There already is an installed switch named geocompute`, it's already done — skip |
| `eval "$(opam env --switch=geocompute)"` | point the shell at that switch | (no output) |
| `opam install -y dune yojson` | build tool + JSON lib | `installed dune.3.x` / `installed yojson.3.x` (or `already installed`) |

### ① `python 01_gee_download/download_inputs.py --config config/<cfg>.json`
- **Does:** pulls the block's MWS + drainage lines from GEE → `data/inputs/<loc>/{mws.geojson,drainage_lines.geojson}` (data only — rule 2).
- **Expected:**
  ```
  ↓ MWS         projects/.../filtered_mws_<d>_<b>_uid
  ↓ drainage    projects/.../drainage_lines_<d>_<b>
  ✓ wrote <N> MWS + <M> drainage lines → .../data/inputs/<loc>
  ```
- If the per-block drainage asset is missing: prints `precomputed asset not found …; clipping pan-India dataset directly` and falls back.
- ⚠ **Large block:** GEE may abort with `Collection query aborted after accumulating over 5000 elements`. Then verify a per-watershed subset (see real-data row below) or add a GCS-export path.

### ② `python 02_python_reference/drainage_density_ref.py --config config/<cfg>.json`
- **Does:** computes drainage density with geopandas (the golden reference) → `data/outputs/python/<loc>/dd.geojson`.
- **Expected:** `✓ <N> watersheds → .../outputs/python/<loc>/dd.geojson`

### ③ `dune build && dune exec ./bin/main.exe -- drainage --mws … --lines … --out …`
- **Does:** the pure-OCaml port → `data/outputs/geocaml/<loc>/dd.geojson`.
- **Expected:** `drainage: <N> watersheds -> .../outputs/geocaml/<loc>/dd.geojson` (and a clean `dune build` with no errors).

### ④ `python 04_verify/compare_geojson.py --python … --geocaml … --field DD --key uid --atol 1e-6`
- **Does:** matches features by `uid`, compares `DD` (+ per-order `DD_stream`/`str_len_km`), prints diffs; **exit 0 = PASS, 1 = FAIL**.
- **Expected (PASS):**
  ```
  compared <N> features on 'DD'
    max  abs diff = 1.5e-12
    mean abs diff = …
    DD_stream: max=… mean=…
    str_len_km: max=… mean=…
  ✓ PASS
  ```

### `./run_verify.sh config/<cfg>.json`  (runs ②③④ in sequence)
- **Does:** ② Python ref → ③ OCaml (auto-activates the opam switch) → ④ compare.
- **Expected — synthetic** (`config/location_synthetic.json`):
  ```
  location slug: test_synthetic_demo
  ② Python reference … ✓ 2 watersheds → …
  ③ OCaml port …       drainage: 2 watersheds -> …
  ④ Verify …           max abs diff = 1.5e-12 … ✓ PASS
  ```
- **Expected — real baksa watershed** (`config/location_baksa_subset.json`):
  ```
  location slug: assam_baksa_baksa_subset
  ② Python reference … ✓ 1 watersheds → …
  ③ OCaml port …       drainage: 1 watersheds -> …
  ④ Verify …           max abs diff = 1.5e-11 … ✓ PASS
  ```
  (python DD ≈ ocaml DD ≈ `34.4151736831` for watershed `11_80980`.)

## Status — drainage density: ✅ WORKING & VERIFIED (synthetic + real data)

| Step | State |
|---|---|
| ① GEE download | ✅ runs; falls back to clipping the pan-India dataset when a per-block asset is absent |
| ② Python reference | ✅ runs (geopandas) → golden GeoJSON |
| ③ OCaml port | ✅ **builds + runs** — pure OCaml, no GDAL/GEOS/PROJ |
| ④ Verify | ✅ **PASS** on both fixtures |

| Fixture | Result |
|---|---|
| `location_synthetic.json` (2 watersheds) | max abs diff **1.5e-12**, ✓ PASS |
| `location_baksa_subset.json` (real: assam/baksa watershed `11_80980`, 31 drainage lines, ORDER 1–4) | DD 34.4151736831 both; max abs diff **1.5e-11**, ✓ PASS |

Proven: the OCaml ellipsoidal LCC (EPSG:7755) + segment/polygon clip + length reproduce
geopandas/pyproj to ~11–12 decimals on **real CoRE Stack drainage geometry**.

### GEE `getInfo` 5000-feature cap
A whole large block (e.g. all of baksa) has >5000 drainage lines, which `getInfo` refuses.
The real pipeline handles this by exporting to GCS. For verification we either (a) verify
per-watershed subsets (as `location_baksa_subset.json` does), or (b) add a GCS-export path
to `01_gee_download` for full blocks. Next module (`rasterize_vector`, needs raster-write)
follows the same ①②③④ loop.

## Conventions

- `<loc>` = `<state>_<district>_<block>` (lowercased, spaces→`_`), matching the pipeline.
- GEE is a **data source only** (rule 2): download just the one block's inputs.
- A module is "done" only when ④ passes within tolerance (golden-file gate, `convert.md` Phase E).

---

## The formula we converted — drainage density

Source: `computing/clart/drainage_density.py::generate_vector` · spec: `../myref/formuale.md` §4.

For each watershed (MWS), drainage lines are reprojected to **EPSG:7755** (metres), clipped
to the polygon, and summed by Strahler stream order:

```
DD = Σ (over stream orders o = 1..11)   ( L_o / 1000 ) · f_o · 100 / ( area_in_ha / 100 )
```

| Symbol | Meaning | Units |
|---|---|---|
| `DD` | drainage density (sum over all orders) | km/km² (scaled) |
| `L_o` | total length of clipped drainage lines of stream order `o`, in EPSG:7755 | m (÷1000 → km) |
| `f_o` | influence factor for order `o` | `60/385, 55/385, 50/385, 45/385, 40/385, 35/385, 30/385, 25/385, 20/385, 15/385, 10/385` |
| `area_in_ha` | watershed area (input attribute) | ha (code uses `/100`) |
| stream order | Strahler `ORDER` attribute on each drainage line | 1 (headwater) … 11 (main channel) |

Outputs per watershed: `DD` (scalar), `DD_stream` (per-order list), `str_len_km` (per-order
length list). The OCaml reproduces this exactly (`03_geocaml/lib/drainage.ml`), including its
own ellipsoidal **Lambert Conformal Conic 2SP** for EPSG:7755 (φ0=24°, λ0=80°,
φ1=12.472955°, φ2=35.1728044°, FE/FN=4,000,000, WGS84).

## What we did (summary)

- Built `ocaml-conversions/` — a download → run-both → verify workspace for porting CoRE
  Stack computation to OCaml, one location at a time.
- Ported the **first module, drainage density**, from Python → **pure OCaml** (no
  GDAL/GEOS/PROJ): GeoJSON I/O (yojson) + ellipsoidal LCC projection + segment/polygon clip
  + length, then the §4 formula.
- Stood up the OCaml toolchain (opam switch `geocompute` = OCaml 4.14.2 + dune + yojson).
- **Verified Python vs OCaml** on two fixtures, both PASS:
  - synthetic (2 watersheds): max abs diff **1.5e-12**;
  - **real** assam/baksa watershed `11_80980` (31 drainage lines, ORDER 1–4): `DD =
    34.4151736831` on both, max abs diff **1.5e-11**.

## Local vs GEE changes we made

Drainage density is a special case: its *math was already local Python* (geopandas) on
`main`; GEE was only the data source. So:

| Concern | Original (`main`) | What we changed |
|---|---|---|
| **Computation** | local Python — `to_crs(7755)` → `gpd.clip` → `.length` → §4 formula | **reimplemented in pure OCaml** (`drainage.ml`) — exact match to ~1e-11 |
| **GEE part** | `ee.FeatureCollection(...).getInfo()` to pull MWS + drainage-line assets | reduced to a **per-block data download** (`01_gee_download`, rule 2), with a fallback that clips the pan-India dataset directly — **no GEE computation involved** |
| **Output** | shapefile (stringifies list fields) | **GeoJSON** with native arrays (`DD_stream`, `str_len_km` kept faithfully) |
| **Django/Celery/GeoServer/DB** | task wrapper + `save_layer_info_to_db` + publish | left out of scope — verification reads/writes plain files |

(For modules that *do* have GEE computation — hydrology, drought, LULC, etc. — the GEE math
gets reimplemented in OCaml too, per `../myref/gee-computations.md`. Drainage density simply
had none.)

## The procedure we followed (maps to `../myref/convert.md`)

1. **Phase A — toolchain:** `opam init` → switch `geocompute` (4.14.2) → `dune` + `yojson`.
2. **Contracts & fixtures:** froze the §4 contract; built a synthetic fixture and a real
   per-watershed subset of baksa (kept under GEE's 5000-feature `getInfo` cap).
3. **① data (rule 2):** downloaded only the one block's MWS + drainage lines from GEE.
4. **② reference:** ran the extracted Python math → golden GeoJSON.
5. **③ port:** implemented the module in OCaml (projection + clip + length + formula).
6. **④ verify (the gate):** compared per-feature `DD` within tolerance; accepted only on
   PASS (golden-file method). Both fixtures passed.
7. **Boundaries kept in Python:** GEE download stays a thin data step; GeoServer/DB
   publishing untouched.

Next module follows the same ①②③④ loop (e.g. `rasterize_vector`, which adds the
raster-write path).

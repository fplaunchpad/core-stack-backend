# End-to-End Integration Plan: ERA5-Land Temperature & Humidity into the CoRE Stack

**Status:** Proposed implementation plan
**Author:** prepared for the FPL / CoRE Stack team
**Target dataset:** ECMWF ERA5-Land (`ECMWF/ERA5_LAND/MONTHLY_AGGR`) — 2 m air temperature and humidity
**Scope:** Backend pan-India raster layer → MWS vectorization → KYL filters → MWS & tehsil report sections

---

## 1. Why this dataset (feasibility recap)

The CoRE Stack is **raster-first and Google-Earth-Engine-native**. Every existing indicator rides one canonical spine (Section 3). The single hardest requirement in the brief — *"pan-India raster layer"* — is the test each candidate dataset must pass:

| Candidate | Native form | In GEE? | Pan-India raster? | New infra needed | Verdict |
|---|---|---|---|---|---|
| **ERA5-Land temp/humidity** | Raster (ImageCollection) | ✅ `ECMWF/ERA5_LAND/MONTHLY_AGGR` | ✅ directly | ~none — reuses precipitation pattern | **Chosen** |
| GBIF biodiversity | Points (occurrences) | ❌ | ❌ must grid points → raster | new points→grid ingestion | Best *second* integration |
| Water quality (PDFs) | Sparse station points in PDFs | ❌ | ❌ must interpolate (IDW/kriging) | PDF parsing + interpolation | Defer |

ERA5-Land is the lowest-risk way to prove the **entire** path end-to-end, because the per-MWS zonal step is the exact `reduceRegions(ee.Reducer.mean())` pattern already implemented in [`computing/mws/precipitation.py`](../computing/mws/precipitation.py). We treat that file as the reference implementation throughout.

---

## 2. Data source specifics (get these right)

- **Collection:** `ECMWF/ERA5_LAND/MONTHLY_AGGR` (monthly aggregated; 1950 → near-present; global incl. all of India).
- **Bands used:**
  - `temperature_2m` — 2 m air temperature, **Kelvin**.
  - `temperature_2m_max` / `temperature_2m_min` *(in the monthly-by-hour / daily products; for monthly-aggr use the available extreme bands)* — for heat-stress indicators.
  - `dewpoint_temperature_2m` — 2 m dewpoint, **Kelvin** (used to derive humidity).
- **Native resolution:** ~0.1° ≈ **11132 m**. Reuse `scale=11132` — the same value `precipitation.py` already uses, so no resampling surprises.
- **Unit conversions (do these in GEE before reducing):**
  - Temperature °C = `temperature_2m − 273.15`.
  - **Relative humidity** from T and dewpoint Td (both °C), August–Roche–Magnus:
    ```
    RH = 100 * exp((17.625 * Td) / (243.04 + Td)) / exp((17.625 * T) / (243.04 + T))
    ```
- **Temporal aggregation for indicators:**
  - **Annual mean temperature** (hydrological year `Jul 1 → Jun 30`, matching `generate_hydrology.py`).
  - **Annual mean / max relative humidity**.
  - **Multi-year trend** via Mann-Kendall (`pymannkendall`, already a dependency used by KYL trend indicators) for `temperature_trend` and `humidity_trend`.

---

## 3. The pipeline spine (what every layer follows)

```
API endpoint (state/district/block)  →  Celery task (queue="nrm")
  → ee_initialize(gee_account_id)
  → load MWS:  filtered_mws_<district>_<block>_uid   (FeatureCollection, key="uid")
  → compute on PAN-INDIA ERA5 ImageCollection
  → [raster] export_raster_asset_to_gee → sync_raster_to_gcs → sync_raster_gcs_to_geoserver
  → [vector] reduceRegions(mean) per MWS → export_vector_asset_to_gee → sync_layer_to_geoserver
  → save_layer_info_to_db  (Dataset / Layer / LayerMapping registry)
  → stats_generator: get_vector_layer_geoserver() dispatch by `workspace` → create_excel_for_climate() → sheet in {district}_{block}.xlsx
  → stats_generator/mws_indicators.py: read sheet → per-MWS KYL indicator dict
  → public_api: get_mws_json_by_kyl_indicator() → frontend KYL filters
  → dpr/gen_mws_report.py + gen_tehsil_report.py: read same Excel → report sections
```

**Key reference files (verified):**
- Raster export/sync helpers: [`utilities/gee_utils.py`](../utilities/gee_utils.py) — `ee_initialize` (L79), `get_gee_asset_path` (L428), `export_raster_asset_to_gee` (L470), `sync_raster_to_gcs` (L601), `sync_raster_gcs_to_geoserver` (L619), `export_vector_asset_to_gee` (L452).
- Per-MWS zonal analog: [`computing/mws/precipitation.py`](../computing/mws/precipitation.py) — `reduceRegions(mean)` keyed on `uid`.
- DB registry: [`computing/models.py`](../computing/models.py) — `Dataset`, `Layer`, `LayerMapping`; `save_layer_info_to_db` in [`computing/utils.py`](../computing/utils.py) L609.
- API surface: [`computing/api.py`](../computing/api.py) + [`computing/urls.py`](../computing/urls.py).
- Excel assembly: [`stats_generator/utils.py`](../stats_generator/utils.py) — `get_vector_layer_geoserver()` L34, workspace dispatch from L88.
- KYL: [`stats_generator/mws_indicators.py`](../stats_generator/mws_indicators.py) — `sheets` dict L69, `results.append({...})` L984.
- Reports: [`dpr/gen_mws_report.py`](../dpr/gen_mws_report.py), [`dpr/gen_tehsil_report.py`](../dpr/gen_tehsil_report.py), templates `templates/mws-report.html`, `templates/block-report.html`.

---

## 4. Phased implementation plan

### Phase 0 — Registry & config (no compute yet)

These must exist *before* the task runs, because `save_layer_info_to_db` does `Dataset.objects.get(name=...)` (will raise if missing).

1. **Seed `Dataset` rows.** Add to [`installation/seed/seed_data.json`](../installation/seed/seed_data.json) (it already lists `Hydrology Precipitation`, `Hydrology Evapotranspiration`, etc.). Add, mirroring those entries:
   - `Climate Temperature` — `layer_type: vector`, `workspace: climate`, `style_name: temperature`.
   - `Climate Humidity` — `layer_type: vector`, `workspace: climate`, `style_name: humidity`.
   - *(optional raster datasets)* `Climate Temperature Raster`, `Climate Humidity Raster` — `layer_type: raster`.
   Load with `python manage.py loaddata installation/seed/seed_data.json` (or create via Django admin in dev).
2. **GeoServer workspace + styles.** Create workspace `climate`. Add raster SLDs alongside [`installation/geoserver/styles/`](../installation/geoserver/styles/) (e.g. `temperature.sld`, `humidity.sld`) with a sensible color ramp (blue→red for temperature). The vector layers reuse the standard MWS polygon style.
3. **`LayerMapping` rows for STAC** in [`data/STAC_specs/input/metadata/layer_mapping.csv`](../data/STAC_specs/input/metadata/layer_mapping.csv), then `python manage.py load_layer_mappings`. One row per layer: `layer_name`, `layer_type`, `db_dataset_name`, `geoserver_workspace_name`, `ee_layer_name=ECMWF/ERA5_LAND/MONTHLY_AGGR`, `spatial_resolution_in_meters=11132`, `theme=Climate`, `auto_stac=true`.

**Deliverable:** registry entries + style + workspace exist; no behavior change yet.

---

### Phase 1 — Backend pan-India raster + per-MWS vector compute

Create a self-contained module `computing/climate/` (mirrors `terrain_descriptor/` structure). Keep the climate math separate from hydrology but reuse the precipitation per-MWS pattern verbatim.

```
computing/climate/
  __init__.py
  temperature.py      # annual mean/max temperature → per-MWS FC  (clone of precipitation.py)
  humidity.py         # derive RH from temp + dewpoint → per-MWS FC
  climate.py          # Celery task: orchestrates compute + raster sync + vector sync + DB save
```

**`computing/climate/temperature.py`** — clone `precipitation.py`, swapping the source and reducer:

```python
def temperature(roi, asset_suffix, asset_folder_list, app_type,
                start_date, end_date, is_annual=True):
    description = "Temp_annual_" + asset_suffix
    asset_id = get_gee_dir_path(asset_folder_list,
                  asset_path=GEE_PATHS[app_type]["GEE_ASSET_PATH"]) + description
    if is_gee_asset_exists(asset_id):
        # incremental year-merge — identical to precipitation.py
        ...
        return None, asset_id, last_date
    return _generate(roi, asset_id, description, start_date, end_date)


def _generate(roi, asset_id, description, start_date, end_date):
    # annual window Jul 1 -> Jun 30, same loop as precipitation._generate_data
    ic = (ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_AGGR")
            .filter(ee.Filter.date(f_start_date, f_end_date))
            .select("temperature_2m"))
    annual = ic.mean().subtract(273.15).clip(roi)          # Kelvin -> Celsius
    stats = annual.reduceRegions(reducer=ee.Reducer.mean(),
                                 collection=roi, scale=11132)
    # join back onto each MWS uid, set column = start_date  (precipitation.py:res())
    ...
    task_id = export_vector_asset_to_gee(roi, description, asset_id)
    return task_id, asset_id, start_date
```

**`computing/climate/humidity.py`** — same skeleton, but build RH in GEE from `temperature_2m` and `dewpoint_temperature_2m` (expression with `ee.Image.expression`) before `reduceRegions(mean)`.

**`computing/climate/climate.py`** — the Celery entry point (pattern from `terrain_clusters.py`):

```python
@app.task(bind=True)
def generate_climate(self, state, district, block, start_year, end_year, gee_account_id):
    ee_initialize(gee_account_id)
    asset_suffix = valid_gee_text(district.lower()) + "_" + valid_gee_text(block.lower())
    asset_folder_list = [state, district, block]
    roi = ee.FeatureCollection(get_gee_dir_path(asset_folder_list, ...) +
            f"filtered_mws_{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}_uid")

    start_date, end_date = f"{start_year}-07-01", f"{end_year+1}-06-30"

    temp_task, temp_asset, _ = temperature(roi, asset_suffix, asset_folder_list, "MWS",
                                           start_date, end_date, is_annual=True)
    hum_task, hum_asset, _   = humidity(roi, asset_suffix, asset_folder_list, "MWS",
                                        start_date, end_date, is_annual=True)
    check_task_status([t for t in (temp_task, hum_task) if t])

    # (optional) pan-India raster product for map display
    #   export_raster_asset_to_gee(annual_temp_image, ...) -> sync_raster_to_gcs -> sync_raster_gcs_to_geoserver

    for asset_id, dataset_name, layer_suffix in [
        (temp_asset, "Climate Temperature", "temperature_annual"),
        (hum_asset,  "Climate Humidity",    "humidity_annual"),
    ]:
        if is_gee_asset_exists(asset_id):
            make_asset_public(asset_id)
            layer_id = save_layer_info_to_db(
                state, district, block,
                layer_name=f"{asset_suffix}_{layer_suffix}",
                asset_id=asset_id, dataset_name=dataset_name,
                algorithm="ERA5_LAND", algorithm_version="1.0",
                misc={"start_year": start_year, "end_year": end_year})
            fc = ee.FeatureCollection(asset_id).getInfo()
            res = sync_layer_to_geoserver(state, fc,
                    f"{asset_suffix}_{layer_suffix}", "climate")
            if res.get("status_code") == 201 and layer_id:
                update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
```

**Wire the API** in [`computing/api.py`](../computing/api.py) (clone the `change_detection` endpoint, which already takes `state/district/block/start_year/end_year/gee_account_id`):

```python
@api_view(["POST"])
@schema(None)
def generate_climate_layer(request):
    state = request.data.get("state").lower(); district = request.data.get("district").lower()
    block = request.data.get("block").lower()
    start_year = request.data.get("start_year"); end_year = request.data.get("end_year")
    gee_account_id = request.data.get("gee_account_id")
    generate_climate.apply_async(
        args=[state, district, block, start_year, end_year, gee_account_id], queue="nrm")
    return Response({"Success": "climate task initiated"}, status=status.HTTP_200_OK)
```

Add the route to [`computing/urls.py`](../computing/urls.py): `path("generate_climate_layer/", api.generate_climate_layer, name="generate_climate_layer")` and the import at the top of `api.py`.

**Deliverable:** POST → per-MWS temperature & humidity FeatureCollections in GEE, public, synced to GeoServer workspace `climate`, registered in `Layer` table. (Optional pan-India raster for map tiles.)

---

### Phase 2 — Vectorization to MWS units

For ERA5 the vectorization **is** the compute (zonal mean of a coarse raster per MWS), so it's already produced in Phase 1 via `reduceRegions`. No separate vector task is required (unlike LULC/change-detection, which mask discrete classes). Each MWS feature ends up with one column per year (`2017-07-01`, `2018-07-01`, …) plus the `uid` — exactly the shape `mws_indicators.py` expects.

**Deliverable:** confirmed per-MWS columns keyed on `uid`, one per hydrological year, in the GeoServer `climate` layers.

---

### Phase 3 — Excel sheet integration (the link to KYL & reports)

Both KYL and the reports read **per-block Excel files** (`{district}_{block}.xlsx`), not GeoServer directly. So the new layers must be turned into sheets.

1. In [`stats_generator/utils.py`](../stats_generator/utils.py), `get_vector_layer_geoserver()` (L34) loops over registered layers and dispatches by `workspace` (L88+). Add a branch:
   ```python
   elif workspace == "climate":
       create_excel_for_climate(geojson_data, xlsx_file, writer)
   ```
2. Implement `create_excel_for_climate(...)` next to the existing `create_excel_for_*` helpers. It flattens the GeoServer GeoJSON to a DataFrame with columns `UID`, the per-year value columns, and writes sheets `temperature_annual` and `humidity_annual`. (Use `create_excel_annual_mws` / `create_excel_for_terrain` as templates for the GeoJSON→DataFrame→sheet shape.)
3. Confirm `fetch_layers_for_excel_generation()` (L18) picks up the new `Layer` rows (it filters on the `Layer`/`Dataset` join — the Phase 0 `Dataset` rows make this automatic).

**Deliverable:** running the existing "generate stats excel" endpoint produces `temperature_annual` and `humidity_annual` sheets in each block file.

---

### Phase 4 — KYL filters

In [`stats_generator/mws_indicators.py`](../stats_generator/mws_indicators.py):

1. Register the sheets in the `sheets` dict (L69):
   ```python
   "temperature_annual": -1,
   "humidity_annual": -1,
   ```
2. Inside the per-MWS loop (before `results.append`), compute the indicators (reuse the precipitation averaging pattern at L127 and the Mann-Kendall trend pattern at L169):
   ```python
   temp_cols = df_temperature[df_temperature["UID"] == specific_mws_id].filter(like="-07-01")
   avg_temperature = round(temp_cols.values.mean(), 2)
   temperature_trend = mk_trend(temp_cols.values.flatten())   # +1 / 0 / -1
   # ... same for humidity ...
   ```
3. Add the keys to `results.append({...})` (L984):
   ```python
   "avg_temperature": avg_temperature,
   "temperature_trend": temperature_trend,
   "avg_relative_humidity": avg_relative_humidity,
   "humidity_trend": humidity_trend,
   ```

No serializer/model/migration is needed — KYL is computed → cached JSON → served by [`public_api/api.py`](../public_api/api.py) `get_mws_json_by_kyl_indicator()`. The frontend discovers new keys dynamically. Regenerate with `GET /stats_generator/download_kyl_data/?...&regenerate=true`.

**Deliverable:** `avg_temperature`, `temperature_trend`, `avg_relative_humidity`, `humidity_trend` appear in the KYL filter JSON for every MWS.

---

### Phase 5 — MWS & tehsil report sections

Both reports read the same Excel and inject context into HTML templates.

**MWS report** ([`dpr/gen_mws_report.py`](../dpr/gen_mws_report.py)):
1. Add `get_climate_data(state, district, block, uid)` — read `temperature_annual` / `humidity_annual`, return a narrative string + a small dict (annual mean, trend direction, mini time-series for a chart). Pattern: `get_soge_data()`.
2. Import & call it in `generate_mws_report()` in [`dpr/api.py`](../dpr/api.py); add `"climate_desc"` / `"climate_data"` to the context dict.
3. Add a `{% if climate_desc %}<section>…</section>{% endif %}` block to `templates/mws-report.html` with a Chart.js line chart of the annual series.

**Tehsil/block report** ([`dpr/gen_tehsil_report.py`](../dpr/gen_tehsil_report.py)):
1. Add `get_climate_stress_data(state, district, block)` returning `mws_pattern` (bool) + `mws_intensity` (0–1) per MWS — e.g. flag MWS with rising-temperature + falling-humidity trend (a heat/aridity-stress pattern). Pattern: `get_agri_water_stress_data()` (indicator-weighting at L1418).
2. Import & call in `generate_tehsil_report()` (dpr/api.py); add context keys.
3. Add a `<section>` to `templates/block-report.html` with the MWS-intensity choropleth + summary text ("X ha of the tehsil shows a warming + drying trend").

**Deliverable:** new "Climate (Temperature & Humidity)" sections render in both report types.

---

### Phase 6 — STAC & public API exposure (mostly automatic)

- `save_layer_info_to_db` + the Phase 0 `LayerMapping` rows let STAC generation pick up the layers automatically (see `auto_stac` and [`computing/signals.py`](../computing/signals.py) L61). Run `python manage.py generate_stac` / `stac_coverage` to verify.
- Public layer URLs are served generically by `public_api/views.py` `get_generated_layer_urls()` — no change needed once the `Layer` rows + GeoServer layers exist.

---

## 5. Testing & validation strategy

1. **Unit / sanity (GEE):** for one known block, print `reduceRegions` output for 2–3 MWS; assert temperature in a plausible range (e.g. 15–45 °C for India) and RH in 0–100. Compare an MWS annual mean against ERA5 in the GEE Code Editor for the same geometry.
2. **Idempotency / incremental merge:** re-run for an additional year; confirm the `merge_fc_into_existing_fc` path appends a column without duplicating (same guarantee precipitation relies on).
3. **Excel:** open a generated `{district}_{block}.xlsx`; confirm `temperature_annual`/`humidity_annual` sheets have `UID` + per-year columns and row count == MWS count.
4. **KYL:** hit `download_kyl_data` with `regenerate=true`; confirm the 4 new keys exist for all MWS and trend ∈ {−1,0,1}.
5. **Reports:** render MWS and tehsil reports for a test block; visually confirm the new sections and charts.
6. **Pick one pilot block** (the repo ships `raichur_devadurga.xlsx` as a sample — use Raichur/Devadurga, Karnataka) to validate the full chain before any pan-India batch run.

---

## 6. Risks & gotchas (read before coding)

- **`Dataset` must pre-exist** — `save_layer_info_to_db` does `.get(name=...)`; Phase 0 is a hard prerequisite, not optional.
- **ERA5-Land band availability:** confirm exact extreme-temperature band names in `MONTHLY_AGGR` before using max/min; if absent, derive heat-stress from the daily product or drop the max indicator for v1.
- **Coarse resolution vs MWS size:** at ~11 km, small MWS may fall within a single ERA5 pixel — `reduceRegions(mean)` still returns a value (the covering pixel), but document that temperature/humidity are *regional context* indicators, not fine-grained like LULC. This matches how precipitation (also ~11 km) is already treated.
- **Null handling:** mirror `ee.Algorithms.If(val, val, 0)` from precipitation for MWS with no covering data, and guard `-1` sentinel sheets in `mws_indicators.py`.
- **Hydrological-year alignment:** keep `Jul 1 → Jun 30` to stay consistent with hydrology columns so KYL joins line up.
- **GeoServer vector size:** these FCs are small (one row per MWS) — no tiling concerns.
- **Don't fold into `generate_hydrology`** unless you want climate to run on every hydrology trigger; a separate `generate_climate_layer` endpoint keeps concerns and run-cost independent.

---

## 7. Touchpoint checklist (single source of truth)

| Phase | File | Change |
|---|---|---|
| 0 | `installation/seed/seed_data.json` | add `Climate Temperature` / `Climate Humidity` Dataset rows |
| 0 | `installation/geoserver/styles/` | add `temperature.sld`, `humidity.sld`; create `climate` workspace |
| 0 | `data/STAC_specs/input/metadata/layer_mapping.csv` | add LayerMapping rows; run `load_layer_mappings` |
| 1 | `computing/climate/{temperature,humidity,climate}.py` | new module (clone of `mws/precipitation.py` + `terrain_clusters.py` task) |
| 1 | `computing/api.py`, `computing/urls.py` | add `generate_climate_layer` endpoint + route + import |
| 3 | `stats_generator/utils.py` | add `workspace == "climate"` branch + `create_excel_for_climate()` |
| 4 | `stats_generator/mws_indicators.py` | add sheets to dict (L69), compute indicators, add 4 keys (L984) |
| 5 | `dpr/gen_mws_report.py`, `dpr/api.py`, `templates/mws-report.html` | `get_climate_data()` + section |
| 5 | `dpr/gen_tehsil_report.py`, `dpr/api.py`, `templates/block-report.html` | `get_climate_stress_data()` + section |
| 6 | — | run `generate_stac`; public layer URLs automatic |

---

## 8. Suggested sequencing & effort

| Phase | Effort | Can land independently? |
|---|---|---|
| 0 Registry/config | 0.5 day | yes |
| 1 Backend raster+vector | 2–3 days | yes (testable via GEE) |
| 3 Excel | 1 day | needs Phase 1 |
| 4 KYL | 1 day | needs Phase 3 |
| 5 Reports | 2 days | needs Phase 3 |
| 6 STAC/public | 0.5 day | needs Phase 1 |

Land Phases 0→1→3→4 first (that delivers a usable KYL filter), then Phase 5 (reports), then 6.

---

## 9. Future: GBIF biodiversity (the "exciting" second integration)

Once this proves the path, GBIF reuses **Phases 3–6 unchanged**. Only Phase 1 differs: instead of a native GEE raster, add a `points → grid` ingestion sub-pipeline:
1. Download occurrences (GBIF API, `country=IN`, Darwin Core Archive), clean/dedupe.
2. Grid to a pan-India **species-richness / Shannon-index raster** (geopandas/rasterio offline) and upload via the existing `upload_tif_to_gcs` + `gcs_to_gee_asset_cli` helpers — or load points as a FeatureCollection with `gdf_to_ee_fc` and count per MWS.
3. From there, the same `reduceRegions` → vector → Excel → KYL → report chain applies.

The repo already has the primitives (`LayerType.POINT`, `gdf_to_ee_fc`, `upload_tif_to_gcs`, `gcs_to_gee_asset_cli`), so the only genuinely new work is the gridding step.

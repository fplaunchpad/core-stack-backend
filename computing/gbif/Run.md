# Running the Biodiversity (GBIF) Pipeline

> A hands-on runbook: every command needed to generate the biodiversity layer for one block, with an
> explanation of **what each command does** and **why**. For architecture see [`PIPELINE.md`](PIPELINE.md);
> for stage-by-stage testing see [`VALIDATION_CHECKLIST.md`](VALIDATION_CHECKLIST.md).
>
> All examples use a real, validated block: **`karnataka / hassan / hassan`**.
> Prerequisite mindset: biodiversity is built **on top of the micro-watershed (MWS) layer** — the block
> must already have its `filtered_mws_<district>_<block>_uid` asset in GEE (from the upstream MWS pipeline).

---

## 0. One-time prerequisites

These are set up once per environment (a fresh install does most of them automatically via `install.sh`).

### 0.1 Activate the project environment
```bash
conda activate corestackenv
cd /home/snaveen/Desktop/core-stack-backend
```
*Why:* the pipeline needs the project's Python env (geopandas, pygbif, earthengine, Django). Nothing runs outside it.

### 0.2 GBIF account credentials in `nrm_app/.env`
```bash
GBIF_USER="your_gbif_username"
GBIF_PWD="your_gbif_password"
GBIF_EMAIL="you@org.org"
```
*Why:* the GBIF Download API needs a (free) GBIF.org account. GEE/GCS credentials do **not** cover it — GBIF is a separate service. Without these the pipeline aborts at the download step.

### 0.3 Register the DB rows (Dataset + LayerInfo)
```bash
python manage.py loaddata installation/seed/seed_data.json     # Dataset "Biodiversity Occurrence"
python manage.py register_biodiversity_layer                   # LayerInfo (idempotent)
```
*Why:* `save_layer_info_to_db` needs the `Dataset` row (else `Dataset.DoesNotExist`), and the Excel/KYL
generator needs the `LayerInfo` row (`excel_to_be_generated=True`). `register_biodiversity_layer` is safe
to run any number of times — it uses `get_or_create` and won't create duplicates. On a **fresh install**
`install.sh` runs both automatically; on an **existing DB** run them manually once.

### 0.4 GeoServer up + workspace + style
```bash
# start your GeoServer Docker container, then confirm it responds (200 or 302):
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/geoserver/web/

python installation/setup_local_geoserver.py                   # creates the 'biodiversity' workspace + uploads the SLD
```
*Why:* the layer is published to GeoServer and styled with `biodiversity_mws`. `setup_local_geoserver.py`
creates the `biodiversity` workspace and provisions the SLD (idempotently). Must run **before** generating a
layer, because the publish step applies that style.

---

## 1. Generate the layer for one block (the main run)

Pick a block that already has an MWS asset in GEE. To list the ones available:
```bash
python manage.py shell -c "
from utilities.gee_utils import ee_initialize, get_gee_asset_path, valid_gee_text, is_gee_asset_exists
import ee; ee_initialize('1')
base='projects/arcane-mason-493503-a6/assets/apps/mws'
ls=lambda p:[a['id'].split('/')[-1] for a in ee.data.listAssets({'parent':p}).get('assets',[])]
[print(st,d,b) for st in ls(base) for d in ls(f'{base}/{st}') for b in ls(f'{base}/{st}/{d}')
 if is_gee_asset_exists(get_gee_asset_path(st,d,b)+'filtered_mws_'+valid_gee_text(d.lower())+'_'+valid_gee_text(b.lower())+'_uid')]
"
```
*Why:* biodiversity can only run where the micro-watershed layer exists. If your block isn't listed, run the
upstream MWS pipeline first (`generate_mws_layer`).

### Option A — via the API (production path, asynchronous)
```bash
curl -X POST http://localhost:8080/api/v1/generate_biodiversity_layer/ \
  -H "Content-Type: application/json" \
  -d '{"state":"karnataka","district":"hassan","block":"hassan","gee_account_id":1}'
```
*What:* queues the `generate_biodiversity_block` Celery task on the `nrm` queue and returns immediately.
*Why:* the full pipeline takes minutes (GBIF download + GEE export), so the API doesn't block. **Requires a
running Celery worker** consuming the `nrm` queue.

### Option B — synchronously in a shell (what to use for testing / no worker)
```bash
python manage.py shell -c "from computing.gbif.biodiversity_task import generate_biodiversity_block as g; print(g('karnataka','hassan','hassan',1))"
```
*What:* runs the entire pipeline in the foreground and prints the result, e.g.
`biodiversity done for hassan_hassan: layer_id=26 synced=True doi=10.15468/dl.4ebch5`.
*Why:* no Celery worker needed; you see the outcome directly. Takes ~5–10 min for a fresh block (the GBIF
download is prepared asynchronously by GBIF's servers).

**What this one command does internally** (all reused CoRE Stack helpers):
1. Derives the block bounding box from the MWS GEE asset.
2. Downloads all GBIF occurrences in that bbox (cached under `computing/gbif/_data/`).
3. Cleans coordinates + enriches each species with its IUCN category (cached in `computing/gbif/_cache/`).
4. Uploads the points to GEE, joins them to the MWS polygons, computes ~16 indicators per MWS.
5. Exports the result to a GEE asset (idempotent — skips recompute if the asset already exists).
6. Registers a `Layer` row (+ `Layer.misc` provenance) and publishes the GeoPackage layer to GeoServer with the style.

---

## 2. Verify the run

### 2.1 Confirm the published layer (WFS)
```bash
curl -s "http://localhost:8080/geoserver/biodiversity/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=biodiversity:hassan_hassan_biodiversity&outputFormat=application/json&maxFeatures=2" | python3 -m json.tool | head -40
```
*What:* fetches the per-MWS features from GeoServer.
*Why:* confirms the layer is published, has one feature per MWS, and carries **full field names** with values
(`species_richness`, `threatened_species_count`, `dominant_class`, `biodiversity_category`, `data_poor`, …).
The layer name is `<district>_<block>_biodiversity` (here `hassan_hassan_biodiversity`).

### 2.2 Generate the Excel sheet
```bash
python manage.py shell -c "from stats_generator.utils import get_vector_layer_geoserver as x; x('karnataka','hassan','hassan', specific_sheets=['biodiversity'])"
```
*What:* reads the GeoServer layer and writes the `biodiversity` sheet into the block's stats Excel.
*Why:* KYL filters and the reports read the Excel sheet (not GeoServer directly). `specific_sheets=['biodiversity']`
generates only this sheet (doesn't need the block's other layers).

### 2.3 Inspect indicators from the DB / layer
```bash
# Layer + provenance stored in Layer.misc:
python manage.py shell -c "from computing.models import Layer; l=Layer.objects.filter(layer_name='hassan_hassan_biodiversity').latest('id'); print(l.id, l.is_sync_to_geoserver, l.misc)"
```
*What:* shows the registered `Layer` and its `misc` (DOI, download key, record counts, download date).
*Why:* confirms DB registration + provenance persisted.

### 2.4 Reports (per-MWS and block summary)
```bash
python manage.py shell -c "from dpr.gen_tehsil_report import get_biodiversity_summary_data as s; print(s('karnataka','hassan','hassan'))"
```
*What:* returns the block rollup (total MWS, data-poor count, average richness, MWS-with-threatened, top-5).
*Why:* confirms the tehsil report section is populated. (The per-MWS `get_biodiversity_data(state,district,block,uid)`
does the same for one watershed.)
*Note:* the report readers read from `EXCEL_DIR`; if that differs from where 2.2 wrote, copy the file there first
(see the EXCEL_PATH/EXCEL_DIR note in [`REVIEW.md`](REVIEW.md)).

---

## 3. See the map

### 3.1 Interactive (GeoServer Layer Preview)
Open in a browser:
```
http://localhost:8080/geoserver/web/
```
→ **Layer Preview** → find **`biodiversity:hassan_hassan_biodiversity`** → click **OpenLayers**.
*Why:* renders the richness choropleth; click any watershed to see its indicators. This is the local map viewer
(the production frontend consumes the same WMS/WFS layer + KYL JSON).

### 3.2 Rendered image (WMS GetMap)
```bash
python manage.py shell -c "
import requests
GS='http://localhost:8080/geoserver'; L='biodiversity:hassan_hassan_biodiversity'
ft=requests.get(requests.get(f'{GS}/rest/layers/{L}.json',auth=('admin','geoserver')).json()['layer']['resource']['href'],auth=('admin','geoserver')).json()['featureType']
bb=ft['latLonBoundingBox']; bbox=f\"{bb['minx']},{bb['miny']},{bb['maxx']},{bb['maxy']}\"
url=f'{GS}/biodiversity/wms?service=WMS&version=1.1.1&request=GetMap&layers={L}&styles=biodiversity_mws&bbox={bbox}&srs=EPSG:4326&width=900&height=900&format=image/png'
open('hassan_map.png','wb').write(requests.get(url,auth=('admin','geoserver')).content); print('wrote hassan_map.png')
"
```
*What:* renders the styled choropleth to `hassan_map.png`.
*Why:* a quick static check that the layer + SLD render. **Grey** = data-poor MWS (<20 records); **green ramp** =
species richness (light = Low → dark = Very High).

---

## 4. Re-running & idempotency

- **Re-running the same block is safe.** The GBIF download is cached on disk, and if the GEE asset already
  exists the compute/export step is **skipped** (`is_gee_asset_exists`), so it just re-registers + re-syncs.
- **To force a full recompute** (e.g. after code changes), delete the GEE asset first:
  ```bash
  python manage.py shell -c "
  from utilities.gee_utils import ee_initialize; from computing.gbif import config; import ee
  ee_initialize('1'); ee.data.deleteAsset(config.get_gee_block_asset_id('karnataka','hassan','hassan'))"
  ```
  *Why:* otherwise the idempotency guard reuses the existing (possibly stale) asset.
- **To re-download fresh GBIF data**, delete the block's cache: `rm -rf computing/gbif/_data/karnataka/hassan/hassan/`.

---

## 5. Common failures & what they mean

| Symptom | Cause | Fix |
|---|---|---|
| `filtered_mws_..._uid ... not found` / 0 MWS | The block has no MWS asset | Run the upstream `generate_mws_layer` for that block first |
| Aborts asking for `GBIF_USER/PWD/EMAIL` | GBIF creds not set | Add them to `nrm_app/.env` (§0.2) |
| `Dataset "Biodiversity Occurrence" not found` | Registry not loaded | Run §0.3 (`loaddata` / `register_biodiversity_layer`) |
| WFS/sync fails, GeoServer connection refused | GeoServer not running | Start the GeoServer container; check §0.4 |
| Map/WMS: `No such resource: biodiversity_mws.sld` | Style not provisioned | Run `python installation/setup_local_geoserver.py` (§0.4) |
| Report shows empty / `total_mws=0` | Excel written to a different dir than the reader reads | Reconcile `EXCEL_PATH` vs `EXCEL_DIR` (see [`REVIEW.md`](REVIEW.md)) |
| `Error making asset public: ... permitted customer` | GEE project IAM rejects the public binding | Harmless — GeoServer serves the data; not required for the layer |

---

## Quick copy-paste (happy path, existing GeoServer + registered DB)
```bash
conda activate corestackenv && cd /home/snaveen/Desktop/core-stack-backend
python installation/setup_local_geoserver.py
python manage.py shell -c "from computing.gbif.biodiversity_task import generate_biodiversity_block as g; print(g('karnataka','hassan','hassan',1))"
python manage.py shell -c "from stats_generator.utils import get_vector_layer_geoserver as x; x('karnataka','hassan','hassan', specific_sheets=['biodiversity'])"
# then open GeoServer Layer Preview -> biodiversity:hassan_hassan_biodiversity
```
</content>

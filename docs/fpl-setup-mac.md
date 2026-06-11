# CoRE Stack: FPL Mac Setup

**Time:** ~30 minutes. Get `core-stack-key.json` from #fpl-esg before starting.

---

## 1. Prerequisites

```bash
brew install python@3.10 postgresql@14
brew services start postgresql@14
```

Install Docker Desktop from https://docker.com if you don't have it.

## 2. Clone

```bash
git clone https://github.com/fplaunchpad/core-stack-backend.git
cd core-stack-backend
```

## 3. Virtual environment + dependencies

```bash
python3.10 -m venv ../corestack-venv
source ../corestack-venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install \
  django==5.2.9 djangorestframework==3.15.2 django-cors-headers==4.7.0 \
  django-environ==0.12.0 django-celery-beat==2.8.1 \
  djangorestframework-simplejwt==5.5.0 djangorestframework-api-key==3.1.0 \
  drf-yasg drf-nested-routers \
  numpy==1.26 pandas==2.1.3 scipy matplotlib==3.8.2 \
  geopandas shapely pyproj rtree folium \
  earthengine-api==0.1.389 google-cloud-storage==2.14.0 \
  google-api-python-client google-auth \
  psycopg2-binary celery \
  requests pyyaml python-dotenv lxml openpyxl pillow \
  boto3 geojson pydantic tqdm orjson polars \
  rasterio fiona geemap cryptography
```

## 4. Database

```bash
createdb corestack
```

## 5. Configure .env

Create `nrm_app/.env`:

```env
DEBUG=True
SECRET_KEY=replace-with-any-long-random-string

DB_NAME=corestack
DB_USER=<your-mac-username>
DB_PASSWORD=

FERNET_KEY=<run: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

GEE_DEFAULT_ACCOUNT_ID=1
GEE_HELPER_ACCOUNT_ID=1
GEE_STORAGE_PROJECT=arcane-mason-493503-a6
GEE_STORAGE_PROJECT_HELPER=arcane-mason-493503-a6
GCS_BUCKET_NAME=fpl-core-stack-dev

GEOSERVER_URL=http://localhost:8080/geoserver
GEOSERVER_USERNAME=admin
GEOSERVER_PASSWORD=geoserver

DEPLOYMENT_DIR=.
TMP_LOCATION=./tmp
WHATSAPP_MEDIA_PATH=./bot_interface/whatsapp_media
EXCEL_DIR=./data/excel_files
EXCEL_PATH=.
LOCAL_COMPUTE_API_URL=http://localhost:8000
ADMIN_GROUP_ID=1
OVERPASS_URL=https://overpass-api.de/api/interpreter
```

## 6. Migrations + superuser

```bash
python manage.py migrate
python manage.py createsuperuser  # use username: admin
```

## 7. Admin boundary data

One-time ~8GB download of the pan-India admin boundary dataset. This is the same archive `install.sh` fetches, but that script is Linux-only (apt-get), so run the Mac equivalent directly:

```bash
brew install p7zip
pip install gdown
gdown 1VqIhB6HrKFDkDnlk1vedcEHhh5fk4f1d -O /tmp/admin-boundary.7z
7z x /tmp/admin-boundary.7z -o./data/ -y
rm /tmp/admin-boundary.7z
```

This populates `data/admin-boundary/input/` with state-wise district GeoJSONs and `soi_tehsil.geojson`. Note: extracted files carry old timestamps from the archive; that is normal.

Before running a computing pipeline test for a new block (e.g. `lulc_pipeline_test.py`), generate that block's boundary once; the exact command is in the docstring of `computing/misc/lulc_pipeline_test.py`.

## 8. Load GEE credentials

```bash
python manage.py shell -c "
from utilities.gee_utils import upsert_gee_account_from_json
upsert_gee_account_from_json('/path/to/core-stack-key.json', account_name='fpl-gee')
"
```

## 9. GeoServer

Start the container:

```bash
docker run -d --name geoserver -p 8080:8080 \
  -e GEOSERVER_ADMIN_PASSWORD=geoserver \
  kartoza/geoserver:2.25.2
```

Wait ~60s, then create workspaces and sync styles:

```bash
python installation/setup_local_geoserver.py

python installation/geoserver_style_bundle.py sync \
  --url http://localhost:8080/geoserver \
  --username admin --password geoserver
```

> The GeoServer container has no persistent storage. Repeat these two commands if you ever remove and recreate the container.

## 10. Verify

```bash
python computing/misc/internal_api_initialisation_test.py --require-gee
```

Expected: `Internal API initialisation test passed.`

---

## Daily use

```bash
source ../corestack-venv/bin/activate
docker start geoserver
python manage.py runserver 0.0.0.0:8001 --noreload
```

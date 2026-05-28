# CoRE Stack — Mac Setup Guide

The upstream `install.sh` targets Ubuntu 24.04 and will not run on macOS. This guide covers what to do instead.

**Time:** ~20 minutes

---

## Prerequisites

Install [Homebrew](https://brew.sh) if you don't have it, then:

```bash
brew install python@3.10 postgresql@14
brew services start postgresql@14
```

---

## 1. Clone and apply fixes

Clone from the FPL fork (which has two Mac bug fixes already applied):

```bash
git clone https://github.com/fplaunchpad/core-stack-backend.git
cd core-stack-backend
```

---

## 2. Create a virtual environment

```bash
python3.10 -m venv ../corestack-venv
source ../corestack-venv/bin/activate
```

---

## 3. Install dependencies

```bash
pip install --upgrade pip setuptools wheel

# Install from environment.yml as pip packages
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
  rasterio fiona geemap
```

> Note: `rasterio`, `fiona`, and GDAL ship pre-built Mac wheels and install cleanly via pip. No system GDAL needed.

---

## 4. Set up the database

```bash
# Create the database (uses your Mac username automatically)
createdb corestack
```

---

## 5. Configure .env

Create `nrm_app/.env`:

```env
DEBUG=True

DB_NAME=corestack
DB_USER=<your-mac-username>

# Generate a Fernet key (required for GEEAccount model)
# Run: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=<generated-key>

# Leave blank for local dev — GEE not needed for basic setup
GEE_DEFAULT_ACCOUNT_ID=
GEE_HELPER_ACCOUNT_ID=
GCS_BUCKET_NAME=
GEOSERVER_URL=

BACKEND_DIR=.
TMP_LOCATION=./tmp
DEPLOYMENT_DIR=.
WHATSAPP_MEDIA_PATH=./bot_interface/whatsapp_media
```

Generate the Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 6. Run migrations and create a superuser

```bash
python manage.py migrate
python manage.py createsuperuser
```

---

## 7. Download admin boundary data

```bash
bash installation/install.sh --only admin_boundary_data
```

This downloads the pan-India village boundary dataset (~1 GB). It is required for the local pipeline to work.

---

## 8. Verify the setup

```bash
python computing/misc/internal_api_initialisation_test.py
```

Expected output: all checks pass or warn (GEE/GCS/GeoServer will warn — that is expected without credentials).

---

## 9. Start the server

Port 8000 may be in use. Use 8001:

```bash
python manage.py runserver 0.0.0.0:8001 --noreload
```

- Admin panel: http://127.0.0.1:8001/admin/
- API explorer: http://127.0.0.1:8001/swagger/

---

## What works without GEE credentials

- Django admin panel and all 530 API endpoints (browsable)
- Admin boundary pipeline: clip village polygons for any state/district/block and generate shapefiles
- Folium map visualization of the output

## What needs GEE credentials

All spatial computation layers (LULC, drought, change detection, cropping intensity, etc.) call out to Google Earth Engine. To enable these, add a GEE service account via the admin panel at `/admin/gee_computing/geeaccount/add/`.

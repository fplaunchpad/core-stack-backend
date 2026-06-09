# CoRE Stack: FPL Linux Setup

**Time:** ~20 minutes. Get `core-stack-key.json` from #fpl-esg before starting.

---

## 1. Clone

```bash
git clone https://github.com/fplaunchpad/core-stack-backend.git
cd core-stack-backend
```

## 2. Start GeoServer

```bash
sudo apt-get install -y docker.io   # skip if Docker is already installed
sudo docker run -d --name geoserver -p 8080:8080 \
  -e GEOSERVER_ADMIN_PASSWORD=geoserver \
  kartoza/geoserver:2.25.2
```

Start this before the installer -- the installer waits for GeoServer to be ready before creating workspaces and syncing styles.

## 3. Run the installer

```bash
bash installation/install.sh \
  --gee-json /path/to/core-stack-key.json \
  --geoserver-config http://localhost:8080/geoserver,admin,geoserver
```

This handles everything: Miniconda, PostgreSQL, RabbitMQ, Python env, migrations, superuser (`admin`/`admin`), admin boundary data, GEE key loading, GeoServer workspaces, and styles.

## 4. Add FPL-specific .env vars

The installer generates `nrm_app/.env`. Append:

```env
GEE_STORAGE_PROJECT=arcane-mason-493503-a6
GEE_STORAGE_PROJECT_HELPER=arcane-mason-493503-a6
GCS_BUCKET_NAME=fpl-core-stack-dev
```

## 5. Verify

```bash
conda activate corestackenv
python computing/misc/internal_api_initialisation_test.py --require-gee
```

Expected: `Internal API initialisation test passed.`

---

## Daily use

```bash
conda activate corestackenv
sudo docker start geoserver
python manage.py runserver 0.0.0.0:8000 --noreload
```

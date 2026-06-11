# CoRE Stack: FPL Mac Setup

**Time:** ~30 minutes. Get `core-stack-key.json` from Sanjay (or DM in #fpl-esg) before starting.

---

## 1. Prerequisites

```bash
brew install python@3.10 postgresql@14
brew services start postgresql@14
```

Also install [Docker Desktop](https://docker.com) if you don't have it (needed only for GeoServer).

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
pip install -r requirements-fpl.txt
```

## 4. Database

```bash
createdb corestack
```

## 5. Configure .env

```bash
cp nrm_app/.env.fpl.example nrm_app/.env
```

Then open `nrm_app/.env` and fill in two values:

- `DB_USER` — your Mac username (`whoami` if unsure)
- `FERNET_KEY` — run this and paste the output:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Everything else in the file is pre-filled with FPL defaults.

## 6. Migrations + superuser

```bash
python manage.py migrate
python manage.py createsuperuser  # use username: admin
```

## 7. Admin boundary data

Loads India admin boundary shapefiles into the database. Takes ~2 minutes, needs internet.

```bash
bash installation/install.sh --only admin_boundary_data
```

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

Wait ~60 seconds, then run:

```bash
bash installation/setup_geoserver_local.sh
```

> **Note:** GeoServer has no persistent storage. If you ever remove and recreate the container, re-run the script above.

## 10. Verify

```bash
python computing/misc/internal_api_initialisation_test.py --require-gee
```

Expected output: `Internal API initialisation test passed.`

---

## Daily use

```bash
source ../corestack-venv/bin/activate
docker start geoserver
python manage.py runserver 0.0.0.0:8001 --noreload
```

---

## Companion files (commit alongside this doc)

- `requirements-fpl.txt` — pinned Python dependencies for FPL's setup
- `nrm_app/.env.fpl.example` — pre-filled env template; only `DB_USER` and `FERNET_KEY` need editing
- `installation/setup_geoserver_local.sh` — wraps the two GeoServer post-start commands

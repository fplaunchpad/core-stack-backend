# CoRE Stack: Installation & Daily Use

Reference guide for setting up and running the project on Linux. Full setup doc: `docs/fpl-setup-linux.md`.

---

## Prerequisites

- Docker installed (or install it in Step 2)
- `core-stack-key.json` GEE service account file (get from #fpl-esg Slack channel)

---

## One-Time Setup

### 1. Clone the repository

```bash
git clone https://github.com/fplaunchpad/core-stack-backend.git
cd core-stack-backend
```

### 2. Start GeoServer (Docker)

```bash
sudo apt-get install -y docker.io   # skip if Docker is already installed

sudo docker run -d --name geoserver -p 8080:8080 \
  -e GEOSERVER_ADMIN_PASSWORD=geoserver \
  kartoza/geoserver:2.25.2
```

Start GeoServer **before** running the installer. The installer waits for GeoServer to be reachable before creating workspaces and syncing styles.

### 3. Run the installer

```bash
bash installation/install.sh \
  --gee-json /path/to/core-stack-key.json \
  --geoserver-config http://localhost:8080/geoserver,admin,geoserver
```

#### What `install.sh` does (in order)

| Step | What happens |
|---|---|
| `unzip_install` | Installs the `unzip` system package |
| `miniconda` | Downloads and installs Miniconda to `~/miniconda3` |
| `postgres` | Installs PostgreSQL, creates user `corestack_admin` and database `corestack_db` |
| `rabbitmq` | Installs RabbitMQ (message broker for async tasks) |
| `conda_env` | Creates the `corestackenv` conda environment from `installation/environment.yml` |
| `env_file` | Generates `nrm_app/.env` with DB credentials, paths, and any provided API keys |
| `data_dir_path` | Sets `DATA_DIR` (and `EXCEL_DIR`) in `.env` to `/var/tmp/core-stack-data` |
| `geoserver` | Waits for GeoServer, then creates workspaces and pushes bundled SLD styles |
| `collectstatic` | Runs `manage.py collectstatic` to gather Django static files |
| `django_migrations` | Runs `manage.py migrate` to apply all database migrations |
| `seed_data` | Loads initial fixture/seed data into the database |
| `superuser` | Creates a test superuser (`admin` / `admin`) |
| `gee_configuration` | Loads the GEE service-account JSON and sets the GEE project |
| `gcs_bucket_configuration` | Configures the Google Cloud Storage bucket for GEE output |
| `admin_boundary_data` | Downloads and imports admin-boundary shapefiles (states, districts, tehsils) |
| `initialisation_check` | Runs `computing/misc/internal_api_initialisation_test.py` to verify the setup |
| `public_api_check` | Smoke-tests the public API endpoint |

The installer is **idempotent** -- completed steps are recorded in `.installation_state/` and skipped on re-runs. You can re-run from a specific step with `--from STEP` or run only selected steps with `--only STEP1,STEP2`.

### 4. Add FPL-specific environment variables

The installer creates `nrm_app/.env`. Append these three lines:

```env
GEE_STORAGE_PROJECT=arcane-mason-493503-a6
GEE_STORAGE_PROJECT_HELPER=arcane-mason-493503-a6
GCS_BUCKET_NAME=fpl-core-stack-dev
```

### 5. Verify the installation

```bash
conda activate corestackenv
python computing/misc/internal_api_initialisation_test.py --require-gee
```

Expected output: `Internal API initialisation test passed.`

---

## Daily Use

Every time you work on the project:

```bash
# 1. Activate the Python environment
conda activate corestackenv

# 2. Start GeoServer (if not already running)
sudo docker start geoserver

# 3. Start the Django dev server
python manage.py runserver 0.0.0.0:8000 --noreload
```

The API will be available at `http://localhost:8000`.

---

## Useful Installer Flags

```bash
# List all available installer steps
bash installation/install.sh --list-steps

# Re-run from a specific step (e.g. after a migration failure)
bash installation/install.sh --from django_migrations

# Run only specific steps
bash installation/install.sh --only seed_data,superuser

# Skip specific steps
bash installation/install.sh --skip admin_boundary_data
```

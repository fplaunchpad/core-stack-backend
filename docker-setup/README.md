# CoRE Stack on Docker (one script)

Sets up the whole CoRE Stack on one machine with Docker: the backend (API,
Celery workers, PostgreSQL, Redis, GeoServer) and both websites (Landscape
Explorer and the admin dashboard). It is meant for a shared lab server that
the ESG group uses, but works on any Linux machine.

The backend part is the official upstream Docker setup
(`docker-compose.yml` and `installation/DOCKER.md` in core-stack-backend).
This folder adds the two websites, per-person test copies, and one script
that does every step.

## Two kinds of copy

| | Shared copy | Personal copy |
| --- | --- | --- |
| Who | One per server, for the whole group | One per person who changes code |
| Code | upstream `main`, nobody edits it | your own clones and branches |
| Set up with | `./docker-setup/corestack.sh setup` | `./docker-setup/corestack.sh setup --personal` |
| Folder | `~/corestack` of whoever runs it | your own `~/corestack-dev` |
| Ports | 8000, 8080, 5432, 3000, 3001 | five of your own, for example 20030 to 20034 |
| RAM when idle (measured) | about 4.5 GB | about 2 GB (+2 GB while `web-dev` runs) |
| Disk | about 20 GB | about 1 GB (reuses the shared image and boundary data) |

Every copy has its own database, GeoServer, Redis and Docker network, so
nothing one person does can touch another copy's data.

**How many fit?** With 64 GB of RAM: the shared copy plus 8 personal copies
idle use about 21 GB, so there is room for several layer jobs and dev servers
at the same time. A stopped copy uses no RAM.

## What you need

| | Minimum | Recommended |
| --- | --- | --- |
| CPU | 4 cores | 8 to 16 cores |
| RAM | 16 GB (shared copy only) | 64 GB for a group of about 8 |
| Disk | 150 GB SSD | 500 GB to 1 TB SSD |
| OS | Linux x86_64 (Ubuntu 22.04 or 24.04) | |

Software: Docker Engine with the Compose plugin, `git` and `curl`. Every user
must be able to run `docker` without `sudo`. An admin does this once, and
adds each group member:

```bash
sudo apt install -y docker.io docker-compose-v2 git curl
sudo usermod -aG docker <username>
```

Then that person logs out and back in. Check it with `docker ps`; it should
not ask for `sudo`.

## Install the shared copy (once per server)

Copy this `docker-setup` folder to the server, then run:

```bash
./docker-setup/corestack.sh setup
```

That's it. The script:

1. Checks Docker, disk, RAM and ports.
2. Clones the backend and both websites into `~/corestack`.
3. Creates `nrm_app/.env` with new random passwords and an admin user. The
   passwords are never printed.
4. Builds and starts the backend. This downloads about 600 MB of admin
   boundaries and takes 20 to 60 minutes the first time.
5. Creates an API key for the explorer.
6. Builds and starts both websites.
7. Prints the addresses and a health check.

If it stops halfway (network drop, closed terminal), run the same command
again. It continues from where it was and keeps the existing settings.

To change the defaults, set them before the first run, for example:

```bash
CORESTACK_DIR=/srv/corestack BACKEND_PORT=8001 ./docker-setup/corestack.sh setup
```

The options are listed at the top of `corestack.sh`. After setup, all
settings live in `~/corestack/core-stack-backend/nrm_app/.env`.

Other users will read the shared admin-boundary data. If personal setup says
it is not readable, the shared copy's owner runs the `chmod` command it prints.

## Your own personal copy (to test changes)

Once the shared copy has been set up, each person copies this `docker-setup`
folder into their own home (Docker reads it as that person, and another
user's home is usually not readable), then runs, as themselves:

```bash
cp -r <path-to>/docker-setup ~/
cd ~
./docker-setup/corestack.sh setup --personal
```

This takes about 10 minutes. It clones fresh copies of the three repos into
`~/corestack-dev`, reuses the shared copy's backend image and boundary data,
and picks five ports from your user ID. It prints your addresses and the SSH
tunnel command; `./docker-setup/corestack.sh info` shows them again later.

After that, every command acts on your personal copy automatically (as long as
you have no `~/corestack` of your own; otherwise add `--personal`).

**Testing a change:**

| You changed | What to do |
| --- | --- |
| API code (`.py` files) | Nothing. The API reloads by itself a second after you save. |
| Celery task code | `./docker-setup/corestack.sh restart` (about 30 seconds) |
| Django models | `./docker-setup/corestack.sh migrate` makes and applies the migrations, then restarts the API and worker |
| A website | `./docker-setup/corestack.sh web-dev explorer` (or `dashboard`): a live dev server on the same port that reloads on every save. Ctrl+C stops it and brings back the built site. |
| A website, final check | `./docker-setup/corestack.sh rebuild` builds it the way the shared copy does |
| Python packages (`installation/environment.yml`) | Set `CORESTACK_BUILD_BACKEND=1` and `CORESTACK_IMAGE=corestack-backend-local:<your copy's name>` in your `nrm_app/.env`, then run `update`. This builds your own 11 GB image. |

Work on a branch in `~/corestack-dev/<repo>`, then push it and open a PR as
usual. `web-dev` runs `npm install`, which can change `package-lock.json`;
don't commit that by accident.

A personal copy is lighter than the shared one: the API runs Django's
development server, one Celery worker takes every queue, GeoServer gets a
smaller memory limit, and nothing starts by itself after a server reboot.

## Open it from your laptop

By default everything listens on `127.0.0.1` only, so nothing is open to the
network. From your laptop, open an SSH tunnel and leave it running. For the
shared copy:

```bash
ssh -N -L 3000:localhost:3000 -L 3001:localhost:3001 -L 8000:localhost:8000 -L 8080:localhost:8080 <user>@<server>
```

For a personal copy, `info` prints the same command with your ports.

| Address (shared copy) | What |
| --- | --- |
| http://localhost:3000 | Landscape Explorer (Know Your Landscape) |
| http://localhost:3001 | Admin dashboard |
| http://localhost:8000/admin/ | Django admin |
| http://localhost:8080/geoserver/ | Local GeoServer |

Log in as `admin`. The password is in the copy's settings file:

```bash
grep DJANGO_SUPERUSER_PASSWORD ~/corestack/core-stack-backend/nrm_app/.env
```

## Every day

```bash
./docker-setup/corestack.sh info            # addresses, ports, SSH tunnel command
./docker-setup/corestack.sh status          # what runs, and a health check
./docker-setup/corestack.sh stop            # stop everything, data is kept
./docker-setup/corestack.sh start           # start again
./docker-setup/corestack.sh logs backend    # follow one service's log
./docker-setup/corestack.sh update          # git pull all three repos and rebuild
```

In a personal copy, `update` skips a repo it cannot pull (for example one on
your own branch) and keeps that code as it is.

The shared copy restarts by itself after a server reboot; personal copies
don't. `stop` can take several minutes: Celery workers are given time to
finish the job they are running.

Please stop your personal copy when you are not using it, so the RAM is free
for others.

## Know Your Landscape data for a block

The explorer's KYL page needs a stats spreadsheet per block. This copies it
from the public CoRE Stack API into the copy you run it for:

```bash
./docker-setup/corestack.sh kyl bihar jamui jamui
```

Use the names as they appear in the explorer's dropdowns.

## Optional: Google Earth Engine and GBIF

Setup and the websites work without them. They are only needed to generate
new layers.

- **Earth Engine**: upload the service-account JSON key in Django admin. See
  "Google Earth Engine" in `core-stack-backend/installation/DOCKER.md`.
- **GBIF** (biodiversity layer only): add `GBIF_USER`, `GBIF_PWD` and
  `GBIF_EMAIL` to `nrm_app/.env`, then run
  `./docker-setup/corestack.sh compose up -d --force-recreate`.

## Good to know

- **Docker access is shared.** Anyone in the `docker` group can stop or
  delete any container on the server. Copies are kept apart by name, not by
  permissions, so only touch your own (`corestack-<your username>`).
- **Email.** The app emails people when users or organisations are created.
  No mail server is set up, so those emails fail instead of going out.
- **Passwords.** Don't change `DB_PASSWORD` or `GEOSERVER_PASSWORD` after the
  first start; the database and GeoServer keep the first ones.
- **Opening it to the network.** To reach it without SSH tunnels, set
  `BIND_IP=0.0.0.0 PUBLIC_HOST=<server-name>` on the first setup. Do this only
  behind the lab firewall: the backend runs with `DEBUG=True`.
- **Websites.** Settings for the websites are baked in when they are built.
  After changing `PUBLIC_HOST` or a port in `nrm_app/.env`, run
  `./docker-setup/corestack.sh rebuild`.
- **Shared image.** Personal copies use the image the shared copy built. When
  the shared copy runs `update`, personal copies pick up the new image the next
  time they run `update` or are recreated.
- **Remove a copy**:
  `./docker-setup/corestack.sh compose down -v` deletes its containers,
  database and GeoServer data. The code and `data/` in its folder stay.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Docker does not run without sudo` | Add your user to the `docker` group (see "What you need"), then log out and back in. |
| `BUSY ... is used by another program` | Another program has that port. Change the port in `nrm_app/.env` and run setup again. A personal copy can also use `PORT_BASE=<number> ./docker-setup/corestack.sh setup --personal` on the first run. |
| A setup job shows a non-zero exit code | `./docker-setup/corestack.sh logs <job>` shows why. Fix it, then run setup again. |
| Admin-boundary download fails | Google Drive limits downloads. Download it in a browser and save it as `~/corestack/core-stack-backend/data/dataset.7z`, then run setup again. |
| `No shared copy found` during personal setup | The shared copy has to be set up first. If it uses another project name, pass it: `SHARED_PROJECT=<name> ./docker-setup/corestack.sh setup --personal`. |
| `The shared backend image ... is gone` | Someone removed the shared image. Ask the shared copy's owner to run `update`, or build your own image (see "Testing a change"). |
| KYL filters are greyed out or the page crashes | No spreadsheet for that block. Run the `kyl` command above. |
| Behind a proxy | See "Behind a proxy" in `installation/DOCKER.md`. |
| Backend build fails with `failed to execute pre/post link script for gdk-pixbuf` | Recent Docker versions give builds a huge open-file limit that the backend's micromamba can't handle. `backend.compose.yml` in this folder caps it; build through `corestack.sh`, not plain `docker compose`. |

## Files in this folder

| File | What it does |
| --- | --- |
| `corestack.sh` | The script. Run `./corestack.sh help` for the commands. |
| `backend.compose.yml` | Gives each copy its own Docker network, and fixes the backend image build (see Troubleshooting). Used by every copy. |
| `personal.compose.yml` | The lighter settings for personal copies. |
| `shared-boundaries.compose.yml` | Mounts the shared copy's admin-boundary data read-only into a personal copy. |
| `frontend.compose.yml` | Adds the two websites to the backend's Docker Compose setup. |
| `frontend.Dockerfile`, `frontend.Dockerfile.dockerignore`, `nginx.conf` | Build each website and serve it with nginx. |

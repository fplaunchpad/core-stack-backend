#!/usr/bin/env bash
# CoRE Stack on Docker: one script to set up and run the backend (API, Celery,
# PostgreSQL, Redis, GeoServer) and both websites (Landscape Explorer, admin
# dashboard).
#
# Two kinds of install on one machine:
#   shared copy     one per machine, used by the whole group:  ./corestack.sh setup
#   personal copy   one per person, to test their own changes: ./corestack.sh setup --personal
#
#   ./corestack.sh setup [--personal]            first install
#   ./corestack.sh info                          addresses, ports, SSH tunnel command
#   ./corestack.sh status                        what is running, and is it healthy
#   ./corestack.sh start | stop                  start or stop everything (data is kept)
#   ./corestack.sh restart                       restart the API and workers (after code changes)
#   ./corestack.sh migrate                       make and apply migrations (after model changes)
#   ./corestack.sh rebuild [explorer|dashboard]  rebuild the websites after code changes
#   ./corestack.sh web-dev explorer|dashboard    live website dev server (reloads on save)
#   ./corestack.sh logs [service]                follow logs, e.g. logs backend
#   ./corestack.sh kyl <state> <district> <block>  add Know Your Landscape data for a block
#   ./corestack.sh update                        git pull all three repos and rebuild
#   ./corestack.sh compose <args>                any other docker compose command
#
# Which copy a command acts on: CORESTACK_DIR if set; with --personal,
# ~/corestack-dev; otherwise ~/corestack, or ~/corestack-dev when only that exists.
#
# Settings for the first setup (all optional, as environment variables):
#   CORESTACK_DIR   where the code goes      (default ~/corestack, personal ~/corestack-dev)
#   PUBLIC_HOST     host name browsers use   (default localhost, for SSH tunnels)
#   BIND_IP         127.0.0.1 or 0.0.0.0     (default 127.0.0.1, reachable only via SSH tunnel)
#   BACKEND_PORT GEOSERVER_PORT POSTGRES_PORT EXPLORER_PORT DASHBOARD_PORT
#                   shared copy: 8000 8080 5432 3000 3001
#                   personal copy: five ports from PORT_BASE (default 20000 + 10 x (uid % 1000))
#   ADMIN_USER ADMIN_EMAIL                   (defaults admin, admin@example.com)
#   BACKEND_BRANCH  backend branch to clone  (default main)
#   SHARED_PROJECT  personal copy: Compose project of the shared copy (default core-stack)
#
# After setup every setting lives in <CORESTACK_DIR>/core-stack-backend/nrm_app/.env.

set -euo pipefail

SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --personal / -p may appear anywhere on the command line.
PERSONAL=0
args=()
for a in "$@"; do
    case "$a" in
        --personal|-p) PERSONAL=1 ;;
        *) args+=("$a") ;;
    esac
done
set -- ${args[@]+"${args[@]}"}

if [ -z "${CORESTACK_DIR:-}" ]; then
    if [ "$PERSONAL" = 1 ]; then
        CORESTACK_DIR="$HOME/corestack-dev"
    elif [ ! -f "$HOME/corestack/core-stack-backend/nrm_app/.env" ] \
        && [ -f "$HOME/corestack-dev/core-stack-backend/nrm_app/.env" ]; then
        CORESTACK_DIR="$HOME/corestack-dev"
    else
        CORESTACK_DIR="$HOME/corestack"
    fi
fi
CORESTACK_DIR="$(realpath -m "$CORESTACK_DIR")"
BACKEND_DIR="$CORESTACK_DIR/core-stack-backend"
ENV_FILE="$BACKEND_DIR/nrm_app/.env"

BACKEND_REPO="${BACKEND_REPO:-https://github.com/core-stack-org/core-stack-backend.git}"
BACKEND_BRANCH="${BACKEND_BRANCH:-main}"
EXPLORER_REPO="${EXPLORER_REPO:-https://github.com/core-stack-org/landscape-explorer.git}"
DASHBOARD_REPO="${DASHBOARD_REPO:-https://github.com/core-stack-org/admin-dashboard.git}"
NODE_IMAGE="node:22-bookworm-slim"

# ---------------------------------------------------------------- helpers

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '    \033[33mWARN\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Read KEY from nrm_app/.env without sourcing it (values may contain $).
getenv() {
    [ -f "$ENV_FILE" ] || return 0
    grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- | sed -e "s/^['\"]//" -e "s/['\"]\$//" || true
}

# Set KEY=VALUE in nrm_app/.env (replace the line, or append it).
setenv() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp "$ENV_FILE.XXXXXX")"
    awk -v k="$key" -v v="$value" '
        BEGIN { done = 0 }
        index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 } ; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
}

random_password() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24 || true; }

project() { local p; p="$(getenv CORESTACK_PROJECT)"; echo "${p:-core-stack}"; }
is_personal() { [ "$(getenv CORESTACK_MODE)" = personal ]; }
builds_backend() { [ "$(getenv CORESTACK_BUILD_BACKEND)" != 0 ]; }

# Compose files for this copy. The backend's own docker-compose.yml first,
# then the overlays in this folder (see README.md, "Files in this folder").
COMPOSE_FILES=()
set_compose_files() {
    COMPOSE_FILES=(-f "$BACKEND_DIR/docker-compose.yml" -f "$SETUP_DIR/backend.compose.yml")
    if is_personal; then COMPOSE_FILES+=(-f "$SETUP_DIR/personal.compose.yml"); fi
    if [ -n "$(getenv CORESTACK_SHARED_ADMIN_DIR)" ]; then
        COMPOSE_FILES+=(-f "$SETUP_DIR/shared-boundaries.compose.yml")
    fi
}

# Backend only.
compose_backend() {
    set_compose_files
    docker compose -p "$(project)" --project-directory "$BACKEND_DIR" \
        --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" "$@"
}

# Backend plus the two websites.
compose_all() {
    set_compose_files
    docker compose -p "$(project)" --project-directory "$BACKEND_DIR" \
        --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" -f "$SETUP_DIR/frontend.compose.yml" "$@"
}

need_install() { [ -f "$ENV_FILE" ] || die "No CoRE Stack install in $CORESTACK_DIR. Run: $0 setup   (or $0 setup --personal)"; }

port_in_use() { ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"; }

http_code() { curl -s -o /dev/null -m 10 -w '%{http_code}' --noproxy '*' "$1" || true; }

# ---------------------------------------------------------------- setup steps

check_prerequisites() {
    say "Checking this machine"
    local missing=()
    for cmd in git curl docker awk ss; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    [ ${#missing[@]} -eq 0 ] || die "Missing commands: ${missing[*]}. Install them first (see README.md)."
    ok "git, curl, docker found"

    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing (the 'docker compose' command). Install the docker-compose-plugin package."
    ok "$(docker compose version)"

    docker info >/dev/null 2>&1 || die "Docker does not run without sudo for $(id -un). Ask an admin to run: sudo usermod -aG docker $(id -un)   then log out and back in."
    ok "Docker works without sudo"

    # A personal copy shares the big image and boundary data, so it needs less.
    local need=30 want=100
    if [ "$PERSONAL" = 1 ]; then need=10; want=30; fi
    local free_gb
    free_gb=$(df -Pk "$CORESTACK_DIR" | awk 'NR==2 {print int($4/1024/1024)}')
    if [ "$free_gb" -lt "$need" ]; then
        die "Only ${free_gb} GB free in $CORESTACK_DIR. Need at least $need GB."
    elif [ "$free_gb" -lt "$want" ]; then
        warn "${free_gb} GB free in $CORESTACK_DIR. Enough to start; layers and data grow over time ($want+ GB is comfortable)."
    else
        ok "${free_gb} GB free disk in $CORESTACK_DIR"
    fi

    local mem_gb avail_gb
    mem_gb=$(awk '/MemTotal/ {print int($2/1024/1024)}' /proc/meminfo)
    avail_gb=$(awk '/MemAvailable/ {print int($2/1024/1024)}' /proc/meminfo)
    if [ "$PERSONAL" = 1 ]; then
        if [ "$avail_gb" -lt 3 ]; then
            warn "Only ${avail_gb} GB RAM available right now. A personal copy needs about 2 to 3 GB; stop unused copies first."
        else
            ok "${avail_gb} GB RAM available"
        fi
    elif [ "$mem_gb" -lt 12 ]; then
        warn "Only ${mem_gb} GB RAM. 16 GB is the minimum; expect slowness or crashes."
    else
        ok "${mem_gb} GB RAM"
    fi
}

# Personal copy: find the shared copy's backend image and admin-boundary data
# through Docker, so the person needs no access to the shared copy's folder.
SHARED_IMAGE=""
SHARED_ADMIN_DIR=""
find_shared_copy() {
    local sp="${SHARED_PROJECT:-core-stack}" cid data
    cid="$(docker ps -a -q --filter "label=com.docker.compose.project=$sp" \
        --filter "label=com.docker.compose.service=backend" | head -n1)"
    if [ -z "$cid" ]; then
        warn "No shared copy found (Compose project '$sp'). This copy builds its own backend image and downloads its own boundary data (about 8 GB)."
        return 0
    fi
    SHARED_IMAGE="$(docker inspect -f '{{.Config.Image}}' "$cid")"
    data="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/tmp/core-stack-data"}}{{.Source}}{{end}}{{end}}' "$cid")"
    ok "Shared copy found: project '$sp', image $SHARED_IMAGE"
    [ -n "$data" ] || { warn "Could not find the shared copy's data folder; this copy downloads its own boundary data."; return 0; }
    # Check it is readable as this user, the same way the containers will read it.
    if docker run --rm --user "$(id -u):$(id -g)" --entrypoint test \
        -v "$data/base_layers/admin-boundary:/x:ro" "$SHARED_IMAGE" \
        -r /x/input/soi_tehsil.geojson 2>/dev/null; then
        SHARED_ADMIN_DIR="$data/base_layers/admin-boundary"
        ok "Using the shared admin-boundary data (read-only): $SHARED_ADMIN_DIR"
    else
        warn "The shared admin-boundary data in $data is not readable for $(id -un). This copy downloads its own (about 8 GB)."
        warn "To share it, the shared copy's owner can run: chmod -R a+rX $data/base_layers/admin-boundary"
    fi
}

check_ports() {
    # Skip when our own containers are already running (re-running setup).
    if [ -n "$(compose_all ps -q 2>/dev/null || true)" ]; then
        return 0
    fi
    say "Checking ports"
    local busy=0 name port
    for name in BACKEND_PORT GEOSERVER_PORT POSTGRES_PORT EXPLORER_PORT DASHBOARD_PORT; do
        port="$(getenv "$name")"
        if port_in_use "$port"; then
            printf '    \033[31mBUSY\033[0m %s=%s is used by another program\n' "$name" "$port"
            busy=1
        else
            ok "$name=$port is free"
        fi
    done
    [ "$busy" = 0 ] || die "Pick free ports in $ENV_FILE (the lines above), then run setup again."
}

get_code() {
    say "Getting the code into $CORESTACK_DIR"
    if [ -d "$BACKEND_DIR/.git" ]; then
        ok "core-stack-backend already there (not changed; use '$0 update' to pull)"
    else
        git clone --branch "$BACKEND_BRANCH" "$BACKEND_REPO" "$BACKEND_DIR"
    fi
    [ -f "$BACKEND_DIR/docker-compose.yml" ] || die "$BACKEND_DIR has no docker-compose.yml. Use a backend branch that includes the Docker setup (upstream main does)."

    local name repo
    for name in landscape-explorer admin-dashboard; do
        repo="$EXPLORER_REPO"; [ "$name" = admin-dashboard ] && repo="$DASHBOARD_REPO"
        if [ -d "$CORESTACK_DIR/$name/.git" ]; then
            ok "$name already there (not changed)"
        else
            git clone "$repo" "$CORESTACK_DIR/$name"
        fi
    done
}

write_env() {
    say "Settings file nrm_app/.env"
    if [ -f "$ENV_FILE" ]; then
        ok "Already exists; keeping your values"
    else
        local bind="${BIND_IP:-127.0.0.1}" host="${PUBLIC_HOST:-localhost}"
        local proj mode base bport gport pport eport dport
        if [ "$PERSONAL" = 1 ]; then
            mode=personal
            proj="${CORESTACK_PROJECT:-corestack-$(id -un | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_\n-' '-')}"
            base="${PORT_BASE:-$((20000 + ($(id -u) % 1000) * 10))}"
            bport="${BACKEND_PORT:-$base}"
            gport="${GEOSERVER_PORT:-$((base + 1))}"
            pport="${POSTGRES_PORT:-$((base + 2))}"
            eport="${EXPLORER_PORT:-$((base + 3))}"
            dport="${DASHBOARD_PORT:-$((base + 4))}"
            find_shared_copy
        else
            mode=full
            proj="${CORESTACK_PROJECT:-core-stack}"
            bport="${BACKEND_PORT:-8000}"; gport="${GEOSERVER_PORT:-8080}"; pport="${POSTGRES_PORT:-5432}"
            eport="${EXPLORER_PORT:-3000}"; dport="${DASHBOARD_PORT:-3001}"
        fi

        cp "$BACKEND_DIR/installation/docker/env.template" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        setenv DB_PASSWORD "$(random_password)"
        setenv GEOSERVER_PASSWORD "$(random_password)"
        setenv DJANGO_SUPERUSER_USERNAME "${ADMIN_USER:-admin}"
        setenv DJANGO_SUPERUSER_EMAIL "${ADMIN_EMAIL:-admin@example.com}"
        setenv DJANGO_SUPERUSER_PASSWORD "$(random_password)"
        setenv BACKEND_PORT "$bport"
        setenv GEOSERVER_PORT "$gport"
        setenv POSTGRES_PORT "$pport"
        setenv BACKEND_BIND_IP "$bind"
        setenv GEOSERVER_BIND_IP "$bind"
        setenv ALLOWED_HOSTS "localhost,127.0.0.1,backend,0.0.0.0,[::1]$( [ "$host" = localhost ] || echo ",$host")"
        {
            echo
            echo "# ---- Added by docker-setup/corestack.sh ----"
            echo "# full = shared copy for the group; personal = one person's test copy."
            echo "CORESTACK_MODE=$mode"
            echo "CORESTACK_PROJECT=$proj"
            echo "CORESTACK_NETWORK=$proj-network"
            echo "# Host name that browsers use to reach this machine. localhost = SSH tunnel."
            echo "PUBLIC_HOST=$host"
            echo "FRONTEND_BIND_IP=$bind"
            echo "EXPLORER_PORT=$eport"
            echo "DASHBOARD_PORT=$dport"
            echo "# Map layers in the explorer. Public CoRE Stack GeoServer has every block."
            echo "EXPLORER_GEOSERVER_URL=https://geoserver.core-stack.org:8443/geoserver/"
            echo "# Created by setup in the local database; baked into the explorer build."
            echo "EXPLORER_API_KEY="
            if [ "$mode" = personal ]; then
                echo "# Personal copy: nothing restarts by itself after a reboot."
                echo "FRONTEND_RESTART=no"
                if [ -n "$SHARED_IMAGE" ]; then
                    echo "# Backend image built by the shared copy; this copy never rebuilds it."
                    echo "# Changed installation/environment.yml? Set CORESTACK_BUILD_BACKEND=1 and"
                    echo "# CORESTACK_IMAGE=corestack-backend-local:$proj, then run update."
                    echo "CORESTACK_IMAGE=$SHARED_IMAGE"
                    echo "CORESTACK_BUILD_BACKEND=0"
                else
                    echo "CORESTACK_IMAGE=corestack-backend-local:$proj"
                    echo "CORESTACK_BUILD_BACKEND=1"
                fi
                if [ -n "$SHARED_ADMIN_DIR" ]; then
                    echo "# Admin-boundary data of the shared copy, mounted read-only."
                    echo "CORESTACK_SHARED_ADMIN_DIR=$SHARED_ADMIN_DIR"
                fi
            else
                echo "CORESTACK_BUILD_BACKEND=1"
            fi
        } >> "$ENV_FILE"
        ok "Created with new random passwords (not printed)"
    fi
    # These two follow wherever this folder and the code are, on every run.
    [ "$(getenv CORESTACK_SETUP_DIR)" = "$SETUP_DIR" ] || setenv CORESTACK_SETUP_DIR "$SETUP_DIR"
    [ "$(getenv CORESTACK_FRONTEND_DIR)" = "$CORESTACK_DIR" ] || setenv CORESTACK_FRONTEND_DIR "$CORESTACK_DIR"
    # Mount point for the shared boundary data, made by us so Docker does not
    # create it as root inside the checkout.
    if [ -n "$(getenv CORESTACK_SHARED_ADMIN_DIR)" ]; then
        mkdir -p "$BACKEND_DIR/data/base_layers/admin-boundary"
    fi
}

# A copy that uses the shared image must never build it: Compose would build
# a missing image under the shared name.
ensure_shared_image() {
    builds_backend && return 0
    local image; image="$(getenv CORESTACK_IMAGE)"
    docker image inspect "$image" >/dev/null 2>&1 \
        || die "The shared backend image $image is gone. Ask the owner of the shared copy, or build your own: set CORESTACK_BUILD_BACKEND=1 and CORESTACK_IMAGE=corestack-backend-local:$(project) in $ENV_FILE, then run setup again."
}

start_backend() {
    ensure_shared_image
    if builds_backend; then
        say "Building and starting the backend (first time: 20 to 60 minutes)"
        echo "    Downloads about 600 MB of admin boundaries and builds a 4 GB image. Let it run."
        compose_backend up -d --build
    else
        say "Starting the backend with the shared image (first time: 2 to 10 minutes)"
        compose_backend up -d
    fi
    wait_backend_healthy
    check_init_jobs
}

wait_backend_healthy() {
    local i state
    for i in $(seq 1 120); do
        state="$(docker inspect -f '{{.State.Health.Status}}' "$(compose_backend ps -q backend 2>/dev/null)" 2>/dev/null || true)"
        [ "$state" = healthy ] && { ok "backend is healthy"; return 0; }
        sleep 10
    done
    die "backend did not become healthy in 20 minutes. Look at: $0 logs backend"
}

check_init_jobs() {
    local job code failed=0
    for job in app-init database-init geoserver-init data-download gee-config tehsil-watershed-setup; do
        code="$(docker inspect -f '{{.State.ExitCode}}' "$(compose_backend ps -a -q "$job" 2>/dev/null)" 2>/dev/null || echo '?')"
        if [ "$code" = 0 ]; then ok "$job finished"; else warn "$job exit code $code (see: $0 logs $job)"; failed=1; fi
    done
    [ "$failed" = 0 ] || warn "Some setup jobs failed. The stack may still work; check the logs above."
}

create_api_key() {
    say "API key for the explorer"
    if [ -n "$(getenv EXPLORER_API_KEY)" ]; then
        ok "Already set"
        return 0
    fi
    local user key
    user="$(getenv DJANGO_SUPERUSER_USERNAME)"
    [ -n "$user" ] || die "DJANGO_SUPERUSER_USERNAME is empty in $ENV_FILE"
    key="$(compose_backend exec -T --user "$(id -u):$(id -g)" -e CS_USER="$user" backend \
        python manage.py shell -c '
import os
from django.contrib.auth import get_user_model
from geoadmin.models import UserAPIKey
u = get_user_model().objects.get(username=os.environ["CS_USER"])
_, key = UserAPIKey.objects.create_key(user=u, name="docker-landscape-explorer")
print("CORESTACK_KEY=" + key)' 2>/dev/null | sed -n 's/^CORESTACK_KEY=//p' | tail -1)"
    [ -n "$key" ] || die "Could not create the API key. Try: $0 compose exec backend python manage.py shell"
    setenv EXPLORER_API_KEY "$key"
    ok "Created and saved in nrm_app/.env (not printed)"
}

start_frontends() {
    say "Building and starting the websites (5 to 15 minutes)"
    compose_all up -d --build explorer dashboard
}

print_summary() {
    local host bport eport dport gport user
    host="$(getenv PUBLIC_HOST)"; host="${host:-localhost}"
    bport="$(getenv BACKEND_PORT)"; gport="$(getenv GEOSERVER_PORT)"
    eport="$(getenv EXPLORER_PORT)"; dport="$(getenv DASHBOARD_PORT)"
    user="$(getenv DJANGO_SUPERUSER_USERNAME)"
    say "Health check"
    show_health
    cat <<EOF

CoRE Stack $(is_personal && echo "personal copy '$(project)'" || echo "copy '$(project)'") in $CORESTACK_DIR

  Landscape Explorer   http://$host:$eport
  Admin dashboard      http://$host:$dport     (log in as: $user)
  API + Django admin   http://$host:$bport/admin/
  Local GeoServer      http://$host:$gport/geoserver/

  Admin password:  grep DJANGO_SUPERUSER_PASSWORD $ENV_FILE

From your laptop, open an SSH tunnel first (if PUBLIC_HOST is localhost):
  ssh -N -L $eport:localhost:$eport -L $dport:localhost:$dport -L $bport:localhost:$bport -L $gport:localhost:$gport $(id -un)@<this-server>

Know Your Landscape needs data per block, for example:
  $0 kyl bihar jamui jamui
EOF
    if is_personal; then
        cat <<EOF

Testing your changes:
  API (.py files)     $BACKEND_DIR   reloads by itself
  Celery task code    then run: $0 restart
  Websites            $0 web-dev explorer   (live reload)   or   $0 rebuild
EOF
    fi
}

show_health() {
    local host bport eport dport gport
    host=127.0.0.1
    bport="$(getenv BACKEND_PORT)"; gport="$(getenv GEOSERVER_PORT)"
    eport="$(getenv EXPLORER_PORT)"; dport="$(getenv DASHBOARD_PORT)"
    check_url() {
        local label="$1" url="$2" code
        code="$(http_code "$url")"
        case "$code" in
            200|301|302) ok "$label  $url  ($code)" ;;
            *) warn "$label  $url  (got ${code:-no answer})" ;;
        esac
    }
    check_url "API       " "http://$host:$bport/api/v1/get_states/"
    check_url "GeoServer " "http://$host:$gport/geoserver/web/"
    check_url "Explorer  " "http://$host:$eport/"
    check_url "Dashboard " "http://$host:$dport/"
}

# ---------------------------------------------------------------- commands

# A copy that uses the shared backend image may build only its websites.
guard_compose_args() {
    builds_backend && return 0
    local a sub="" build=0 named=0 other=0
    for a in "$@"; do
        case "$a" in
            --build) build=1 ;;
            -*) ;;
            *)
                if [ -z "$sub" ]; then
                    sub="$a"; [ "$a" = build ] && build=1
                elif [ "$a" = explorer ] || [ "$a" = dashboard ]; then
                    named=1
                else
                    other=1
                fi ;;
        esac
    done
    if [ "$build" = 1 ] && { [ "$named" = 0 ] || [ "$other" = 1 ]; }; then
        die "This copy uses the shared backend image and must not build it. Rebuild the websites with: $0 rebuild. To build your own backend image, see README.md (\"Testing a change\")."
    fi
    return 0
}

cmd_setup() {
    if [ -f "$ENV_FILE" ]; then
        # An existing copy keeps its kind, whatever the flag says.
        if is_personal; then PERSONAL=1; else PERSONAL=0; fi
    fi
    mkdir -p "$CORESTACK_DIR"
    check_prerequisites
    get_code
    write_env
    check_ports
    start_backend
    create_api_key
    start_frontends
    print_summary
}

cmd_status() {
    need_install
    compose_all ps -a --format 'table {{.Service}}\t{{.Status}}'
    say "Health check"
    show_health
}

cmd_restart() {
    need_install
    local services
    services="$(compose_all ps --services --status running | grep -E '^(backend|celery-)' || true)"
    [ -n "$services" ] || die "Nothing is running. Start it with: $0 start"
    say "Restarting: $(echo "$services" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    compose_all restart $services
    wait_backend_healthy
}

cmd_migrate() {
    need_install
    say "Making and applying database migrations (database-init)"
    compose_backend start database-init
    local cid code
    cid="$(compose_backend ps -a -q database-init)"
    code="$(docker wait "$cid")"
    compose_backend logs --no-log-prefix --tail 25 database-init
    [ "$code" = 0 ] || die "database-init failed (exit code $code). See the log above."
    ok "Migrations applied"
    cmd_restart
}

cmd_rebuild() {
    need_install
    local apps=("$@")
    [ ${#apps[@]} -gt 0 ] || apps=(explorer dashboard)
    for a in "${apps[@]}"; do
        case "$a" in explorer|dashboard) ;; *) die "rebuild takes explorer and/or dashboard, not '$a'" ;; esac
    done
    say "Rebuilding ${apps[*]} (a few minutes)"
    compose_all up -d --build "${apps[@]}"
    show_health
}

cmd_webdev() {
    need_install
    [ $# -eq 1 ] || die "Usage: $0 web-dev explorer|dashboard"
    local app="$1" dir port host bport gport bind envfile tty=()
    host="$(getenv PUBLIC_HOST)"; host="${host:-localhost}"
    bport="$(getenv BACKEND_PORT)"; gport="$(getenv GEOSERVER_PORT)"
    bind="$(getenv FRONTEND_BIND_IP)"; bind="${bind:-127.0.0.1}"
    envfile="$(mktemp)"; chmod 600 "$envfile"
    case "$app" in
        explorer)
            dir="$CORESTACK_DIR/landscape-explorer"; port="$(getenv EXPLORER_PORT)"
            {
                echo "REACT_APP_API_URL=http://$host:$bport/api/v1"
                echo "REACT_APP_BASEURL=http://$host:$bport"
                echo "REACT_APP_GEOSERVER_URL=$(getenv EXPLORER_GEOSERVER_URL)"
                echo "REACT_APP_API_KEY=$(getenv EXPLORER_API_KEY)"
            } > "$envfile" ;;
        dashboard)
            dir="$CORESTACK_DIR/admin-dashboard"; port="$(getenv DASHBOARD_PORT)"
            {
                echo "REACT_APP_BASEURL=http://$host:$bport/"
                echo "REACT_APP_LOCAL_API_BASE_URL=http://$host:$bport"
                echo "REACT_APP_IMAGE_LAYER_URL=http://$host:$gport/geoserver/"
            } > "$envfile" ;;
        *) rm -f "$envfile"; die "Usage: $0 web-dev explorer|dashboard" ;;
    esac
    [ -t 0 ] && tty=(-t)

    say "Live dev server for $app on port $port"
    echo "    Edit files under $dir/src; the browser reloads by itself."
    echo "    The first start installs packages (a few minutes). Ctrl+C stops it and brings back the built site."
    compose_all stop "$app" >/dev/null 2>&1 || true
    # shellcheck disable=SC2064
    trap "rm -f '$envfile'; compose_all start '$app' >/dev/null 2>&1 || true" EXIT
    docker run --rm -i --init "${tty[@]}" --name "$(project)-$app-dev" \
        --user "$(id -u):$(id -g)" -e HOME=/tmp -e TINI_KILL_PROCESS_GROUP=1 \
        -e BROWSER=none -e PORT=3000 -e WDS_SOCKET_PORT="$port" \
        -e DANGEROUSLY_DISABLE_HOST_CHECK=true -e DISABLE_ESLINT_PLUGIN=true \
        --env-file "$envfile" \
        -v "$dir:/src" -w /src -p "$bind:$port:3000" \
        "$NODE_IMAGE" sh -c 'npm install --no-audit --no-fund --loglevel=error && exec npm start'
}

cmd_kyl() {
    need_install
    [ $# -eq 3 ] || die "Usage: $0 kyl <state> <district> <block>   e.g. $0 kyl bihar jamui jamui"
    local s d b dir file
    # Names as the public API wants them: lowercase, spaces as underscores.
    s="$(echo "$1" | tr '[:upper:] ' '[:lower:]_')"
    d="$(echo "$2" | tr '[:upper:] ' '[:lower:]_')"
    b="$(echo "$3" | tr '[:upper:] ' '[:lower:]_')"
    dir="$BACKEND_DIR/data/stats_excel_files/$(echo "$s" | tr '[:lower:]' '[:upper:]')/$(echo "$d" | tr '[:lower:]' '[:upper:]')"
    file="$dir/${d}_${b}.xlsx"
    say "Know Your Landscape data for $s / $d / $b"
    mkdir -p "$dir"
    curl -fsSL -m 600 -o "$file.part" \
        "https://geoserver.core-stack.org/api/v1/download_excel_layer/?state=$s&district=$d&block=$b" \
        || { rm -f "$file.part"; die "Download failed. Check the names (they must match the explorer's dropdowns)."; }
    if [ "$(head -c 2 "$file.part")" != "PK" ]; then
        rm -f "$file.part"
        die "The public API did not return a spreadsheet for this block. Check the names."
    fi
    mv "$file.part" "$file"
    rm -f "$dir/${d}_${b}"_KYL_*.json   # old cached results
    ok "Saved $file ($(du -h "$file" | cut -f1))"
    echo "    Open the explorer, pick this block and click Know Your Landscape."
}

cmd_update() {
    need_install
    local repo
    for repo in "$BACKEND_DIR" "$CORESTACK_DIR/landscape-explorer" "$CORESTACK_DIR/admin-dashboard"; do
        say "git pull in $repo"
        if ! git -C "$repo" pull --ff-only; then
            # A personal copy is often on a branch of its own; keep its code as is.
            is_personal || die "git pull failed in $repo. The shared copy should stay on main with no local changes."
            warn "Not pulled (own branch or local commits); using the code as it is."
        fi
    done
    if builds_backend; then
        say "Rebuilding the backend"
        compose_backend up -d --build --force-recreate
    else
        ensure_shared_image
        say "Restarting the backend on the new code (the shared image is not rebuilt)"
        compose_backend up -d --force-recreate
    fi
    wait_backend_healthy
    say "Rebuilding the websites"
    compose_all up -d --build --force-recreate explorer dashboard
    show_health
}

case "${1:-help}" in
    setup)   cmd_setup ;;
    info)    need_install; print_summary ;;
    status)  cmd_status ;;
    start)   need_install; ensure_shared_image; compose_all up -d; show_health ;;
    stop)    need_install; compose_all stop ;;
    restart) cmd_restart ;;
    migrate) cmd_migrate ;;
    rebuild) shift; cmd_rebuild "$@" ;;
    web-dev) shift; cmd_webdev "$@" ;;
    logs)    need_install; shift; compose_all logs -f --tail=100 "$@" ;;
    kyl)     shift; cmd_kyl "$@" ;;
    update)  cmd_update ;;
    compose) need_install; shift; guard_compose_args "$@"; ensure_shared_image; compose_all "$@" ;;
    help|-h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0" ;;
    *) die "Unknown command '$1'. Run: $0 help" ;;
esac

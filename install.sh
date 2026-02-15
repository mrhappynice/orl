#!/usr/bin/env bash
set -Eeuo pipefail

# -------------------- config --------------------
SITE_NAME="orl"
SITE_AVAILABLE="/etc/nginx/sites-available/${SITE_NAME}"
SITE_ENABLED="/etc/nginx/sites-enabled/${SITE_NAME}"

DOCKER_KEYRING_DIR="/etc/apt/keyrings"
DOCKER_KEYRING="${DOCKER_KEYRING_DIR}/docker.asc"
DOCKER_SOURCES="/etc/apt/sources.list.d/docker.sources"

ORL_REPO_URL="https://github.com/mrhappynice/orl.git"
ORL_DIR="/opt/orl"

# health/wait settings
DOCKER_WAIT_SECS=60
PORT_WAIT_SECS=120
HTTP_WAIT_SECS=60

# expected local upstream ports used by nginx config
UPSTREAM_PORTS=(5880 8090)

# -------------------- helpers --------------------
log()  { echo -e "\n[+] $*"; }
warn() { echo -e "\n[!] $*" >&2; }
die()  { echo -e "\n[✗] $*" >&2; exit 1; }

need_root() { [[ "${EUID}" -eq 0 ]] || die "Run as root (use sudo)."; }

usage() {
  cat >&2 <<'EOF'
Usage:
  sudo ./setup_orl.sh <ip_address_or_hostname>

Examples:
  sudo ./setup_orl.sh 203.0.113.10
  sudo ./setup_orl.sh my.example.com
EOF
  exit 2
}

is_ipv4() {
  local ip="$1"
  [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS='.' read -r o1 o2 o3 o4 <<<"$ip"
  for o in "$o1" "$o2" "$o3" "$o4"; do
    [[ "$o" -ge 0 && "$o" -le 255 ]] || return 1
  done
  return 0
}

is_hostname() {
  local h="$1"
  [[ "$h" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]
}

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

apt_update() {
  log "Running apt update"
  apt-get update -y >/dev/null
  log "apt update OK"
}

apt_install() {
  local pkgs=("$@")
  log "Installing packages: ${pkgs[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}" >/dev/null
  log "Package install OK: ${pkgs[*]}"
}

verify_pkg() {
  local pkg="$1"
  dpkg -s "$pkg" >/dev/null 2>&1 || die "Package not installed or not healthy: $pkg"
  log "Verified package installed: $pkg"
}

wait_for_docker() {
  log "Waiting for Docker daemon to become ready (timeout: ${DOCKER_WAIT_SECS}s)"
  local start end
  start="$(date +%s)"
  end=$((start + DOCKER_WAIT_SECS))

  until docker info >/dev/null 2>&1; do
    if [[ "$(date +%s)" -ge "$end" ]]; then
      die "Docker daemon did not become ready within ${DOCKER_WAIT_SECS}s"
    fi
    sleep 2
  done
  log "Docker daemon is ready"
}

is_port_listening_local() {
  local port="$1"
  ss -lnt 2>/dev/null | grep -Eq ":[[:space:]]*${port}\b|:${port}\b"
}

wait_for_ports() {
  log "Waiting for local ports to listen: ${UPSTREAM_PORTS[*]} (timeout: ${PORT_WAIT_SECS}s)"
  local start end
  start="$(date +%s)"
  end=$((start + PORT_WAIT_SECS))

  while :; do
    local all_ok=1
    for p in "${UPSTREAM_PORTS[@]}"; do
      if ! is_port_listening_local "$p"; then
        all_ok=0
      fi
    done

    if [[ "$all_ok" -eq 1 ]]; then
      log "All expected ports are listening: ${UPSTREAM_PORTS[*]}"
      return 0
    fi

    if [[ "$(date +%s)" -ge "$end" ]]; then
      warn "Timed out waiting for ports ${UPSTREAM_PORTS[*]} to listen. Containers may still be starting."
      return 1
    fi
    sleep 2
  done
}

wait_for_http() {
  # Not all apps return 200 on /, so accept any HTTP response code.
  local url="$1"
  log "Probing nginx over HTTP: ${url} (timeout: ${HTTP_WAIT_SECS}s)"
  local start end
  start="$(date +%s)"
  end=$((start + HTTP_WAIT_SECS))

  while :; do
    # -I = headers only, --max-time to avoid hanging
    if curl -sS -I --max-time 5 "$url" >/dev/null 2>&1; then
      log "nginx is responding on ${url}"
      return 0
    fi
    if [[ "$(date +%s)" -ge "$end" ]]; then
      warn "Timed out probing ${url}. nginx may be up but not reachable from this host/network."
      return 1
    fi
    sleep 2
  done
}

# -------------------- rollback on failure --------------------
CREATED_SITE_FILE=0
CREATED_SITE_LINK=0

rollback() {
  local exit_code=$?
  if [[ $exit_code -eq 0 ]]; then
    return 0
  fi

  warn "A failure occurred (exit code: ${exit_code}). Attempting rollback of nginx site changes..."

  if [[ "${CREATED_SITE_LINK}" -eq 1 && -L "${SITE_ENABLED}" ]]; then
    rm -f "${SITE_ENABLED}" || true
    warn "Removed symlink: ${SITE_ENABLED}"
  fi

  if [[ "${CREATED_SITE_FILE}" -eq 1 && -f "${SITE_AVAILABLE}" ]]; then
    rm -f "${SITE_AVAILABLE}" || true
    warn "Removed site file: ${SITE_AVAILABLE}"
  fi

  # Try to restore nginx to a valid state (best effort)
  if command -v nginx >/dev/null 2>&1; then
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  fi

  warn "Rollback attempt complete."
}
trap rollback EXIT

# -------------------- main --------------------
need_root
require_cmd apt-get
require_cmd dpkg
require_cmd tee
require_cmd install
require_cmd curl
require_cmd chmod
require_cmd ln
require_cmd systemctl
require_cmd ss

[[ $# -eq 1 ]] || usage
SERVER_NAME="$1"

if is_ipv4 "$SERVER_NAME"; then
  log "Server name looks like IPv4: $SERVER_NAME"
elif is_hostname "$SERVER_NAME"; then
  log "Server name looks like hostname: $SERVER_NAME"
else
  die "Argument must be an IPv4 address or a hostname. Got: '$SERVER_NAME'"
fi

# 1) Base deps + Docker keyring
apt_update
apt_install ca-certificates curl
verify_pkg ca-certificates
verify_pkg curl

log "Ensuring keyrings directory exists: ${DOCKER_KEYRING_DIR}"
install -m 0755 -d "${DOCKER_KEYRING_DIR}"
[[ -d "${DOCKER_KEYRING_DIR}" ]] || die "Keyrings directory was not created."

log "Downloading Docker GPG key to: ${DOCKER_KEYRING}"
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "${DOCKER_KEYRING}"
[[ -s "${DOCKER_KEYRING}" ]] || die "Docker key file missing/empty: ${DOCKER_KEYRING}"

log "Setting permissions on Docker key"
chmod a+r "${DOCKER_KEYRING}"
[[ -r "${DOCKER_KEYRING}" ]] || die "Docker key is not readable: ${DOCKER_KEYRING}"

# 2) Add Docker repository to Apt sources (Ubuntu)
log "Writing Docker apt source file: ${DOCKER_SOURCES}"
UBUNTU_CODENAME="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
[[ -n "${UBUNTU_CODENAME}" ]] || die "Could not determine UBUNTU_CODENAME from /etc/os-release."

cat > "${DOCKER_SOURCES}" <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME}
Components: stable
Signed-By: ${DOCKER_KEYRING}
EOF

[[ -s "${DOCKER_SOURCES}" ]] || die "Docker sources file missing/empty: ${DOCKER_SOURCES}"
grep -q "download.docker.com" "${DOCKER_SOURCES}" || die "Docker sources file does not look correct."

apt_update

# 3) Install Docker engine packages
log "Installing Docker engine packages"
apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
verify_pkg docker-ce
verify_pkg docker-ce-cli
verify_pkg containerd.io

log "Enabling and starting Docker service"
systemctl enable --now docker >/dev/null
systemctl is-active --quiet docker || die "Docker service is not active."

require_cmd docker
docker version >/dev/null 2>&1 || die "docker version failed (daemon not reachable?)"

wait_for_docker

# 4) Install nginx
log "Installing nginx"
apt_update
apt_install nginx
verify_pkg nginx

log "Enabling and starting nginx"
systemctl enable --now nginx >/dev/null
systemctl is-active --quiet nginx || die "nginx service is not active."

# 5) Write nginx site config with server_name argument
log "Writing nginx site config: ${SITE_AVAILABLE}"
cat > "${SITE_AVAILABLE}" <<EOF
server {
  listen 80;
  server_name ${SERVER_NAME};

  # Proxy to the web container
  location / {
    proxy_pass http://127.0.0.1:5880/;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
  }

  location /hls/ {
    proxy_pass http://127.0.0.1:5880/hls/;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_buffering off;
  }

  location /dashboard/ {
    proxy_pass http://127.0.0.1:5880/dashboard/;
    proxy_set_header Host \$host;
  }

  # Stats API
  location /api/ {
    proxy_pass http://127.0.0.1:8090/api/;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
  }
}
EOF
CREATED_SITE_FILE=1

[[ -s "${SITE_AVAILABLE}" ]] || die "Nginx site file missing/empty: ${SITE_AVAILABLE}"
grep -q "server_name ${SERVER_NAME};" "${SITE_AVAILABLE}" || die "server_name not written correctly."

# 6) Enable site
log "Enabling nginx site symlink: ${SITE_ENABLED}"
if [[ -L "${SITE_ENABLED}" || -e "${SITE_ENABLED}" ]]; then
  log "Site enabled entry already exists: ${SITE_ENABLED} (leaving as-is)"
else
  ln -s "${SITE_AVAILABLE}" "${SITE_ENABLED}"
  CREATED_SITE_LINK=1
fi

[[ -L "${SITE_ENABLED}" ]] || die "Expected symlink not present: ${SITE_ENABLED}"
readlink -f "${SITE_ENABLED}" | grep -q "${SITE_AVAILABLE}" || die "Symlink does not point to expected site file."

# 7) Test and reload nginx (no upstream required yet)
log "Testing nginx configuration (nginx -t)"
nginx -t

log "Reloading nginx"
systemctl reload nginx
systemctl is-active --quiet nginx || die "nginx not active after reload."
log "nginx reload OK (upstreams do not need to be running yet)"

# 8) Clone ORL repo and start containers
log "Ensuring git is installed"
apt_install git
verify_pkg git
require_cmd git

if [[ -d "${ORL_DIR}/.git" ]]; then
  log "ORL repo already exists at ${ORL_DIR}, pulling latest changes"
  cd "${ORL_DIR}"
  git pull
else
  log "Cloning ORL repo to ${ORL_DIR}"
  git clone "${ORL_REPO_URL}" "${ORL_DIR}"
  [[ -d "${ORL_DIR}/.git" ]] || die "Git clone failed"
  cd "${ORL_DIR}"
fi

log "Verifying docker compose plugin"
docker compose version >/dev/null 2>&1 || die "docker compose plugin not available"

log "Building and starting ORL containers"
docker compose up -d --build

log "Showing compose status"
docker compose ps || true

# 9) Health-ish verification: wait for ports and nginx response
wait_for_ports || true

# Probe nginx locally. If this machine isn't the same as the public IP/hostname,
# this may fail; it's just a best-effort.
wait_for_http "http://${SERVER_NAME}/" || true

log "DONE.

nginx site:
  ${SITE_AVAILABLE}
enabled link:
  ${SITE_ENABLED}

Try:
  http://${SERVER_NAME}/
  http://${SERVER_NAME}/dashboard/
  http://${SERVER_NAME}/api/

If you see 502s initially, wait a moment for containers to finish starting."

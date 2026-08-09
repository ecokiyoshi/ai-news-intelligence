#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEPLOY_USER="${DEPLOY_USER:-deploy}"
readonly DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/ai-news-intelligence}"
readonly SSH_PORT="${SSH_PORT:-22}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${EUID}" -ne 0 ]]; then
  fail "run this script as root (for example: sudo ./deploy/lightsail-bootstrap.sh)"
fi

[[ "${DEPLOY_USER}" =~ ^[a-z_][a-z0-9_-]*$ ]] || fail "DEPLOY_USER is invalid"
[[ "${DEPLOY_ROOT}" == /* && "${DEPLOY_ROOT}" != "/" ]] || fail "DEPLOY_ROOT must be an absolute non-root path"
[[ "${SSH_PORT}" =~ ^[0-9]{1,5}$ ]] || fail "SSH_PORT must be an integer from 1 to 65535"
((10#${SSH_PORT} >= 1 && 10#${SSH_PORT} <= 65535)) || fail "SSH_PORT must be an integer from 1 to 65535"

if [[ ! -r /etc/os-release ]]; then
  fail "cannot identify the operating system"
fi

# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "this bootstrap supports Ubuntu only"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl ufw

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "${DEPLOY_USER}"
fi
usermod -aG docker "${DEPLOY_USER}"

install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 0750 "${DEPLOY_ROOT}"
install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 0750 "${DEPLOY_ROOT}/deploy"
install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 0750 "${DEPLOY_ROOT}/scripts"
install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 0700 "/home/${DEPLOY_USER}/.ssh"
touch "/home/${DEPLOY_USER}/.ssh/authorized_keys"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh/authorized_keys"
chmod 0600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"

ufw allow "${SSH_PORT}/tcp"
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

docker version
docker compose version
ufw status verbose

printf '\nLightsail host bootstrap completed.\n'
printf 'Install the dedicated deployment public key in /home/%s/.ssh/authorized_keys.\n' "${DEPLOY_USER}"
printf 'Then start a new SSH session as %s so docker-group membership takes effect.\n' "${DEPLOY_USER}"

#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly ENV_FILE="${DEPLOY_DIR}/.env"
readonly COMPOSE_FILE="${DEPLOY_DIR}/compose.production.yaml"
readonly CADDY_FILE="${DEPLOY_DIR}/deploy/Caddyfile"
readonly OPERATIONS_LOCK_FILE="${DEPLOY_DIR}/.operations.lock"
readonly EXPECTED_IMAGE_PREFIX="ghcr.io/ecokiyoshi/ai-news-intelligence:sha-"
readonly HEALTH_TIMEOUT_SECONDS="${DEPLOY_HEALTH_TIMEOUT_SECONDS:-180}"

umask 077
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

diagnostics() {
  local exit_code=$?
  trap - ERR
  printf 'Deployment failed (exit %s). Current service status:\n' "${exit_code}" >&2
  "${COMPOSE[@]}" ps >&2 || true
  printf 'Recent service logs:\n' >&2
  "${COMPOSE[@]}" logs --tail=100 scheduler dashboard caddy >&2 || true
  exit "${exit_code}"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  return 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

acquire_operations_lock() {
  local inherited_fd=${OPERATIONS_LOCK_FD:-}

  if [[ -n "${inherited_fd}" ]]; then
    [[ "${inherited_fd}" =~ ^[0-9]+$ ]] || fail "OPERATIONS_LOCK_FD must be numeric"
    flock --nonblock "${inherited_fd}" ||
      fail "inherited operations lock is unavailable"
    return 0
  fi

  exec 9>"${OPERATIONS_LOCK_FILE}"
  flock --nonblock 9 || fail "another deploy, backup, or restore operation is already running"
}

write_image_to_env() {
  local image=$1
  local temporary_file

  temporary_file="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk -v image="${image}" '
    BEGIN { replaced = 0 }
    /^APP_IMAGE=/ {
      if (!replaced) {
        print "APP_IMAGE=" image
        replaced = 1
      }
      next
    }
    { print }
    END {
      if (!replaced) {
        print "APP_IMAGE=" image
      }
    }
  ' "${ENV_FILE}" >"${temporary_file}"
  chmod 600 "${temporary_file}"
  mv -f -- "${temporary_file}" "${ENV_FILE}"
}

env_value() {
  local key=$1
  awk -v key="${key}" '
    index($0, key "=") == 1 {
      sub(/^[^=]*=/, "")
      print
      exit
    }
  ' "${ENV_FILE}"
}

service_container_id() {
  local service=$1
  local ids

  ids="$("${COMPOSE[@]}" ps -q "${service}")"
  [[ "$(printf '%s\n' "${ids}" | sed '/^$/d' | wc -l | tr -d ' ')" == "1" ]] ||
    fail "expected exactly one ${service} container"
  printf '%s\n' "${ids}"
}

service_is_healthy() {
  local service=$1
  local container_id
  local state
  local health

  container_id="$(service_container_id "${service}")" || return 1
  state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_id}")"
  [[ "${state}" == "running" && "${health}" == "healthy" ]]
}

wait_for_services() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
  local service
  local all_healthy

  while ((SECONDS < deadline)); do
    all_healthy=true
    for service in scheduler dashboard caddy; do
      if ! service_is_healthy "${service}"; then
        all_healthy=false
        break
      fi
    done
    if [[ "${all_healthy}" == "true" ]]; then
      return 0
    fi
    sleep 5
  done
  fail "services did not become healthy within ${HEALTH_TIMEOUT_SECONDS} seconds"
}

verify_application_images() {
  local expected_image=$1
  local service
  local container_id
  local actual_image

  for service in scheduler dashboard; do
    container_id="$(service_container_id "${service}")"
    actual_image="$(docker inspect --format '{{.Config.Image}}' "${container_id}")"
    [[ "${actual_image}" == "${expected_image}" ]] ||
      fail "${service} is running ${actual_image}, expected ${expected_image}"
  done
}

wait_for_public_health() {
  local domain=$1
  local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))

  while ((SECONDS < deadline)); do
    if curl --fail --silent --max-time 10 "https://${domain}/health" >/dev/null; then
      return 0
    fi
    sleep 5
  done
  fail "public health endpoint did not become ready within ${HEALTH_TIMEOUT_SECONDS} seconds"
}

main() {
  local image=${1:-}
  local domain

  [[ "${HEALTH_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] ||
    fail "DEPLOY_HEALTH_TIMEOUT_SECONDS must be a positive integer"
  [[ "${image}" =~ ^${EXPECTED_IMAGE_PREFIX}[0-9a-f]{40}$ ]] ||
    fail "usage: $0 ghcr.io/ecokiyoshi/ai-news-intelligence:sha-<40-character-commit-sha>"

  require_command awk
  require_command curl
  require_command docker
  require_command flock
  require_command mktemp

  [[ -f "${ENV_FILE}" ]] || fail "missing runtime configuration: ${ENV_FILE}"
  [[ -f "${COMPOSE_FILE}" ]] || fail "missing production Compose file: ${COMPOSE_FILE}"
  [[ -f "${CADDY_FILE}" ]] || fail "missing Caddy configuration: ${CADDY_FILE}"

  domain="$(env_value APP_DOMAIN)"
  [[ "${domain}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] ||
    fail "APP_DOMAIN must be a DNS name without a scheme, port, path, or quotes"

  cd -- "${DEPLOY_DIR}"
  acquire_operations_lock
  write_image_to_env "${image}"
  "${COMPOSE[@]}" config --quiet
  trap diagnostics ERR
  "${COMPOSE[@]}" pull
  "${COMPOSE[@]}" up -d --no-build --remove-orphans
  wait_for_services
  verify_application_images "${image}"
  wait_for_public_health "${domain}"

  printf 'Deployment succeeded: %s\n' "${image}"
  "${COMPOSE[@]}" ps
}

main "$@"

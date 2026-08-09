#!/usr/bin/env bash

readonly OPERATIONS_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="$(cd -- "${OPERATIONS_SCRIPT_DIR}/.." && pwd)"
readonly ENV_FILE="${DEPLOY_DIR}/.env"
readonly COMPOSE_FILE="${DEPLOY_DIR}/compose.production.yaml"
readonly OPERATIONS_HELPER_IMAGE="${OPERATIONS_HELPER_IMAGE:-caddy:2-alpine}"
readonly OPERATIONS_LOCK_FILE="${DEPLOY_DIR}/.operations.lock"

umask 077
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

ops_fail() {
  printf 'ERROR: %s\n' "$*" >&2
  return 1
}

ops_require_command() {
  command -v "$1" >/dev/null 2>&1 || ops_fail "required command not found: $1"
}

ops_validate_runtime() {
  ops_require_command docker
  [[ -f "${ENV_FILE}" ]] || ops_fail "missing runtime configuration: ${ENV_FILE}"
  [[ -f "${COMPOSE_FILE}" ]] || ops_fail "missing production Compose file: ${COMPOSE_FILE}"
  "${COMPOSE[@]}" config --quiet
}

ops_acquire_exclusive_lock() {
  ops_require_command flock
  exec 9>"${OPERATIONS_LOCK_FILE}"
  flock --nonblock 9 || ops_fail "another deploy, backup, or restore operation is already running"
}

ops_env_value() {
  local key=$1
  awk -v key="${key}" '
    index($0, key "=") == 1 {
      sub(/^[^=]*=/, "")
      print
      exit
    }
  ' "${ENV_FILE}"
}

ops_service_container_id() {
  local service=$1
  local include_stopped=${2:-false}
  local ids

  if [[ "${include_stopped}" == "true" ]]; then
    ids="$("${COMPOSE[@]}" ps --all --quiet "${service}")"
  else
    ids="$("${COMPOSE[@]}" ps --quiet "${service}")"
  fi
  [[ "$(printf '%s\n' "${ids}" | sed '/^$/d' | wc -l | tr -d ' ')" == "1" ]] ||
    ops_fail "expected exactly one ${service} container"
  printf '%s\n' "${ids}"
}

ops_volume_for_destination() {
  local service=$1
  local destination=$2
  local container_id
  local volume

  container_id="$(ops_service_container_id "${service}" true)"
  volume="$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"${destination}\"}}{{.Name}}{{end}}{{end}}" "${container_id}")"
  [[ -n "${volume}" ]] || ops_fail "could not resolve volume mounted at ${destination}"
  [[ "${volume}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || ops_fail "resolved volume name is invalid"
  printf '%s\n' "${volume}"
}

ops_service_is_healthy() {
  local service=$1
  local container_id
  local state
  local health

  container_id="$(ops_service_container_id "${service}")" || return 1
  state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_id}")"
  [[ "${state}" == "running" && "${health}" == "healthy" ]]
}

ops_wait_for_service() {
  local service=$1
  local timeout_seconds=${2:-180}
  local deadline

  [[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] ||
    ops_fail "health timeout must be a positive integer"
  deadline=$((SECONDS + timeout_seconds))

  while ((SECONDS < deadline)); do
    if ops_service_is_healthy "${service}"; then
      return 0
    fi
    sleep 5
  done
  ops_fail "${service} did not become healthy within ${timeout_seconds} seconds"
}

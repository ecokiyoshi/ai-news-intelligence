#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=operations-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/operations-common.sh"

readonly BACKUP_ROOT="${BACKUP_DIR:-${DEPLOY_DIR}/backups}"

volume_usage() {
  local label=$1
  local volume=$2

  printf '%s (%s): ' "${label}" "${volume}"
  docker run --rm --pull never --network none --read-only \
    --volume "${volume}:/source:ro" \
    --entrypoint du "${OPERATIONS_HELPER_IMAGE}" -sh /source
}

main() {
  local app_data_volume=""
  local outputs_volume=""
  local health_status=0

  ops_validate_runtime

  printf '== Compose services ==\n'
  "${COMPOSE[@]}" ps

  printf '\n== Health ==\n'
  "${OPERATIONS_SCRIPT_DIR}/healthcheck.sh" || health_status=$?

  printf '\n== Recent scheduler logs ==\n'
  "${COMPOSE[@]}" logs --tail="${STATUS_LOG_LINES:-100}" scheduler || true

  printf '\n== Recent dashboard logs ==\n'
  "${COMPOSE[@]}" logs --tail="${STATUS_LOG_LINES:-100}" dashboard || true

  printf '\n== Filesystem usage ==\n'
  df -h -- "${DEPLOY_DIR}"
  if [[ -d "${BACKUP_ROOT}" ]]; then
    df -h -- "${BACKUP_ROOT}"
  fi

  printf '\n== Persistent volume usage ==\n'
  if app_data_volume="$(ops_volume_for_destination scheduler /data/state)"; then
    volume_usage app_data "${app_data_volume}" || true
  else
    printf 'app_data: unavailable\n' >&2
  fi
  if outputs_volume="$(ops_volume_for_destination scheduler /data/outputs)"; then
    volume_usage generated_outputs "${outputs_volume}" || true
  else
    printf 'generated_outputs: unavailable\n' >&2
  fi

  printf '\n== Docker disk usage ==\n'
  docker system df

  return "${health_status}"
}

main "$@"

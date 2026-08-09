#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=operations-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/operations-common.sh"

readonly CONFIGURED_BACKUP_DIR="${BACKUP_DIR:-${DEPLOY_DIR}/backups}"
readonly RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-}"
readonly TIMESTAMP="$(date -u +%Y-%m-%d_%H%M%SZ)"

BACKUP_ROOT=""
STAGING_DIR=""
SCHEDULER_WAS_RUNNING=false
SCHEDULER_STOPPED=false

restart_scheduler() {
  if [[ "${SCHEDULER_STOPPED}" == "true" && "${SCHEDULER_WAS_RUNNING}" == "true" ]]; then
    printf 'Restarting scheduler...\n'
    "${COMPOSE[@]}" up -d --no-build scheduler
    SCHEDULER_STOPPED=false
    ops_wait_for_service scheduler "${BACKUP_HEALTH_TIMEOUT_SECONDS:-180}"
  fi
}

on_exit() {
  local exit_code=$?
  trap - EXIT
  if [[ "${SCHEDULER_STOPPED}" == "true" ]]; then
    restart_scheduler || true
  fi
  if ((exit_code != 0)) && [[ -n "${STAGING_DIR}" && -d "${STAGING_DIR}" ]]; then
    printf 'Incomplete backup retained for diagnostics: %s\n' "${STAGING_DIR}" >&2
  fi
  exit "${exit_code}"
}

archive_volume() {
  local volume=$1
  local archive_name=$2
  local owner_uid
  local owner_gid

  owner_uid="$(id -u)"
  owner_gid="$(id -g)"
  docker run --rm --pull never --network none --read-only \
    --env "BACKUP_UID=${owner_uid}" \
    --env "BACKUP_GID=${owner_gid}" \
    --env "ARCHIVE_NAME=${archive_name}" \
    --volume "${volume}:/source:ro" \
    --volume "${STAGING_DIR}:/backup" \
    --entrypoint sh "${OPERATIONS_HELPER_IMAGE}" -ceu \
    'tar -C /source -czf "/backup/${ARCHIVE_NAME}" . && chown "${BACKUP_UID}:${BACKUP_GID}" "/backup/${ARCHIVE_NAME}"'
}

apply_retention() {
  local candidate
  local resolved_candidate

  [[ -n "${RETENTION_DAYS}" ]] || return 0
  [[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] || ops_fail "BACKUP_RETENTION_DAYS must be a non-negative integer"

  while IFS= read -r -d '' candidate; do
    resolved_candidate="$(realpath -- "${candidate}")"
    [[ "$(dirname -- "${resolved_candidate}")" == "${BACKUP_ROOT}" ]] ||
      ops_fail "refusing to remove backup outside ${BACKUP_ROOT}"
    [[ "$(basename -- "${resolved_candidate}")" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}_[0-9]{6}Z$ ]] || continue
    printf 'Removing expired backup: %s\n' "${resolved_candidate}"
    rm -rf -- "${resolved_candidate}"
  done < <(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d \
    -name '20??-??-??_??????Z' -mtime "+${RETENTION_DAYS}" -print0)
}

main() {
  local scheduler_id
  local scheduler_state
  local app_data_volume
  local outputs_volume
  local final_dir

  ops_require_command date
  ops_require_command find
  ops_require_command realpath
  ops_require_command sha256sum
  ops_require_command tar
  ops_validate_runtime
  ops_acquire_exclusive_lock

  [[ "${CONFIGURED_BACKUP_DIR}" == /* && "${CONFIGURED_BACKUP_DIR}" != "/" ]] ||
    ops_fail "BACKUP_DIR must be an absolute non-root path"
  install -d -m 0700 -- "${CONFIGURED_BACKUP_DIR}"
  BACKUP_ROOT="$(realpath -- "${CONFIGURED_BACKUP_DIR}")"
  [[ "${BACKUP_ROOT}" != "/" ]] || ops_fail "refusing to use / as BACKUP_DIR"

  scheduler_id="$(ops_service_container_id scheduler true)"
  scheduler_state="$(docker inspect --format '{{.State.Status}}' "${scheduler_id}")"
  app_data_volume="$(ops_volume_for_destination scheduler /data/state)"
  outputs_volume="$(ops_volume_for_destination scheduler /data/outputs)"
  final_dir="${BACKUP_ROOT}/${TIMESTAMP}"
  [[ ! -e "${final_dir}" ]] || ops_fail "backup already exists: ${final_dir}"
  STAGING_DIR="$(mktemp -d "${BACKUP_ROOT}/.incomplete-${TIMESTAMP}.XXXXXX")"
  chmod 0700 "${STAGING_DIR}"

  trap on_exit EXIT
  if [[ "${scheduler_state}" == "running" ]]; then
    SCHEDULER_WAS_RUNNING=true
    printf 'Stopping scheduler for a consistent backup...\n'
    SCHEDULER_STOPPED=true
    "${COMPOSE[@]}" stop --timeout 60 scheduler
  fi

  archive_volume "${app_data_volume}" app_data.tar.gz
  archive_volume "${outputs_volume}" generated_outputs.tar.gz
  printf 'format_version=1\ncreated_at_utc=%s\n' "${TIMESTAMP}" >"${STAGING_DIR}/manifest.txt"
  (
    cd -- "${STAGING_DIR}"
    sha256sum app_data.tar.gz generated_outputs.tar.gz >SHA256SUMS
    tar -tzf app_data.tar.gz >/dev/null
    tar -tzf generated_outputs.tar.gz >/dev/null
  )
  chmod 0600 "${STAGING_DIR}"/*
  mv -- "${STAGING_DIR}" "${final_dir}"
  STAGING_DIR=""

  restart_scheduler
  apply_retention
  trap - EXIT
  printf 'Backup completed: %s\n' "${final_dir}"
}

main "$@"

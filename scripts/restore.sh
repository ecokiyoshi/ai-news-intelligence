#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=operations-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/operations-common.sh"

readonly CONFIGURED_BACKUP_DIR="${BACKUP_DIR:-${DEPLOY_DIR}/backups}"

BACKUP_ROOT=""
BACKUP_PATH=""
RESTORE_STARTED=false

diagnostics() {
  local exit_code=$?
  trap - ERR EXIT
  printf 'Restore failed (exit %s). Current service status:\n' "${exit_code}" >&2
  "${COMPOSE[@]}" ps >&2 || true
  "${COMPOSE[@]}" logs --tail=100 scheduler dashboard caddy >&2 || true
  if [[ "${RESTORE_STARTED}" == "true" ]]; then
    printf 'Attempting to restart production services...\n' >&2
    "${COMPOSE[@]}" up -d --no-build scheduler dashboard caddy >&2 || true
  fi
  exit "${exit_code}"
}

expected_checksum() {
  local filename=$1
  awk -v filename="${filename}" '$2 == filename { print $1 }' "${BACKUP_PATH}/SHA256SUMS"
}

validate_archive() {
  local filename=$1
  local archive="${BACKUP_PATH}/${filename}"
  local expected
  local actual
  local entry
  local normalized

  [[ -f "${archive}" ]] || ops_fail "missing backup archive: ${filename}"
  expected="$(expected_checksum "${filename}")"
  [[ "${expected}" =~ ^[0-9a-f]{64}$ ]] || ops_fail "invalid checksum entry for ${filename}"
  [[ "$(awk -v filename="${filename}" '$2 == filename { count++ } END { print count + 0 }' "${BACKUP_PATH}/SHA256SUMS")" == "1" ]] ||
    ops_fail "expected exactly one checksum entry for ${filename}"
  actual="$(sha256sum "${archive}" | awk '{ print $1 }')"
  [[ "${actual}" == "${expected}" ]] || ops_fail "checksum mismatch for ${filename}"

  while IFS= read -r entry; do
    normalized="${entry#./}"
    case "${normalized}" in
      "" | .) ;;
      /* | .. | ../* | */../* | */..) ops_fail "unsafe archive path in ${filename}: ${entry}" ;;
    esac
  done < <(tar -tzf "${archive}")

  tar -tvzf "${archive}" | awk '
    substr($1, 1, 1) != "-" && substr($1, 1, 1) != "d" { exit 1 }
  ' || ops_fail "${filename} contains unsupported links or special files"
}

restore_volume() {
  local volume=$1
  local archive_name=$2

  docker run --rm --pull never --network none --read-only \
    --env "ARCHIVE_NAME=${archive_name}" \
    --volume "${volume}:/target" \
    --volume "${BACKUP_PATH}:/backup:ro" \
    --entrypoint sh "${OPERATIONS_HELPER_IMAGE}" -ceu \
    'find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} \; && tar -xzf "/backup/${ARCHIVE_NAME}" -C /target'
}

confirm_restore() {
  local force=$1
  local answer

  printf 'WARNING: this will replace app_data and generated_outputs with backup %s.\n' "${BACKUP_PATH}" >&2
  printf 'Named Docker volumes will be retained, but their current contents will be overwritten.\n' >&2
  if [[ "${force}" == "true" ]]; then
    return 0
  fi
  [[ -t 0 ]] || ops_fail "non-interactive restore requires --force"
  read -r -p 'Type RESTORE to continue: ' answer
  [[ "${answer}" == "RESTORE" ]] || ops_fail "restore cancelled"
}

wait_for_stack() {
  local timeout_seconds=${RESTORE_HEALTH_TIMEOUT_SECONDS:-180}
  local deadline

  [[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] ||
    ops_fail "RESTORE_HEALTH_TIMEOUT_SECONDS must be a positive integer"
  deadline=$((SECONDS + timeout_seconds))

  while ((SECONDS < deadline)); do
    if "${OPERATIONS_SCRIPT_DIR}/healthcheck.sh" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  "${OPERATIONS_SCRIPT_DIR}/healthcheck.sh" || ops_fail "restored stack did not become healthy"
}

main() {
  local force=false
  local requested=""
  local app_data_volume
  local outputs_volume

  while (($#)); do
    case "$1" in
      --force) force=true ;;
      -*) ops_fail "unknown option: $1" ;;
      *)
        [[ -z "${requested}" ]] || ops_fail "usage: $0 [--force] <backup-directory>"
        requested=$1
        ;;
    esac
    shift
  done
  [[ -n "${requested}" ]] || ops_fail "usage: $0 [--force] <backup-directory>"

  ops_require_command awk
  ops_require_command realpath
  ops_require_command sha256sum
  ops_require_command tar
  ops_validate_runtime
  ops_acquire_exclusive_lock
  [[ "${CONFIGURED_BACKUP_DIR}" == /* && "${CONFIGURED_BACKUP_DIR}" != "/" ]] ||
    ops_fail "BACKUP_DIR must be an absolute non-root path"
  [[ -d "${CONFIGURED_BACKUP_DIR}" ]] || ops_fail "backup root does not exist: ${CONFIGURED_BACKUP_DIR}"
  BACKUP_ROOT="$(realpath -- "${CONFIGURED_BACKUP_DIR}")"
  [[ "${BACKUP_ROOT}" != "/" ]] || ops_fail "refusing to use / as BACKUP_DIR"
  BACKUP_PATH="$(realpath -- "${requested}")"
  [[ "$(dirname -- "${BACKUP_PATH}")" == "${BACKUP_ROOT}" ]] ||
    ops_fail "backup must be a direct child of ${BACKUP_ROOT}"
  [[ "$(basename -- "${BACKUP_PATH}")" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}_[0-9]{6}Z$ ]] ||
    ops_fail "backup directory name is invalid"
  [[ -f "${BACKUP_PATH}/manifest.txt" && -f "${BACKUP_PATH}/SHA256SUMS" ]] ||
    ops_fail "backup metadata is incomplete"
  grep -qx 'format_version=1' "${BACKUP_PATH}/manifest.txt" || ops_fail "unsupported backup format"
  [[ "$(wc -l <"${BACKUP_PATH}/SHA256SUMS" | tr -d ' ')" == "2" ]] ||
    ops_fail "SHA256SUMS must contain exactly two entries"

  validate_archive app_data.tar.gz
  validate_archive generated_outputs.tar.gz
  confirm_restore "${force}"

  app_data_volume="$(ops_volume_for_destination scheduler /data/state)"
  outputs_volume="$(ops_volume_for_destination scheduler /data/outputs)"
  trap diagnostics ERR EXIT
  RESTORE_STARTED=true
  "${COMPOSE[@]}" stop --timeout 60 scheduler dashboard
  restore_volume "${app_data_volume}" app_data.tar.gz
  restore_volume "${outputs_volume}" generated_outputs.tar.gz
  "${COMPOSE[@]}" up -d --no-build scheduler dashboard caddy
  wait_for_stack
  RESTORE_STARTED=false
  trap - ERR EXIT
  printf 'Restore completed and production is healthy: %s\n' "${BACKUP_PATH}"
}

main "$@"

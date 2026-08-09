#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=operations-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/operations-common.sh"

main() {
  local service
  local domain
  local failed=false

  ops_require_command awk
  ops_require_command curl
  ops_validate_runtime

  for service in scheduler dashboard caddy; do
    if ops_service_is_healthy "${service}"; then
      printf '%s: healthy\n' "${service}"
    else
      printf '%s: unhealthy\n' "${service}" >&2
      failed=true
    fi
  done

  domain="$(ops_env_value APP_DOMAIN)"
  if [[ ! "${domain}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]]; then
    printf 'public endpoint: invalid APP_DOMAIN\n' >&2
    failed=true
  elif curl --fail --silent --max-time "${HEALTHCHECK_HTTP_TIMEOUT_SECONDS:-10}" \
    "https://${domain}/health" >/dev/null; then
    printf 'public endpoint: healthy\n'
  else
    printf 'public endpoint: unhealthy\n' >&2
    failed=true
  fi

  [[ "${failed}" == "false" ]]
}

main "$@"

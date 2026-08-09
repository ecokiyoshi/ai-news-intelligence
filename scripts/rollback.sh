#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly IMAGE_REPOSITORY="ghcr.io/ecokiyoshi/ai-news-intelligence"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

main() {
  local requested=${1:-}
  local image

  case "${requested}" in
    sha-[0-9a-f][0-9a-f]*)
      image="${IMAGE_REPOSITORY}:${requested}"
      ;;
    "${IMAGE_REPOSITORY}":sha-[0-9a-f][0-9a-f]*)
      image="${requested}"
      ;;
    *)
      fail "usage: $0 sha-<40-character-commit-sha>"
      ;;
  esac

  [[ "${image}" =~ ^${IMAGE_REPOSITORY}:sha-[0-9a-f]{40}$ ]] ||
    fail "rollback requires an immutable 40-character SHA tag"

  exec "${SCRIPT_DIR}/deploy.sh" "${image}"
}

main "$@"

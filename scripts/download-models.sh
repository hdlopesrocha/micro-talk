#!/usr/bin/env bash
# Download every model required by MicroTalk into persistent Docker volumes.
#
# Usage:
#   ./scripts/download-models.sh                 # all three model services
#   ./scripts/download-models.sh translation     # translation only
#   ./scripts/download-models.sh summary tts     # selected services
#   ./scripts/download-models.sh --list          # show available targets
#
# Model weights are never fetched during normal requests; this command is the
# only network access required after building the images.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TARGETS=("$@")
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=(translation summary tts)
fi

for target in "${TARGETS[@]}"; do
  case "$target" in
    translation|translation-service)
      service="translation-service"
      ;;
    summary|summary-service)
      service="summary-service"
      ;;
    tts|tts-service)
      service="tts-service"
      ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    --list)
      printf '%s\n' translation summary tts
      exit 0
      ;;
    *)
      echo "Unknown target: $target (expected: translation, summary, tts)" >&2
      exit 2
      ;;
  esac
  echo
  echo "==> Downloading $service models"
  docker compose run --rm "$service" download-models
done

echo
echo "All requested models are installed in their persistent volumes."
echo "Start or restart the stack with: docker compose up -d"

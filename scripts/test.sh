#!/usr/bin/env bash
# Run the backend and frontend test suites locally.
#
# The backend suite installs only lightweight dependencies (no Torch/Piper) and
# tests the shared libraries, PDF extraction, gateway store/artifacts, media
# subtitle timing and language/segmentation helpers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${MICROTALK_TEST_VENV:-$ROOT/.venv-test}"
PYTHON="${PYTHON:-python3}"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "==> Creating test virtualenv at $VENV"
  "$PYTHON" -m venv "$VENV"
fi

echo "==> Installing backend test dependencies"
"$VENV/bin/pip" install -q -r tests/requirements.txt

echo "==> Backend tests"
"$VENV/bin/python" -m pytest

echo "==> Frontend unit tests"
(
  cd frontend
  if [[ ! -d node_modules ]]; then
    npm ci --no-audit --no-fund
  fi
  npm test
  npm run build
)

#!/usr/bin/env bash
# Run every test suite (portal, assistant, infrastructure, evaluations). No AWS calls are made.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-$PWD/.venv/bin/python}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}" AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
status=0
for suite in portal agent infra evals; do
  echo "== $suite"
  (cd "$suite" && "$PY" -m pytest -q -p no:cacheprovider -W ignore::DeprecationWarning tests) | tail -1 || status=1
done
exit $status

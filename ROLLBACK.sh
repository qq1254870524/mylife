#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")" && pwd)}"
ORIGINAL_COMMIT="ce54de719aedcd2f9193226c8db7a5e94700ca28"
cd "$ROOT"

tracked=(
  controller.py
  tests/test_controller_offline.py
)
for file in "${tracked[@]}"; do
  git show "${ORIGINAL_COMMIT}:${file}" > "${file}.rollback.tmp"
  mv -f "${file}.rollback.tmp" "$file"
done

PYTHON_BIN="${PYTHON_BIN:-C:/Python312/python.exe}"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -B -m unittest tests.test_controller_offline -v
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -B -m unittest discover -s tests
git diff --exit-code -- controller.py tests/test_controller_offline.py

echo "ROLLBACK_RESULT=restored_v1.1.4_controller_proxy_wait_baseline"
echo "ROLLBACK_COMMIT=${ORIGINAL_COMMIT}"

#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")" && pwd)}"
ORIGINAL_COMMIT="a171f150ebf9e9a6684538c992f98811fc8d4f45"
cd "$ROOT"

tracked=(
  CHANGELOG.md
  DIFF_FILE
  MODIFIED_FILE
  README.md
  VERIFICATION.txt
  browser_worker.py
  controller.py
  database.py
  demographics_enricher.py
  input_loader.py
  models.py
  proxy_pool.py
  source_rewriter.py
  tests/test_browser_strategy.py
  tests/test_controller_offline.py
  tests/test_demographics_enricher.py
  tests/test_input_loader.py
  tests/test_proxy_pool.py
  tests/test_source_rewriter.py
  version.py
)
for file in "${tracked[@]}"; do
  git show "${ORIGINAL_COMMIT}:${file}" > "$file"
done
rm -f RELEASE_NOTES_v1.1.4.md
git show "${ORIGINAL_COMMIT}:ROLLBACK.sh" > ROLLBACK.sh.restored

PYTHON_BIN="${PYTHON_BIN:-C:/Python312/python.exe}"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -B -m unittest discover -s tests -v

echo "ROLLBACK_RESULT=restored_v1.1.3_behavior"
mv -f ROLLBACK.sh.restored ROLLBACK.sh

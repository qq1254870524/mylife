#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")" && pwd)}"
ORIGINAL_COMMIT="b462072d4a37f362722f2660f51d80d389c63f40"
cd "$ROOT"

tracked=(
  CHANGELOG.md
  README.md
  input_loader.py
  source_rewriter.py
  tests/test_input_loader.py
  tests/test_source_rewriter.py
  version.py
)

for file in "${tracked[@]}"; do
  git show "${ORIGINAL_COMMIT}:${file}" > "$file"
done
rm -f row_identity.py RELEASE_NOTES_v1.1.3.md

PYTHON_BIN="${PYTHON_BIN:-C:/Python312/python.exe}"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -B -m unittest \
  tests.test_input_loader tests.test_source_rewriter -v

echo "ROLLBACK_RESULT=restored_v1.1.2_full_row_matching_behavior"

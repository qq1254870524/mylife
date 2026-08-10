#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-$(cd "$(dirname "$0")" && pwd)}"
PYTHON_BIN="$(command -v python3 || command -v python)"

"$PYTHON_BIN" - "$TARGET" <<'PY'
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
base_commit = "9d374a5bf0bd81c00381352b7c2cb71d8da891f4"
expected_modified_hash = "D2536C0098902A1BF2E9B97C1996B90D3DC018765E618DE3A78C153F1763A492"
expected_restored_hash = "4449BFAF311FE7F49DE47157A83E422D8B0806F2A69C4697E818E545DF94C52A"
preserve = {"ROLLBACK.sh", "VERIFICATION.txt", "MODIFIED_FILE", "DIFF_FILE"}

if not (root / ".git").exists():
    raise SystemExit(f"ROLLBACK_ABORT missing git metadata: {root}")
modified_hash = hashlib.sha256((root / "controller.py").read_bytes()).hexdigest().upper()
if modified_hash != expected_modified_hash:
    raise SystemExit(f"ROLLBACK_ABORT modified controller hash mismatch: {modified_hash}")

def git_bytes(*args: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if process.returncode:
        raise SystemExit(process.stderr.decode(errors="replace"))
    return process.stdout

changed = [
    item.decode("utf-8")
    for item in git_bytes("diff", "--name-only", "-z", f"{base_commit}..HEAD").split(b"\0")
    if item and item.decode("utf-8") not in preserve
]
base_files = {
    item.decode("utf-8")
    for item in git_bytes("ls-tree", "-r", "--name-only", "-z", base_commit).split(b"\0")
    if item
}
for relative in changed:
    path = root / relative
    if relative in base_files:
        data = git_bytes("show", f"{base_commit}:{relative}")
        temporary = path.with_name(path.name + ".rollback_tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(data)
        os.replace(temporary, path)
    elif path.is_file() or path.is_symlink():
        path.unlink()

restored_hash = hashlib.sha256((root / "controller.py").read_bytes()).hexdigest().upper()
if restored_hash != expected_restored_hash:
    raise SystemExit(f"ROLLBACK_ABORT restored controller hash mismatch: {restored_hash}")
print(
    f"ROLLBACK_OK base={base_commit[:7]} files={len(changed)} "
    f"controller_sha256={restored_hash} restored_behavior=xlsx_xlsm_realtime_row_deletion"
)
PY

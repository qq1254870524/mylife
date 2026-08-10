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
base_commit = "24588fa8d50d2fbd5611d9a040b9c0452966ebb5"
expected_modified_version = "1.1.1"
expected_restored_hash = "8740E1202A509450D8164DECBB30E06C5D9E307209FE1C9D3916E8A59E52798C"
preserve = {"ROLLBACK.sh", "VERIFICATION.txt", "MODIFIED_FILE", "DIFF_FILE"}

if not (root / ".git").exists():
    raise SystemExit(f"ROLLBACK_ABORT missing git metadata: {root}")
version_text = (root / "version.py").read_text(encoding="utf-8")
if expected_modified_version not in version_text:
    raise SystemExit("ROLLBACK_ABORT expected v1.1.1 source was not found")

def git_bytes(*args: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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

restored_hash = hashlib.sha256((root / "version.py").read_bytes()).hexdigest().upper()
if restored_hash != expected_restored_hash:
    raise SystemExit(f"ROLLBACK_ABORT restored version hash mismatch: {restored_hash}")
if (root / "identity_matcher.py").exists() or (root / "runtime_monitor.py").exists():
    raise SystemExit("ROLLBACK_ABORT v1.1.0-only module still exists")
print(
    f"ROLLBACK_OK base={base_commit[:7]} files={len(changed)} "
    f"version_sha256={restored_hash} restored_behavior=v1.0.1_launcher_and_v1.0.0_collection"
)
PY

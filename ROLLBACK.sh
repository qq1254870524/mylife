#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-$(cd "$(dirname "$0")" && pwd)}"

python - "$TARGET" <<'PY'
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
base_commit = "91399689e0077374c151c56ded7a5d7e16d6b2d2"
launcher = "启动MyLife正式版.cmd"
expected_fixed = "C8B2D5AAA4DA493053A65147E66E0C2A6350D08BD7393C2352B3F0A84705D9BF"
expected_original = "64837F9A1477162492D59D571839DC17C9C1D7264C3A4B10DCBC2D1C70FB1352"

if not (root / ".git").exists():
    raise SystemExit(f"ROLLBACK_ABORT missing git metadata: {root}")
current = hashlib.sha256((root / launcher).read_bytes()).hexdigest().upper()
if current != expected_fixed:
    raise SystemExit(f"ROLLBACK_ABORT launcher hash mismatch: {current}")

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
    if item and item.decode("utf-8") != "ROLLBACK.sh"
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

restored = hashlib.sha256((root / launcher).read_bytes()).hexdigest().upper()
if restored != expected_original:
    raise SystemExit(f"ROLLBACK_ABORT restored hash mismatch: {restored}")
print(
    f"ROLLBACK_OK base={base_commit[:7]} files={len(changed)} "
    "launcher_sha256=64837F9A1477162492D59D571839DC17C9C1D7264C3A4B10DCBC2D1C70FB1352 "
    "restored_behavior=lf_only_v1.0.0"
)
PY

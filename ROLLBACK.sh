#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:-$(cd "$(dirname "$0")" && pwd)}"
PYTHON_BIN="$(command -v python3 || command -v python)"
"$PYTHON_BIN" - "$TARGET" <<'PY'
from __future__ import annotations
import hashlib, os, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
base_commit = "628d2035526d0c21a6bc4ec40f46c25272ebb81c"
expected_modified_hash = "B5B371E7E67742EE868548EC5FD2FFA7477BA7BDBDEE6E16F5E590245B7CB392"
expected_restored_hash = "D2536C0098902A1BF2E9B97C1996B90D3DC018765E618DE3A78C153F1763A492"
preserve = {"ROLLBACK.sh", "VERIFICATION.txt", "MODIFIED_FILE", "DIFF_FILE"}
if not (root / ".git").exists(): raise SystemExit(f"ROLLBACK_ABORT missing git metadata: {root}")
modified_hash = hashlib.sha256((root / "controller.py").read_bytes()).hexdigest().upper()
if modified_hash != expected_modified_hash: raise SystemExit(f"ROLLBACK_ABORT modified controller hash mismatch: {modified_hash}")
def git_bytes(*args: str) -> bytes:
    p=subprocess.run(["git","-C",str(root),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode: raise SystemExit(p.stderr.decode(errors="replace"))
    return p.stdout
changed=[x.decode("utf-8") for x in git_bytes("diff","--name-only","-z",f"{base_commit}..HEAD").split(b"\0") if x and x.decode("utf-8") not in preserve]
base_files={x.decode("utf-8") for x in git_bytes("ls-tree","-r","--name-only","-z",base_commit).split(b"\0") if x}
for relative in changed:
    path=root/relative
    if relative in base_files:
        data=git_bytes("show",f"{base_commit}:{relative}")
        temporary=path.with_name(path.name+".rollback_tmp")
        temporary.parent.mkdir(parents=True,exist_ok=True)
        temporary.write_bytes(data); os.replace(temporary,path)
    elif path.is_file() or path.is_symlink(): path.unlink()
restored_hash=hashlib.sha256((root/"controller.py").read_bytes()).hexdigest().upper()
if restored_hash != expected_restored_hash: raise SystemExit(f"ROLLBACK_ABORT restored controller hash mismatch: {restored_hash}")
print(f"ROLLBACK_OK base={base_commit[:7]} files={len(changed)} controller_sha256={restored_hash} restored_behavior=stop_keeps_deferred_xlsx_rows")
PY

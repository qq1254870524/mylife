#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:-$(cd "$(dirname "$0")" && pwd)}"
PYTHON_BIN="$(command -v python3 || command -v python)"
"$PYTHON_BIN" - "$TARGET" <<'PY'
from __future__ import annotations
import hashlib, os, subprocess, sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
base_commit="d898737cd034419bc0f3492562a1ffd46dc0862f"
relative="启动MyLife正式版.cmd"
expected_modified="11566CB78669E0CE38C538498523BC9487BADBEB284F8E89CB9213AAB2A8C31F"
expected_restored="C8B2D5AAA4DA493053A65147E66E0C2A6350D08BD7393C2352B3F0A84705D9BF"
preserve={"ROLLBACK.sh","VERIFICATION.txt","MODIFIED_FILE","DIFF_FILE"}
if not (root/".git").exists(): raise SystemExit(f"ROLLBACK_ABORT missing git metadata: {root}")
current=hashlib.sha256((root/relative).read_bytes()).hexdigest().upper()
if current!=expected_modified: raise SystemExit(f"ROLLBACK_ABORT modified launcher hash mismatch: {current}")
def git_bytes(*args):
    p=subprocess.run(["git","-C",str(root),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode: raise SystemExit(p.stderr.decode(errors="replace"))
    return p.stdout
changed=[x.decode("utf-8") for x in git_bytes("diff","--name-only","-z",f"{base_commit}..HEAD").split(b"\0") if x and x.decode("utf-8") not in preserve]
for name in changed:
    path=root/name; data=git_bytes("show",f"{base_commit}:{name}")
    tmp=path.with_name(path.name+".rollback_tmp"); tmp.parent.mkdir(parents=True,exist_ok=True); tmp.write_bytes(data); os.replace(tmp,path)
restored=hashlib.sha256((root/relative).read_bytes()).hexdigest().upper()
if restored!=expected_restored: raise SystemExit(f"ROLLBACK_ABORT restored launcher hash mismatch: {restored}")
print(f"ROLLBACK_OK base={base_commit[:7]} files={len(changed)} launcher_sha256={restored} restored_behavior=blocking_console_python_launcher")
PY

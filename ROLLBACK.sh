#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-$(cd "$(dirname "$0")" && pwd)}"

python - "$TARGET" <<'PY'
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
expected = {
    "turnstile_harvester.py": "BEA2D6F251594CABDF5748FC24A188D18DF1DA9E8AB93D5394D4F1637B046654",
    "测试文件1.xlsx": "9D00BD8F72DBD0CFF6E7AD380F62CAD31397C45F32B2D9C4C03121325158EC36",
    "测试文件2.csv": "7F74F23E2B56656D468EDBE620E46FF12342D3007DBDF5E51514E41604772DC9",
    "设计思路.txt": "8588065E7D5F193A614CF1439417EF7CFB15C5450702F0A7BE86052B5918D94A",
}
for name, digest in expected.items():
    path = root / name
    if not path.is_file():
        raise SystemExit(f"ROLLBACK_ABORT missing original: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    if actual != digest:
        raise SystemExit(f"ROLLBACK_ABORT original hash changed: {name}")

files = {
    ".gitignore", ".mylife_gui_settings.json", "bootstrap.py", "browser_worker.py",
    "CHANGELOG.md", "cloudflare_handler.py", "controller.py", "database.py", "DIFF_FILE",
    "gui.py", "input_loader.py", "main.py", "models.py", "MODIFIED_FILE", "mylife_parser.py",
    "output_writer.py", "proxy_pool.py", "README.md", "RELEASE_NOTES_v1.0.0.md", "requirements.txt", "ROLLBACK.sh",
    "socks_bridge.py", "source_rewriter.py", "VERIFICATION.txt", "version.py", "启动MyLife正式版.cmd",
}
directories = ["tests", "verification", ".pytest_cache", "__pycache__", ".git"]
for name in files:
    path = root / name
    if path.is_file() or path.is_symlink():
        path.unlink()
for name in directories:
    path = root / name
    if path.is_dir():
        def remove_readonly(function, target, _error):
            os.chmod(target, stat.S_IWRITE)
            function(target)
        shutil.rmtree(path, onexc=remove_readonly)
print("ROLLBACK_OK originals=4 restored_behavior=baseline_no_tests")
PY

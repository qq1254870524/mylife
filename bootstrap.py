from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


REQUIRED = {
    "patchright": "patchright>=1.61.2,<1.62",
    "openpyxl": "openpyxl>=3.1.5,<4",
    "requests": "requests[socks]>=2.32.5,<3",
    "bs4": "beautifulsoup4>=4.14.3,<5",
    "tzdata": "tzdata>=2025.3",
}


def ensure_dependencies() -> None:
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    missing = [requirement for module, requirement in REQUIRED.items() if importlib.util.find_spec(module) is None]
    if missing:
        print("缺少依赖，正在安装：" + ", ".join(missing))
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    else:
        print("依赖检查通过，已安装项全部跳过。")
    chrome_candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    if not any(path.is_file() for path in chrome_candidates):
        print("未找到 Google Chrome，正在通过 Patchright 安装 Chrome。")
        subprocess.check_call([sys.executable, "-m", "patchright", "install", "chrome"])


if __name__ == "__main__":
    ensure_dependencies()

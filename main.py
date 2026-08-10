from __future__ import annotations

import argparse
import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def main() -> int:
    parser = argparse.ArgumentParser(description="MyLife 正式版 GUI")
    parser.add_argument("--check", action="store_true", help="仅执行依赖与模块自检")
    parser.add_argument("--auto-start", action="store_true", help="载入上次 GUI 配置后自动开始")
    args = parser.parse_args()
    from bootstrap import ensure_dependencies

    ensure_dependencies()
    if args.check:
        from input_loader import load_people  # noqa: F401
        from mylife_parser import parse_profile_html  # noqa: F401
        from proxy_pool import parse_proxy_line  # noqa: F401

        print("SELF_CHECK_OK")
        return 0
    from gui import run_gui

    run_gui(auto_start=args.auto_start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

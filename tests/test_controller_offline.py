from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller import AppController
from models import ProfileResult, RunConfig


class FakeBrowserSession:
    def __init__(self, **_kwargs: object) -> None:
        self.page = None

    def start(self, fresh_profile: bool = False) -> None:
        self.page = object()

    def process(self, person: object) -> list[ProfileResult]:
        return [
            ProfileResult(1, "https://www.mylife.com/jane-doe/e1", full_name="Jane Doe", birthday="March 7, 1984"),
            ProfileResult(2, "https://www.mylife.com/jane-doe/e2", full_name="Jane Doe", birthday="April 8, 1985"),
        ]

    def clear_person_data(self) -> None:
        pass

    def close(self) -> None:
        pass

    def rebuild(self, proxy_geo: object = None) -> None:
        self.page = object()


class ControllerOfflineTests(unittest.TestCase):
    def test_full_pipeline_realtime_output_then_single_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "people.csv"
            source.write_text("first_name,last_name,note\nJane,Doe,keep\n", encoding="utf-8-sig")
            output = root / "output"
            config = RunConfig(source, output, thread_count=2, browser_mode="无头")
            controller = AppController(config)
            with patch("controller.BrowserSession", FakeBrowserSession):
                controller.run()
            result_path = output / "people_MyLife结果.csv"
            with result_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["note"], "keep")
            self.assertEqual(rows[1]["mylife_birthday"], "April 8, 1985")
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                remaining = list(csv.reader(handle))
            self.assertEqual(remaining, [["first_name", "last_name", "note"]])
            self.assertFalse((output / ".mylife_runtime" / "profiles").exists())

    def test_proxy_thread_limit_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "people.txt"
            source.write_text("Jane Doe\n", encoding="utf-8")
            config = RunConfig(
                source,
                root / "out",
                thread_count=2,
                proxy_enabled=True,
                proxy_lines=["proxy.example:1080:u:p|https://refresh.example/change"],
            )
            controller = AppController(config)
            with self.assertRaisesRegex(ValueError, "线程数量不可以超过代理数量"):
                controller._validate()


if __name__ == "__main__":
    unittest.main()

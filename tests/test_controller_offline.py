from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from controller import AppController, _is_proxy_network_error
from models import ProfileResult, RunConfig


class FakeBrowserSession:
    observed_input_counts: list[int] = []

    def __init__(self, **_kwargs: object) -> None:
        self.page = None

    def start(self, fresh_profile: bool = False) -> None:
        self.page = object()

    def process(self, person: object) -> list[ProfileResult]:
        source = getattr(person, "source_path")
        if source.suffix.lower() in {".xlsx", ".xlsm"}:
            workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
            try:
                self.observed_input_counts.append(max(0, workbook.active.max_row - 1))
            finally:
                workbook.close()
        else:
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                self.observed_input_counts.append(max(0, len(list(csv.reader(handle))) - 1))
        return [
            ProfileResult(1, "https://www.mylife.com/jane-doe/e1", full_name="Jane Doe", birthday="March 7, 1984", gender="Female", zodiac="Pisces (February 19 - March 20)"),
        ]

    def clear_person_data(self) -> None:
        pass

    def close(self) -> None:
        pass

    def rebuild(self, proxy_geo: object = None) -> None:
        self.page = object()


class ControllerOfflineTests(unittest.TestCase):
    def test_proxy_browser_connection_error_is_detected_for_refresh(self) -> None:
        self.assertTrue(_is_proxy_network_error("Page.goto: net::ERR_SOCKS_CONNECTION_FAILED"))
        self.assertFalse(_is_proxy_network_error("selector not found"))

    def test_full_pipeline_deletes_each_explicit_result_in_realtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "people.csv"
            source.write_text(
                "first_name,last_name,note\nJane,Doe,first\nJohn,Doe,second\n",
                encoding="utf-8-sig",
            )
            output = root / "output"
            config = RunConfig(source, output, thread_count=1, browser_mode="无头")
            progress_states: list[dict[str, int | str]] = []
            controller = AppController(config, progress=progress_states.append)
            FakeBrowserSession.observed_input_counts = []
            with patch("controller.BrowserSession", FakeBrowserSession):
                controller.run()
            current_run_states = [state for state in progress_states if state.get("total") == 2]
            self.assertTrue(current_run_states)
            self.assertTrue(any(state.get("output", "").endswith("people_MyLife结果.csv") for state in current_run_states))
            result_path = output / "people_MyLife结果.csv"
            with result_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["note"] for row in rows], ["first", "second"])
            self.assertEqual(list(rows[0])[-4:], ["生日", "性别", "星座", "备注原因"])
            self.assertIn("搜索方式：", rows[0]["备注原因"])
            self.assertEqual(rows[0]["生日"], "March 7, 1984")
            self.assertEqual(rows[0]["性别"], "Female")
            self.assertEqual(rows[0]["星座"], "Pisces (February 19 - March 20)")
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                remaining = list(csv.reader(handle))
            self.assertEqual(remaining, [["first_name", "last_name", "note"]])
            self.assertEqual(sorted(FakeBrowserSession.observed_input_counts), [1, 2])
            self.assertFalse((output / ".mylife_runtime" / "profiles").exists())

    def test_xlsx_keeps_all_rows_during_processing_then_deletes_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "people.xlsx"
            workbook = openpyxl.Workbook()
            workbook.active.append(["first_name", "last_name", "note"])
            workbook.active.append(["Jane", "Doe", "first"])
            workbook.active.append(["John", "Doe", "second"])
            workbook.create_sheet("keep").append(["other sheet", "must stay"])
            workbook.save(source)
            workbook.close()
            controller = AppController(RunConfig(source, root / "output", thread_count=1, browser_mode="无头"))
            FakeBrowserSession.observed_input_counts = []
            with patch("controller.BrowserSession", FakeBrowserSession):
                controller.run()
            self.assertEqual(FakeBrowserSession.observed_input_counts, [2, 2])
            workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
            try:
                self.assertEqual(list(workbook.active.iter_rows(values_only=True)), [("first_name", "last_name", "note")])
                self.assertEqual(list(workbook["keep"].iter_rows(values_only=True)), [("other sheet", "must stay")])
            finally:
                workbook.close()

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

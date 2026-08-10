from __future__ import annotations

import unittest
from pathlib import Path

from gui import MainWindow


class DummyVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value


class DummyLog:
    def __init__(self) -> None:
        self.text = "旧测试文件1日志"
        self.states: list[str] = []

    def configure(self, *, state: str) -> None:
        self.states.append(state)

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _where: str, value: str) -> None:
        self.text += value

    def see(self, _where: str) -> None:
        pass


class GuiStateTests(unittest.TestCase):
    def test_new_input_clears_previous_counts_and_log_before_controller_progress(self) -> None:
        window = object.__new__(MainWindow)
        window.count_var = DummyVar("总数 3558｜完成 34")
        window.output_status_var = DummyVar("实时 CSV：测试文件1_MyLife结果.csv")
        window.log_text = DummyLog()

        window._reset_run_display(Path("C:/Users/zhang/Desktop/mylife/测试文件2.csv"))

        self.assertIn("总数 0", window.count_var.value)
        self.assertEqual(window.output_status_var.value, "")
        self.assertNotIn("测试文件1", window.log_text.text)
        self.assertIn("测试文件2.csv", window.log_text.text)
        self.assertEqual(window.log_text.states, ["normal", "disabled"])


if __name__ == "__main__":
    unittest.main()

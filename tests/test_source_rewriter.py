from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import openpyxl

from source_rewriter import remove_completed_rows


class SourceRewriterTests(unittest.TestCase):
    def test_csv_is_rebuilt_once_by_first_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("name,note\nJane Doe,a\nJohn Doe,b\nJane Doe,c\n", encoding="utf-8-sig")
            removed = remove_completed_rows(path, {"Jane Doe"})
            self.assertEqual(removed, 2)
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows, [["name", "note"], ["John Doe", "b"]])

    def test_xlsx_rebuild_preserves_unprocessed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(["name", "note"])
            sheet.append(["Jane Doe", "a"])
            sheet.append(["John Doe", "b"])
            workbook.save(path)
            workbook.close()
            self.assertEqual(remove_completed_rows(path, {"Jane Doe"}), 1)
            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
            rows = list(workbook.active.iter_rows(values_only=True))
            workbook.close()
            self.assertEqual(rows, [("name", "note"), ("John Doe", "b")])


if __name__ == "__main__":
    unittest.main()

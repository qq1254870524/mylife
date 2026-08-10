from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from runtime_monitor import RuntimeHealthMonitor


class RuntimeMonitorTests(unittest.TestCase):
    def test_csv_audit_counts_distinct_birthdays_and_remarks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["生日", "性别", "星座", "备注原因"])
                writer.writeheader()
                writer.writerow({"生日": "01/01/1980", "性别": "Female", "星座": "Capricorn", "备注原因": "命中"})
                writer.writerow({"生日": "01/01/1980", "性别": "Male", "星座": "Capricorn", "备注原因": "命中"})
                writer.writerow({"生日": "", "性别": "", "星座": "", "备注原因": "未公开"})
            audit = RuntimeHealthMonitor.audit_csv(path)
            self.assertEqual(audit["rows"], 3)
            self.assertEqual(audit["birthdays"], 2)
            self.assertEqual(audit["unique_birthdays"], 1)
            self.assertEqual(audit["remarks"], 3)


if __name__ == "__main__":
    unittest.main()

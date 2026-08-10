from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from database import DatabaseWriter, JobDatabase
from models import PersonInput, ProfileResult
from output_writer import RealtimeCsvWriter


class DatabaseOutputTests(unittest.TestCase):
    def test_wal_single_writer_and_realtime_bom_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.csv"
            input_path.write_text("name,note\nJane Doe,keep\n", encoding="utf-8")
            person = PersonInput(
                source_path=input_path.resolve(),
                source_row=2,
                headers=["name", "note"],
                original={"name": "Jane Doe", "note": "keep"},
                first_value="Jane Doe",
                first_name="Jane",
                last_name="Doe",
            )
            database = JobDatabase(root / "state.sqlite3")
            database.import_people([person])
            job_id, _, _ = database.pending_people(input_path, 3)[0]
            csv_writer = RealtimeCsvWriter(root, input_path, person.headers)
            writer = DatabaseWriter(database, csv_writer, lambda _message: None)
            writer.start()
            writer.put("running", job_id)
            writer.put(
                "result",
                job_id,
                person,
                ProfileResult(1, "https://www.mylife.com/jane-doe/e1", full_name="Jane Doe", birthday="March 7, 1984", gender="Female", zodiac="Pisces (February 19 - March 20)"),
            )
            writer.put("done", job_id, "ok")
            writer.flush()
            raw = csv_writer.path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            with csv_writer.path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["name"], "Jane Doe")
            self.assertEqual(list(rows[0])[-3:], ["生日", "性别", "星座"])
            self.assertEqual(rows[0]["生日"], "March 7, 1984")
            self.assertEqual(rows[0]["性别"], "Female")
            self.assertEqual(rows[0]["星座"], "Pisces (February 19 - March 20)")
            replayed = database.existing_results(input_path)
            self.assertEqual(len(replayed), 1)
            self.assertFalse(csv_writer.append(*replayed[0], restoring=True))
            writer.close()
            with closing(sqlite3.connect(database.path)) as connection:
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
            self.assertEqual(database.summary(input_path).get("done"), 1)

    def test_two_identical_source_rows_are_preserved_and_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "same.csv"
            input_path.write_text("name\nJane Doe\nJane Doe\n", encoding="utf-8")
            original = {"name": "Jane Doe"}
            result = {"birthday": "01/01/1980", "gender": "Female", "zodiac": "Capricorn"}
            writer = RealtimeCsvWriter(root, input_path, ["name"])
            self.assertTrue(writer.append(original, result, "t1"))
            self.assertTrue(writer.append(original, result, "t2"))
            writer.close()

            replay = RealtimeCsvWriter(root, input_path, ["name"])
            self.assertFalse(replay.append(original, result, "t1", restoring=True))
            self.assertFalse(replay.append(original, result, "t2", restoring=True))
            self.assertTrue(replay.append(original, result, "t3", restoring=True))
            replay.close()
            with (root / "same_MyLife结果.csv").open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 3)


if __name__ == "__main__":
    unittest.main()

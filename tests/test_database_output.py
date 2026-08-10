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
    def test_proxy_network_retry_does_not_consume_person_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.csv"
            input_path.write_text("name\nJane Doe\n", encoding="utf-8")
            person = PersonInput(
                source_path=input_path.resolve(),
                source_row=2,
                headers=["name"],
                original={"name": "Jane Doe"},
                first_value="Jane Doe",
                first_name="Jane",
                last_name="Doe",
            )
            database = JobDatabase(root / "state.sqlite3")
            database.import_people([person])
            job_id, _, _ = database.pending_people(input_path, 3)[0]
            output = RealtimeCsvWriter(root, input_path, ["name"])
            writer = DatabaseWriter(database, output, lambda _message: None)
            writer.start()
            writer.put("running", job_id)
            writer.put("retry_network", job_id, "ERR_SOCKS_CONNECTION_FAILED")
            writer.flush()
            writer.close()
            with closing(sqlite3.connect(database.path)) as connection:
                status, attempts = connection.execute(
                    "SELECT status, attempts FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
            self.assertEqual(status, "retry")
            self.assertEqual(attempts, 0)

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
            self.assertEqual(list(rows[0])[-4:], ["生日", "性别", "星座", "备注原因"])
            self.assertEqual(rows[0]["生日"], "March 7, 1984")
            self.assertEqual(rows[0]["性别"], "Female")
            self.assertEqual(rows[0]["星座"], "Pisces (February 19 - March 20)")
            self.assertIn("搜索方式：", rows[0]["备注原因"])
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

    def test_old_blank_birthday_is_requeued_once_and_csv_can_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.csv"
            input_path.write_text("name\nJane Doe\n", encoding="utf-8")
            person = PersonInput(
                source_path=input_path.resolve(),
                source_row=2,
                headers=["name"],
                original={"name": "Jane Doe"},
                first_value="Jane Doe",
                first_name="Jane",
                last_name="Doe",
            )
            database = JobDatabase(root / "state.sqlite3")
            database.import_people([person])
            job_id, _, _ = database.pending_people(input_path, 3)[0]
            old = ProfileResult(1, "", status="无结果", message="old", search_revision=1)
            output = RealtimeCsvWriter(root, input_path, ["name"])
            writer = DatabaseWriter(database, output, lambda _message: None)
            writer.start()
            writer.put("result", job_id, person, old)
            writer.put("done", job_id, "old")
            writer.flush()
            writer.close()
            self.assertEqual(database.reset_incomplete_birthdays(input_path, 2), 1)
            self.assertEqual(database.reset_incomplete_birthdays(input_path, 2), 0)
            rebuilt = RealtimeCsvWriter(root, input_path, ["name"], rebuild=True)
            rebuilt.close()
            with (root / "input_MyLife结果.csv").open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])
            self.assertEqual(database.summary(input_path).get("pending"), 1)

    def test_failed_job_is_released_on_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.csv"
            input_path.write_text("name\nJane Doe\n", encoding="utf-8")
            person = PersonInput(
                source_path=input_path.resolve(),
                source_row=2,
                headers=["name"],
                original={"name": "Jane Doe"},
                first_value="Jane Doe",
                first_name="Jane",
                last_name="Doe",
            )
            database = JobDatabase(root / "state.sqlite3")
            database.import_people([person])
            job_id, _, _ = database.pending_people(input_path, 3)[0]
            with closing(sqlite3.connect(database.path)) as connection, connection:
                connection.execute("UPDATE jobs SET status='failed', attempts=3 WHERE id=?", (job_id,))
            self.assertEqual(database.reset_failed_for_new_run(input_path), 1)
            jobs = database.pending_people(input_path, 3)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0][2], 0)

    def test_old_gender_only_gap_is_requeued_by_demographic_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.csv"
            input_path.write_text("name\nJane Doe\n", encoding="utf-8")
            person = PersonInput(
                source_path=input_path.resolve(), source_row=2, headers=["name"],
                original={"name": "Jane Doe"}, first_value="Jane Doe",
                first_name="Jane", last_name="Doe",
            )
            database = JobDatabase(root / "state.sqlite3")
            database.import_people([person])
            job_id, _, _ = database.pending_people(input_path, 3)[0]
            result = ProfileResult(
                1, "https://www.mylife.com/jane-doe/e1", birthday="03/07/1984",
                zodiac="Pisces (February 19 - March 20)", search_revision=2,
            )
            output = RealtimeCsvWriter(root, input_path, ["name"])
            writer = DatabaseWriter(database, output, lambda _message: None)
            writer.start()
            writer.put("result", job_id, person, result)
            writer.put("done", job_id, "old")
            writer.flush(); writer.close()
            self.assertEqual(database.reset_incomplete_demographics(input_path, 3), 1)
            self.assertEqual(database.summary(input_path).get("pending"), 1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from models import PersonInput, ProfileResult
from output_writer import RealtimeCsvWriter


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    first_value TEXT NOT NULL,
    headers_json TEXT NOT NULL,
    original_json TEXT NOT NULL,
    first_name TEXT,
    middle_name TEXT,
    last_name TEXT,
    city TEXT,
    state TEXT,
    zip_code TEXT,
    validation_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_path, source_row)
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    result_index INTEGER NOT NULL,
    profile_url TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, result_index, profile_url)
);
"""


class JobDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def import_people(self, people: list[PersonInput]) -> int:
        now = utc_now()
        inserted = 0
        with closing(self.connect()) as connection, connection:
            for person in people:
                before = connection.total_changes
                connection.execute(
                    """
                    INSERT OR IGNORE INTO jobs (
                        source_path, source_row, first_value, headers_json, original_json,
                        first_name, middle_name, last_name, city, state, zip_code,
                        validation_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(person.source_path), person.source_row, person.first_value,
                        json.dumps(person.headers, ensure_ascii=False),
                        json.dumps(person.original, ensure_ascii=False),
                        person.first_name, person.middle_name, person.last_name,
                        person.city, person.state, person.zip_code,
                        person.validation_error, now, now,
                    ),
                )
                inserted += connection.total_changes - before
            connection.commit()
        return inserted

    def reset_interrupted(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE jobs SET status='pending', updated_at=? WHERE status='running'",
                (utc_now(),),
            )
            connection.commit()

    def pending_people(self, source_path: Path, max_attempts: int) -> list[tuple[int, PersonInput, int]]:
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE source_path=? AND status IN ('pending','retry') AND attempts < ? ORDER BY source_row",
                (str(source_path.resolve()), max_attempts),
            ).fetchall()
        jobs: list[tuple[int, PersonInput, int]] = []
        for row in rows:
            jobs.append(
                (
                    int(row["id"]),
                    PersonInput(
                        source_path=Path(row["source_path"]),
                        source_row=int(row["source_row"]),
                        headers=json.loads(row["headers_json"]),
                        original=json.loads(row["original_json"]),
                        first_value=row["first_value"],
                        first_name=row["first_name"] or "",
                        middle_name=row["middle_name"] or "",
                        last_name=row["last_name"] or "",
                        city=row["city"] or "",
                        state=row["state"] or "",
                        zip_code=row["zip_code"] or "",
                        validation_error=row["validation_error"] or "",
                    ),
                    int(row["attempts"]),
                )
            )
        return jobs

    def summary(self, source_path: Path) -> dict[str, int]:
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs WHERE source_path=? GROUP BY status",
                (str(source_path.resolve()),),
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}


class DatabaseWriter(threading.Thread):
    def __init__(self, database: JobDatabase, csv_writer: RealtimeCsvWriter, log: Callable[[str], None]) -> None:
        super().__init__(name="database-writer", daemon=False)
        self.database = database
        self.csv_writer = csv_writer
        self.log = log
        self.events: queue.Queue[tuple[str, tuple[Any, ...], threading.Event | None]] = queue.Queue()
        self.error: BaseException | None = None

    def put(self, event: str, *payload: Any) -> None:
        self.events.put((event, payload, None))

    def flush(self) -> None:
        done = threading.Event()
        self.events.put(("flush", (), done))
        done.wait(timeout=30)
        if self.error:
            raise RuntimeError("数据库写入线程失败") from self.error

    def close(self) -> None:
        done = threading.Event()
        self.events.put(("stop", (), done))
        done.wait(timeout=30)
        self.join(timeout=30)
        if self.error:
            raise RuntimeError("数据库写入线程失败") from self.error

    def _handle(self, connection: sqlite3.Connection, event: str, payload: tuple[Any, ...]) -> None:
        now = utc_now()
        if event == "running":
            job_id = int(payload[0])
            connection.execute(
                "UPDATE jobs SET status='running', attempts=attempts+1, message='', updated_at=? WHERE id=?",
                (now, job_id),
            )
        elif event == "result":
            job_id, person, result = payload
            assert isinstance(person, PersonInput)
            assert isinstance(result, ProfileResult)
            data = result.as_dict()
            cursor = connection.execute(
                "INSERT OR IGNORE INTO results(job_id,result_index,profile_url,result_json,created_at) VALUES(?,?,?,?,?)",
                (job_id, result.result_index, result.profile_url, json.dumps(data, ensure_ascii=False), now),
            )
            if cursor.rowcount:
                self.csv_writer.append(person.original, data, now)
        elif event == "done":
            job_id, message = payload
            connection.execute(
                "UPDATE jobs SET status='done', message=?, updated_at=? WHERE id=?",
                (str(message), now, int(job_id)),
            )
        elif event == "retry":
            job_id, message = payload
            connection.execute(
                "UPDATE jobs SET status='retry', message=?, updated_at=? WHERE id=?",
                (str(message), now, int(job_id)),
            )
        elif event == "failed":
            job_id, message = payload
            connection.execute(
                "UPDATE jobs SET status='failed', message=?, updated_at=? WHERE id=?",
                (str(message), now, int(job_id)),
            )

    def run(self) -> None:
        connection = self.database.connect()
        try:
            while True:
                event, payload, done = self.events.get()
                try:
                    if event == "stop":
                        connection.commit()
                        self.csv_writer.close()
                        if done:
                            done.set()
                        return
                    if event == "flush":
                        connection.commit()
                    else:
                        self._handle(connection, event, payload)
                        connection.commit()
                    if done:
                        done.set()
                except BaseException as exc:
                    connection.rollback()
                    self.error = exc
                    self.log(f"数据库写入错误：{type(exc).__name__}: {exc}")
                    if done:
                        done.set()
        finally:
            connection.close()

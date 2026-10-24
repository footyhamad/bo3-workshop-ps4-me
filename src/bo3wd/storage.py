"""Atomic settings plus a small SQLite-backed persistent job/history store."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import DownloadEvent, JobStatus, WorkshopItem


def default_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "BO3WorkshopDownloader"


class SettingsStore:
    """Thread-safe JSON settings with atomic replacement and safe defaults."""

    DEFAULTS: dict[str, Any] = {
        "steamcmd": "",
        "steam_user": "",
        "download_root": "",
        "validate_downloads": True,
        "max_attempts": 30,
        "attempt_timeout_minutes": 15,
        "initial_backoff_seconds": 3,
        "max_backoff_seconds": 45,
        "max_stagnant_attempts": 8,
        "theme": "dark",
    }

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            current = dict(self.DEFAULTS)
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return current
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Settings file is unreadable: {self.path}: {exc}") from exc
            if not isinstance(stored, dict):
                raise RuntimeError(f"Settings file must contain a JSON object: {self.path}")
            current.update(stored)
            return current

    def save(self, values: dict[str, Any]) -> None:
        if not isinstance(values, dict):
            raise TypeError("settings must be a dictionary")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(values, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise


class JobHistory:
    """Persistent latest state per Workshop ID; SQLite operations are short and isolated."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back a transaction, then always close its connection."""
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    workshop_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 0,
                    bytes_received INTEGER NOT NULL DEFAULT 0,
                    expected_bytes INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    log_path TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL,
                    queue_position INTEGER NOT NULL DEFAULT 0
                )"""
            )
            columns = {str(column[1]) for column in db.execute("PRAGMA table_info(jobs)").fetchall()}
            if "queue_position" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN queue_position INTEGER NOT NULL DEFAULT 0")
                existing = db.execute(
                    "SELECT workshop_id FROM jobs ORDER BY created_at ASC, updated_at ASC, workshop_id ASC"
                ).fetchall()
                for position, row in enumerate(existing):
                    db.execute(
                        "UPDATE jobs SET queue_position=? WHERE workshop_id=?",
                        (position, str(row["workshop_id"])),
                    )
            db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at DESC)")

    def ensure_item(self, item: WorkshopItem, log_path: Path | None = None) -> None:
        now = time.time()
        with self._session() as db:
            db.execute(
                """INSERT INTO jobs(workshop_id,title,url,status,expected_bytes,log_path,created_at,updated_at,queue_position)
                   VALUES(?,?,?,?,?,?,?,?,(SELECT COALESCE(MAX(queue_position)+1,0) FROM jobs))
                   ON CONFLICT(workshop_id) DO UPDATE SET
                     title=CASE WHEN excluded.title='' THEN jobs.title ELSE excluded.title END,
                     url=CASE WHEN excluded.url='' THEN jobs.url ELSE excluded.url END,
                     expected_bytes=CASE WHEN excluded.expected_bytes=0 THEN jobs.expected_bytes ELSE excluded.expected_bytes END,
                     log_path=CASE WHEN excluded.log_path='' THEN jobs.log_path ELSE excluded.log_path END,
                     status=CASE WHEN jobs.status IN ('preparing','downloading','retry_wait') THEN jobs.status ELSE excluded.status END,
                     updated_at=excluded.updated_at""",
                (item.workshop_id, item.title, item.url, JobStatus.QUEUED.value, item.expected_bytes,
                 str(log_path) if log_path is not None and str(log_path) != "." else "", now, now),
            )

    def update(self, event: DownloadEvent, title: str = "", url: str = "") -> None:
        now = time.time()
        finished = now if event.status.terminal else None
        with self._session() as db:
            db.execute(
                """INSERT INTO jobs(workshop_id,title,url,status,attempt,max_attempts,bytes_received,
                       expected_bytes,message,log_path,created_at,updated_at,finished_at,queue_position)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,(SELECT COALESCE(MAX(queue_position)+1,0) FROM jobs))
                   ON CONFLICT(workshop_id) DO UPDATE SET
                       title=CASE WHEN excluded.title='' THEN jobs.title ELSE excluded.title END,
                       url=CASE WHEN excluded.url='' THEN jobs.url ELSE excluded.url END,
                       status=excluded.status,
                       attempt=excluded.attempt,
                       max_attempts=excluded.max_attempts,
                       bytes_received=excluded.bytes_received,
                       expected_bytes=CASE WHEN excluded.expected_bytes=0 THEN jobs.expected_bytes ELSE excluded.expected_bytes END,
                       message=excluded.message,
                       log_path=CASE WHEN excluded.log_path='' THEN jobs.log_path ELSE excluded.log_path END,
                       updated_at=excluded.updated_at,
                       finished_at=CASE WHEN excluded.finished_at IS NOT NULL THEN excluded.finished_at ELSE NULL END""",
                (event.workshop_id, title, url, event.status.value, event.attempt, event.max_attempts,
                 event.bytes_received, event.expected_bytes, event.message, event.log_path,
                 now, now, finished),
            )

    def list_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._session() as db:
            rows = db.execute(
                """SELECT * FROM jobs
                   ORDER BY CASE WHEN status=? THEN 0 ELSE 1 END,
                            CASE WHEN status=? THEN queue_position END ASC,
                            updated_at DESC LIMIT ?""",
                (JobStatus.QUEUED.value, JobStatus.QUEUED.value, max(1, min(limit, 5000)))
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, workshop_id: str) -> dict[str, Any] | None:
        with self._session() as db:
            row = db.execute("SELECT * FROM jobs WHERE workshop_id=?", (workshop_id,)).fetchone()
        return dict(row) if row else None

    def pending_items(self) -> list[dict[str, Any]]:
        with self._session() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY queue_position ASC, created_at ASC, workshop_id ASC",
                (JobStatus.QUEUED.value,)
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_interrupted(self) -> int:
        """Requeue jobs left active by a crash or forced shutdown; never touch download files."""
        interrupted = (
            JobStatus.PREPARING.value,
            JobStatus.DOWNLOADING.value,
            JobStatus.RETRY_WAIT.value,
        )
        now = time.time()
        placeholders = ",".join("?" for _ in interrupted)
        with self._session() as db:
            cursor = db.execute(
                f"UPDATE jobs SET status=?, message=?, updated_at=?, finished_at=NULL "
                f"WHERE status IN ({placeholders})",
                (JobStatus.QUEUED.value,
                 "Recovered after the previous session ended; SteamCMD partial data was preserved.",
                 now, *interrupted),
            )
            return cursor.rowcount

    def delete(self, workshop_id: str) -> None:
        with self._session() as db:
            db.execute("DELETE FROM jobs WHERE workshop_id=?", (workshop_id,))

    def requeue(self, workshop_id: str) -> bool:
        with self._session() as db:
            cursor = db.execute(
                "UPDATE jobs SET status=?, message='Queued for retry', updated_at=?, finished_at=NULL WHERE workshop_id=? AND status NOT IN (?,?,?)",
                (JobStatus.QUEUED.value, time.time(), workshop_id, JobStatus.PREPARING.value,
                 JobStatus.DOWNLOADING.value, JobStatus.RETRY_WAIT.value),
            )
            return cursor.rowcount > 0

    def requeue_retryable(self) -> int:
        """Queue failed/cancelled jobs again without touching any SteamCMD files."""
        now = time.time()
        with self._session() as db:
            cursor = db.execute(
                "UPDATE jobs SET status=?, message=?, updated_at=?, finished_at=NULL WHERE status IN (?,?)",
                (
                    JobStatus.QUEUED.value,
                    "Queued again by Resume Old Jobs; existing partial/download files were preserved.",
                    now,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELLED.value,
                ),
            )
            return cursor.rowcount

    def move_queued(self, workshop_id: str, direction: int) -> bool:
        """Move a queued job one position up (-1) or down (+1) and persist the order."""
        if direction not in {-1, 1}:
            raise ValueError("direction must be -1 (up) or 1 (down)")
        with self._session() as db:
            rows = db.execute(
                "SELECT workshop_id, queue_position FROM jobs WHERE status=? "
                "ORDER BY queue_position ASC, created_at ASC, workshop_id ASC",
                (JobStatus.QUEUED.value,),
            ).fetchall()
            ids = [str(row["workshop_id"]) for row in rows]
            try:
                index = ids.index(workshop_id)
            except ValueError:
                return False
            neighbour = index + direction
            if neighbour < 0 or neighbour >= len(rows):
                return False
            current_position = int(rows[index]["queue_position"])
            neighbour_position = int(rows[neighbour]["queue_position"])
            db.execute(
                "UPDATE jobs SET queue_position=? WHERE workshop_id=? AND status=?",
                (neighbour_position, workshop_id, JobStatus.QUEUED.value),
            )
            db.execute(
                "UPDATE jobs SET queue_position=? WHERE workshop_id=? AND status=?",
                (current_position, ids[neighbour], JobStatus.QUEUED.value),
            )
            return True

    def clear_finished(self) -> int:
        with self._session() as db:
            cursor = db.execute(
                "DELETE FROM jobs WHERE status IN (?,?,?)",
                (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value),
            )
            return cursor.rowcount

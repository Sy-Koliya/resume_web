from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


APPLICATION_STATUSES = (
    "new",
    "reviewing",
    "interview",
    "offer",
    "rejected",
    "archived",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    reference_code TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL,
    cover_note TEXT NOT NULL DEFAULT '',
    consent_given INTEGER NOT NULL CHECK (consent_given IN (0, 1)),
    resume_original_name TEXT NOT NULL,
    resume_stored_path TEXT NOT NULL UNIQUE,
    resume_media_type TEXT NOT NULL,
    resume_size INTEGER NOT NULL,
    resume_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'reviewing', 'interview', 'offer', 'rejected', 'archived')),
    admin_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_applications_created_at
ON applications(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_applications_status_created_at
ON applications(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_applications_position_created_at
ON applications(position, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_evaluations_application_created
ON ai_evaluations(application_id, created_at DESC);

PRAGMA user_version = 1;
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA optimize")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_application(self, application: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO applications (
                    id, reference_code, full_name, email, phone, position,
                    cover_note, consent_given, resume_original_name,
                    resume_stored_path, resume_media_type, resume_size,
                    resume_sha256, status, admin_notes, created_at, updated_at
                ) VALUES (
                    :id, :reference_code, :full_name, :email, :phone, :position,
                    :cover_note, :consent_given, :resume_original_name,
                    :resume_stored_path, :resume_media_type, :resume_size,
                    :resume_sha256, 'new', '', :created_at, :updated_at
                )
                """,
                application,
            )

    def list_applications(
        self,
        *,
        query: str = "",
        status: str = "",
        position: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if query:
            clauses.append(
                "(full_name LIKE :query OR email LIKE :query OR reference_code LIKE :query)"
            )
            params["query"] = f"%{query}%"
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if position:
            clauses.append("position = :position")
            params["position"] = position

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params["limit"] = page_size
        params["offset"] = (page - 1) * page_size
        with self.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM applications {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM applications
                {where}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows], int(total)

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_application(self, application_id: str, status: str, notes: str) -> bool:
        if status not in APPLICATION_STATUSES:
            raise ValueError("Unknown application status")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE applications
                SET status = ?, admin_notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, notes, utc_now(), application_id),
            )
        return cursor.rowcount == 1

    def status_counts(self) -> dict[str, int]:
        result = {status: 0 for status in APPLICATION_STATUSES}
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM applications GROUP BY status"
            ).fetchall()
        for row in rows:
            result[str(row["status"])] = int(row["count"])
        result["total"] = sum(result.values())
        return result

    def add_ai_evaluation(
        self,
        application_id: str,
        provider: str,
        model: str,
        result: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_evaluations (
                    application_id, provider, model, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    provider,
                    model,
                    json.dumps(result, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def list_ai_evaluations(self, application_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ai_evaluations
                WHERE application_id = ?
                ORDER BY created_at DESC
                """,
                (application_id,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["result"] = json.loads(item.pop("result_json"))
            except json.JSONDecodeError:
                item["result"] = {"summary": "Stored evaluation could not be decoded."}
                item.pop("result_json", None)
            results.append(item)
        return results

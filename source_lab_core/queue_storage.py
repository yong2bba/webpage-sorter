"""Judgment queue storage for Source Lab (v0.3.4).

SQLite-backed storage for judgment requests with status management,
priority ordering, and duplicate handling.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .validation import validate_judgment_request

# ─── Status constants ──────────────────────────────────────────

PENDING = "pending"
IN_REVIEW = "in_review"
RESOLVED = "resolved"
CANCELLED = "cancelled"

_VALID_STATUSES = {PENDING, IN_REVIEW, RESOLVED, CANCELLED}

# allowed transitions: from_status → set of to_statuses
_TRANSITIONS = {
    PENDING: {IN_REVIEW, CANCELLED},
    IN_REVIEW: {RESOLVED, CANCELLED},
    RESOLVED: set(),
    CANCELLED: set(),
}

# priority sort order (lower = higher priority)
_PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "medium": 2, "low": 3}


# ─── Canonical URL helper ──────────────────────────────────────

def _canonical_url(payload: dict) -> str:
    """Extract canonical URL from payload (falls back to source_url)."""
    return payload.get("source_url", "")


# ─── Storage class ─────────────────────────────────────────────

class QueueStorage:
    """SQLite-backed judgment request queue."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS judgment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                canonical_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jr_status_priority
            ON judgment_requests(status, priority, created_at)
        """)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # ── Save ───────────────────────────────────────────────────

    def save(self, payload: dict) -> int:
        """Validate and save a judgment request. Returns row id.

        Raises ValueError on validation failure or unresolved duplicate.
        """
        errors = validate_judgment_request(payload)
        if errors:
            raise ValueError(f"validation failed: {errors}")

        canonical = _canonical_url(payload)

        # duplicate check: same canonical URL + unresolved status
        existing = self._conn.execute(
            "SELECT id FROM judgment_requests "
            "WHERE canonical_url = ? AND status NOT IN (?, ?)",
            (canonical, RESOLVED, CANCELLED),
        ).fetchone()
        if existing:
            raise ValueError(
                f"duplicate: unresolved request exists for {canonical}"
            )

        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "INSERT INTO judgment_requests "
            "(request_id, canonical_url, status, priority, payload_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload["request_id"],
                canonical,
                PENDING,
                payload.get("priority", "normal"),
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    # ── Get ────────────────────────────────────────────────────

    def get_by_id(self, row_id: int) -> Optional[Dict[str, Any]]:
        """Get a single request by its row id, or None."""
        row = self._conn.execute(
            "SELECT * FROM judgment_requests WHERE id = ?", (row_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ── List pending ───────────────────────────────────────────

    def list_pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List pending requests sorted by priority then FIFO."""
        rows = self._conn.execute(
            "SELECT * FROM judgment_requests WHERE status = ? ORDER BY created_at ASC",
            (PENDING,),
        ).fetchall()
        # Python-side priority sort (since priority is text, not int)
        result = [dict(r) for r in rows]
        result.sort(key=lambda r: _PRIORITY_ORDER.get(r["priority"], 99))
        return result[:limit]

    # ── Status transition ──────────────────────────────────────

    def transition(self, row_id: int, to_status: str) -> None:
        """Transition a request's status. Raises ValueError on invalid transition."""
        if to_status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: '{to_status}'. must be one of {_VALID_STATUSES}")

        row = self._conn.execute(
            "SELECT status FROM judgment_requests WHERE id = ?", (row_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"request not found: id={row_id}")

        from_status = row["status"]
        allowed = _TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise ValueError(
                f"invalid transition: {from_status} → {to_status}. "
                f"allowed from {from_status}: {allowed or 'none (terminal)'}"
            )

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE judgment_requests SET status = ?, updated_at = ? WHERE id = ?",
            (to_status, now, row_id),
        )
        self._conn.commit()

    # ── Get by request_id ─────────────────────────────────────

    def get_by_request_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get a single request by its request_id string, or None."""
        row = self._conn.execute(
            "SELECT * FROM judgment_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ── Save result ───────────────────────────────────────────

    def save_result(self, row_id: int, result_payload: dict, resolved_at: str) -> None:
        """Store result JSON and resolved_at timestamp on a request row."""
        self._conn.execute(
            "UPDATE judgment_requests SET result_json = ?, resolved_at = ?, updated_at = ? WHERE id = ?",
            (json.dumps(result_payload, ensure_ascii=False), resolved_at, resolved_at, row_id),
        )
        self._conn.commit()

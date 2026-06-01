"""PostgreSQL-backed judgment queue storage for SourceLab.

This mirrors the SQLite QueueStorage interface while writing into the v0
`sourcelab.*` operational schema. It is intentionally narrow: it covers the
existing queue operations first (save/list/get/transition/save_result), while
leaving the full collector flow wiring for the next step.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .queue_storage import (
    CANCELLED,
    IN_REVIEW,
    PENDING,
    RESOLVED,
    _PRIORITY_ORDER,
)
from .validation import validate_judgment_request

_VALID_STATUSES = {PENDING, IN_REVIEW, RESOLVED, CANCELLED}
_TRANSITIONS = {
    PENDING: {IN_REVIEW, CANCELLED},
    IN_REVIEW: {RESOLVED, CANCELLED},
    RESOLVED: set(),
    CANCELLED: set(),
}


def _canonical_url(payload: dict) -> str:
    """Extract canonical URL from payload, matching SQLite QueueStorage."""
    return payload.get("source_url", "")


def _canonical_key(url: str) -> str:
    """Build a stable canonical key for v0 queue-only writes."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = (parsed.path or "/").rstrip("/") or "/"
    if host == "github.com":
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return f"github:{parts[0]}/{parts[1]}"
    return f"url:{url}"


def _infer_source_type(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if host == "github.com":
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return "github_repo"
    return "website"


def _owner_repo(url: str) -> tuple[Optional[str], Optional[str]]:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None, None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


class PostgresQueueStorage:
    """PostgreSQL-backed SourceLab judgment request queue."""

    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("database_url is required")
        self._conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)

    def close(self) -> None:
        self._conn.close()

    def _ensure_source(self, payload: dict) -> str:
        canonical = _canonical_url(payload)
        owner, repo = _owner_repo(canonical)
        summary = payload.get("content_summary") or ""
        title = payload.get("title") or (repo if repo else None)
        metadata = {
            "queue_request_id": payload.get("request_id"),
            "branch_reason": payload.get("branch_reason"),
            "priority": payload.get("priority"),
        }
        with self._conn.cursor() as cur:
            row = cur.execute(
                """
                INSERT INTO sourcelab.sources (
                    source_type,
                    original_url,
                    canonical_url,
                    canonical_key,
                    title,
                    summary,
                    host,
                    owner_name,
                    repo_name,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (canonical_key) DO UPDATE SET
                    last_seen_at = now(),
                    seen_count = sourcelab.sources.seen_count + 1,
                    title = COALESCE(EXCLUDED.title, sourcelab.sources.title),
                    summary = COALESCE(NULLIF(EXCLUDED.summary, ''), sourcelab.sources.summary),
                    metadata = sourcelab.sources.metadata || EXCLUDED.metadata
                RETURNING id
                """,
                (
                    _infer_source_type(canonical),
                    canonical,
                    canonical,
                    _canonical_key(canonical),
                    title,
                    summary,
                    urlparse(canonical).netloc.lower(),
                    owner,
                    repo,
                    Jsonb(metadata),
                ),
            ).fetchone()
        return str(row["id"])

    def save(self, payload: dict) -> str:
        """Validate and save a judgment request. Returns UUID row id as string."""
        errors = validate_judgment_request(payload)
        if errors:
            raise ValueError(f"validation failed: {errors}")

        canonical = _canonical_url(payload)
        source_id = self._ensure_source(payload)
        now = datetime.now(timezone.utc)
        requested_at = payload.get("requested_at") or now.isoformat()

        try:
            with self._conn.transaction():
                with self._conn.cursor() as cur:
                    existing = cur.execute(
                        """
                        SELECT id FROM sourcelab.judgment_requests
                        WHERE canonical_url = %s AND status IN ('pending', 'in_review')
                        LIMIT 1
                        """,
                        (canonical,),
                    ).fetchone()
                    if existing:
                        raise ValueError(
                            f"duplicate: unresolved request exists for {canonical}"
                        )
                    row = cur.execute(
                        """
                        INSERT INTO sourcelab.judgment_requests (
                            request_id,
                            source_id,
                            canonical_url,
                            status,
                            priority,
                            branch_reason,
                            confidence,
                            content_summary,
                            payload_json,
                            requested_by,
                            requested_at
                        )
                        VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            payload["request_id"],
                            source_id,
                            canonical,
                            payload.get("priority", "normal"),
                            payload["branch_reason"],
                            payload["confidence"],
                            payload["content_summary"],
                            Jsonb(payload),
                            payload.get("requested_by", "source_lab"),
                            requested_at,
                        ),
                    ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f"duplicate: {canonical}") from exc
        return str(row["id"])

    def get_by_id(self, row_id: Any) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM sourcelab.judgment_requests WHERE id = %s", (row_id,)
        ).fetchone()
        return self._normalize_row(row)

    def get_by_request_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM sourcelab.judgment_requests WHERE request_id = %s",
            (request_id,),
        ).fetchone()
        return self._normalize_row(row)

    def list_pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM sourcelab.judgment_requests
            WHERE status = 'pending'
            ORDER BY requested_at ASC
            """
        ).fetchall()
        result = [self._normalize_row(r) for r in rows]
        result.sort(key=lambda r: _PRIORITY_ORDER.get(r["priority"], 99))
        return result[:limit]

    def transition(self, row_id: Any, to_status: str) -> None:
        if to_status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: '{to_status}'. must be one of {_VALID_STATUSES}")

        with self._conn.transaction():
            with self._conn.cursor() as cur:
                row = cur.execute(
                    "SELECT status FROM sourcelab.judgment_requests WHERE id = %s FOR UPDATE",
                    (row_id,),
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
                cur.execute(
                    "UPDATE sourcelab.judgment_requests SET status = %s WHERE id = %s",
                    (to_status, row_id),
                )

    def save_result(self, row_id: Any, result_payload: dict, resolved_at: str) -> None:
        resolved_value = resolved_at or datetime.now(timezone.utc).isoformat()
        with self._conn.transaction():
            self._conn.execute(
                """
                UPDATE sourcelab.judgment_requests
                SET result_json = %s, resolved_at = %s
                WHERE id = %s
                """,
                (Jsonb(result_payload), resolved_value, row_id),
            )

    @staticmethod
    def _normalize_row(row: Optional[dict]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        out: Dict[str, Any] = dict(row)
        for key, value in list(out.items()):
            if key == "id" or key.endswith("_id"):
                out[key] = str(value) if value is not None else None
            elif isinstance(value, datetime):
                out[key] = value.isoformat()
            elif isinstance(value, Decimal):
                out[key] = float(value)
            elif key in {"payload_json", "result_json"} and value is not None:
                # Keep plugin handler compatibility: it expects JSON strings here.
                out[key] = json.dumps(value, ensure_ascii=False)
            else:
                out[key] = value
        return out

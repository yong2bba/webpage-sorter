"""PostgreSQL full collector-flow repository for SourceLab.

This persists the collector spine in order:
source upsert → intake_event → artifact → analysis → branch_decision →
judgment_request, and extends queue result processing with final decisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

from .postgres_queue_storage import (
    PostgresQueueStorage,
    _canonical_key,
    _infer_source_type,
    _owner_repo,
)


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _to_plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(_to_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class PostgresCollectorFlowRepository(PostgresQueueStorage):
    """PostgreSQL-backed repository for the full SourceLab collector flow."""

    def record_intake_result(
        self,
        intake_result: Any,
        *,
        requested_by: str = "source_lab",
        submitted_via: str = "slack",
        request_id: Optional[str] = None,
        submitted_by: Optional[str] = None,
        slack_channel_id: Optional[str] = None,
        slack_channel_name: Optional[str] = None,
        slack_thread_ts: Optional[str] = None,
        slack_message_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist an IntakeResult through the v0 collector-flow tables.

        Returns IDs for every row created in the spine. The method is atomic: if
        any later step fails, earlier source/intake/artifact/analysis/branch rows
        are rolled back together.
        """
        result = _to_plain(intake_result)
        original_url = result.get("url") or ""
        canonical_url = result.get("canonical_url") or original_url
        state = result.get("state") or "error"
        analysis = result.get("analysis") or {}
        branch = result.get("branch_decision") or {}
        request_id = request_id or (branch.get("judgment_request_payload") or {}).get("request_id")

        with self._conn.transaction():
            source_id = self._upsert_source(original_url, canonical_url, analysis)
            intake_event_id = self._insert_intake_event(
                source_id=source_id,
                request_id=request_id,
                state=state,
                submitted_url=original_url,
                canonical_url=canonical_url,
                requested_by=requested_by,
                submitted_by=submitted_by,
                submitted_via=submitted_via,
                slack_channel_id=slack_channel_id,
                slack_channel_name=slack_channel_name,
                slack_thread_ts=slack_thread_ts,
                slack_message_ts=slack_message_ts,
                payload=result,
            )

            artifact_id = None
            analysis_id = None
            branch_decision_id = None
            judgment_request_id = None
            judgment_request_request_id = None

            if analysis:
                artifact_id = self._insert_analysis_artifact(source_id, intake_event_id, analysis, request_id)
                analysis_id = self._insert_analysis(source_id, intake_event_id, artifact_id, analysis)

            if branch:
                branch_decision_id = self._insert_branch_decision(
                    source_id, intake_event_id, analysis_id, branch
                )
                if branch.get("state") == "judgment_requested" and branch.get("judgment_request_payload"):
                    judgment_request_id, judgment_request_request_id = self._insert_judgment_request(
                        source_id=source_id,
                        intake_event_id=intake_event_id,
                        analysis_id=analysis_id,
                        branch_decision_id=branch_decision_id,
                        payload=branch["judgment_request_payload"],
                        branch=branch,
                        requested_by=requested_by,
                    )

            return {
                "source_id": source_id,
                "intake_event_id": intake_event_id,
                "artifact_id": artifact_id,
                "analysis_id": analysis_id,
                "branch_decision_id": branch_decision_id,
                "judgment_request_id": judgment_request_id,
                "judgment_request_request_id": judgment_request_request_id,
                "state": state,
                "canonical_url": canonical_url,
            }

    def save_result(self, row_id: Any, result_payload: dict, resolved_at: str) -> None:
        """Persist queue result JSON and append a durable final decision row."""
        super().save_result(row_id, result_payload, resolved_at)
        row = self.get_by_id(row_id)
        if row is None:
            return
        source_id = row.get("source_id")
        if not source_id:
            return
        with self._conn.transaction():
            existing = self._conn.execute(
                """
                SELECT id FROM sourcelab.decisions
                WHERE judgment_request_id = %s
                  AND decision = %s
                  AND action = %s
                LIMIT 1
                """,
                (row_id, result_payload.get("judgment", ""), result_payload.get("action", "")),
            ).fetchone()
            if existing:
                return
            self._conn.execute(
                """
                INSERT INTO sourcelab.decisions (
                    source_id,
                    judgment_request_id,
                    decision,
                    action,
                    confidence,
                    reason,
                    decided_by,
                    decided_at,
                    result_payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_id,
                    row_id,
                    result_payload.get("judgment", ""),
                    result_payload.get("action", ""),
                    result_payload.get("confidence"),
                    result_payload.get("reason", ""),
                    result_payload.get("decided_by", "yongyongbot"),
                    result_payload.get("decided_at") or resolved_at or datetime.now(timezone.utc).isoformat(),
                    Jsonb(result_payload),
                ),
            )

    def get_latest_source_state(self, url: str) -> Optional[Dict[str, Any]]:
        canonical_key = _canonical_key(url)
        row = self._conn.execute(
            "SELECT * FROM sourcelab.v_latest_source_state WHERE canonical_key = %s",
            (canonical_key,),
        ).fetchone()
        return self._normalize_row(row)

    def get_wiki_projection(self, source_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            """
            SELECT * FROM sourcelab.wiki_projections
            WHERE source_id = %s AND projection_kind = 'source_report'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        return self._normalize_row(row)

    def _upsert_source(self, original_url: str, canonical_url: str, analysis: dict) -> str:
        owner, repo = _owner_repo(canonical_url)
        parsed = urlparse(canonical_url)
        title = analysis.get("title") or (repo if repo else None)
        summary = analysis.get("summary") or ""
        metadata = {
            "content_type": analysis.get("content_type"),
            "signals": analysis.get("signals") or [],
            "risk_flags": analysis.get("risk_flags") or [],
        }
        row = self._conn.execute(
            """
            INSERT INTO sourcelab.sources (
                source_type,
                original_url,
                canonical_url,
                canonical_key,
                title,
                summary,
                language,
                license,
                primary_author,
                host,
                owner_name,
                repo_name,
                external_id,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (canonical_key) DO UPDATE SET
                last_seen_at = now(),
                seen_count = sourcelab.sources.seen_count + 1,
                title = COALESCE(EXCLUDED.title, sourcelab.sources.title),
                summary = COALESCE(NULLIF(EXCLUDED.summary, ''), sourcelab.sources.summary),
                language = COALESCE(EXCLUDED.language, sourcelab.sources.language),
                license = COALESCE(EXCLUDED.license, sourcelab.sources.license),
                primary_author = COALESCE(EXCLUDED.primary_author, sourcelab.sources.primary_author),
                metadata = sourcelab.sources.metadata || EXCLUDED.metadata
            RETURNING id
            """,
            (
                _infer_source_type(canonical_url),
                original_url,
                canonical_url,
                _canonical_key(canonical_url),
                title,
                summary,
                analysis.get("language"),
                analysis.get("license"),
                analysis.get("primary_author") or analysis.get("author"),
                parsed.netloc.lower(),
                owner,
                repo,
                analysis.get("external_id"),
                Jsonb(metadata),
            ),
        ).fetchone()
        return str(row["id"])

    def _insert_intake_event(self, **values: Any) -> str:
        row = self._conn.execute(
            """
            INSERT INTO sourcelab.intake_events (
                source_id,
                request_id,
                state,
                submitted_url,
                canonical_url,
                submitted_by,
                submitted_via,
                slack_channel_id,
                slack_channel_name,
                slack_thread_ts,
                slack_message_ts,
                requested_by,
                payload,
                completed_at
            )
            VALUES (%(source_id)s, %(request_id)s, %(state)s, %(submitted_url)s,
                    %(canonical_url)s, %(submitted_by)s, %(submitted_via)s,
                    %(slack_channel_id)s, %(slack_channel_name)s,
                    %(slack_thread_ts)s, %(slack_message_ts)s, %(requested_by)s,
                    %(payload)s, now())
            RETURNING id
            """,
            {**values, "payload": Jsonb(values["payload"])},
        ).fetchone()
        return str(row["id"])

    def _insert_analysis_artifact(
        self, source_id: str, intake_event_id: str, analysis: dict, request_id: Optional[str]
    ) -> str:
        data = _json_bytes(analysis)
        digest = hashlib.sha256(data).hexdigest()
        storage_uri = f"db://sourcelab/intake/{request_id or digest}/analysis.json"
        row = self._conn.execute(
            """
            INSERT INTO sourcelab.artifacts (
                source_id,
                intake_event_id,
                kind,
                storage_uri,
                content_type,
                byte_size,
                sha256,
                title,
                description,
                is_public,
                metadata
            )
            VALUES (%s, %s, 'analysis_json', %s, 'application/json', %s, %s, %s, %s, false, %s)
            RETURNING id
            """,
            (
                source_id,
                intake_event_id,
                storage_uri,
                len(data),
                digest,
                analysis.get("title"),
                "Low-model analysis JSON snapshot stored inline in PostgreSQL metadata.",
                Jsonb({"analysis": analysis, "persisted_inline": True}),
            ),
        ).fetchone()
        return str(row["id"])

    def _insert_analysis(
        self, source_id: str, intake_event_id: str, artifact_id: str, analysis: dict
    ) -> str:
        row = self._conn.execute(
            """
            INSERT INTO sourcelab.analyses (
                source_id,
                intake_event_id,
                analyzer_name,
                model_provider,
                model_name,
                prompt_version,
                content_type,
                confidence,
                summary,
                signals,
                risk_flags,
                evidence,
                key_claims,
                extracted_entities,
                raw_text_preview,
                analysis_json,
                artifact_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                source_id,
                intake_event_id,
                analysis.get("analyzer_name") or "source_lab_low_analysis",
                analysis.get("model_provider"),
                analysis.get("model_name"),
                analysis.get("prompt_version"),
                analysis.get("content_type") or "other",
                analysis.get("confidence", 0.0),
                analysis.get("summary") or "No summary provided.",
                analysis.get("signals") or [],
                analysis.get("risk_flags") or [],
                analysis.get("evidence") or [],
                Jsonb(analysis.get("key_claims") or []),
                Jsonb(analysis.get("extracted_entities") or []),
                analysis.get("raw_text_preview"),
                Jsonb(analysis),
                artifact_id,
            ),
        ).fetchone()
        return str(row["id"])

    def _insert_branch_decision(
        self,
        source_id: str,
        intake_event_id: str,
        analysis_id: Optional[str],
        branch: dict,
    ) -> str:
        row = self._conn.execute(
            """
            INSERT INTO sourcelab.branch_decisions (
                source_id,
                intake_event_id,
                analysis_id,
                state,
                branch_reason,
                priority,
                confidence,
                reason,
                evidence,
                decision_payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                source_id,
                intake_event_id,
                analysis_id,
                branch.get("state") or "error",
                branch.get("branch_reason"),
                branch.get("priority") or "low",
                branch.get("confidence", 0.0),
                branch.get("reason") or "No branch reason provided.",
                branch.get("evidence") or [],
                Jsonb(branch),
            ),
        ).fetchone()
        return str(row["id"])

    def _insert_judgment_request(
        self,
        *,
        source_id: str,
        intake_event_id: str,
        analysis_id: Optional[str],
        branch_decision_id: str,
        payload: dict,
        branch: dict,
        requested_by: str,
    ) -> tuple[str, str]:
        requested_at = payload.get("requested_at") or datetime.now(timezone.utc).isoformat()
        row = self._conn.execute(
            """
            INSERT INTO sourcelab.judgment_requests (
                request_id,
                source_id,
                intake_event_id,
                analysis_id,
                branch_decision_id,
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
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, request_id
            """,
            (
                payload["request_id"],
                source_id,
                intake_event_id,
                analysis_id,
                branch_decision_id,
                payload["source_url"],
                payload.get("priority", branch.get("priority") or "normal"),
                payload["branch_reason"],
                payload["confidence"],
                payload["content_summary"],
                Jsonb(payload),
                payload.get("requested_by") or requested_by,
                requested_at,
            ),
        ).fetchone()
        return str(row["id"]), str(row["request_id"])

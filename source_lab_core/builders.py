"""Payload builders for Source Lab judgment request/result (v0.3.1)."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Judgment Request Builder ──────────────────────────────────

def build_judgment_request(
    source_url: str,
    content_summary: str,
    branch_reason: str,
    confidence: float,
    priority: str,
    requested_by: str,
    analysis_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a valid judgment request payload.

    Auto-generates request_id (uuid4) and requested_at (UTC ISO 8601).
    """
    payload: Dict[str, Any] = {
        "request_id": str(uuid.uuid4()),
        "source_url": source_url,
        "content_summary": content_summary,
        "branch_reason": branch_reason,
        "confidence": confidence,
        "priority": priority,
        "requested_at": _now_iso(),
        "requested_by": requested_by,
    }
    if analysis_snapshot is not None:
        payload["analysis_snapshot"] = analysis_snapshot
    return payload


# ─── Judgment Result Builder ───────────────────────────────────

def build_judgment_result(
    request_id: str,
    judgment: str,
    reason: str,
    confidence: float,
    action: str,
    decided_by: str,
    evidence: Optional[List[str]] = None,
    followup_tasks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a valid judgment result payload.

    Auto-generates decided_at (UTC ISO 8601).
    """
    payload: Dict[str, Any] = {
        "request_id": request_id,
        "judgment": judgment,
        "reason": reason,
        "confidence": confidence,
        "action": action,
        "decided_at": _now_iso(),
        "decided_by": decided_by,
    }
    if evidence is not None:
        payload["evidence"] = evidence
    if followup_tasks is not None:
        payload["followup_tasks"] = followup_tasks
    return payload

"""Branching logic for Source Lab pipeline (v0.3.2).

Decides whether a low-level analysis result should self-close
or request judgment from 용용봇, based on the v0.3.0 contract.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .builders import build_judgment_request
from .contracts import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    BranchReason,
    Priority,
)


# ─── Analysis input model ─────────────────────────────────────

# Known signal → (branch_reason, priority) mapping
_SIGNAL_MAP: Dict[str, tuple] = {
    "investment_advice": (BranchReason.INVESTMENT_JUDGMENT, Priority.HIGH),
    "financial_forecast": (BranchReason.INVESTMENT_JUDGMENT, Priority.HIGH),
    "legal_interpretation": (BranchReason.REGULATORY_INTERPRETATION, Priority.HIGH),
    "regulatory_compliance": (BranchReason.REGULATORY_INTERPRETATION, Priority.HIGH),
    "personal_data": (BranchReason.PERSONAL_DATA_SENSITIVITY, Priority.HIGH),
    "medical_advice": (BranchReason.REGULATORY_INTERPRETATION, Priority.HIGH),
    "health_recommendation": (BranchReason.REGULATORY_INTERPRETATION, Priority.HIGH),
}

# Known risk_flag → branch_reason mapping
_RISK_FLAG_MAP: Dict[str, BranchReason] = {
    "contradicts_known_facts": BranchReason.CONFLICTING_INFORMATION,
    "unverified_claim": BranchReason.CONFLICTING_INFORMATION,
    "conflicting_sources": BranchReason.CONFLICTING_INFORMATION,
    "bias_detected": BranchReason.CONFLICTING_INFORMATION,
    "insufficient_source": BranchReason.LOW_CONFIDENCE,
}

# Content types that are always judgment-required (forbidden for low-model)
_FORBIDDEN_CONTENT_TYPES = {"financial"}


# ─── Output model ──────────────────────────────────────────────

@dataclass
class BranchDecision:
    """Result of the branching decision."""
    state: str                                    # "self_close" | "judgment_requested"
    branch_reason: Optional[str] = None           # BranchReason value or None
    priority: str = "low"                         # Priority value
    confidence: float = 0.0
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    judgment_request_payload: Optional[Dict[str, Any]] = None


# ─── Core decision function ───────────────────────────────────

def decide_branch(
    analysis: dict,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    requested_by: str = "source_lab",
) -> BranchDecision:
    """Decide self-close vs judgment-request based on analysis result.

    Args:
        analysis: dict with keys url, content_type, confidence,
                  signals, risk_flags, evidence, summary.
        confidence_threshold: override for DEFAULT_CONFIDENCE_THRESHOLD.
        requested_by: agent_id for the judgment request payload.

    Returns:
        BranchDecision with state, reason, and optional payload.
    """
    url = analysis.get("url", "")
    content_type = analysis.get("content_type", "other")
    confidence = analysis.get("confidence", 0.0)
    signals: List[str] = analysis.get("signals", []) or []
    risk_flags: List[str] = analysis.get("risk_flags", []) or []
    evidence: List[str] = analysis.get("evidence") or []
    summary = analysis.get("summary", "")

    # ── Check 1: Forbidden content types (always judgment) ─────
    if content_type in _FORBIDDEN_CONTENT_TYPES:
        return _make_judgment(
            url=url,
            content_type=content_type,
            summary=summary,
            confidence=confidence,
            branch_reason=BranchReason.INVESTMENT_JUDGMENT,
            priority=Priority.HIGH,
            evidence=evidence,
            reason=f"Forbidden content type: {content_type}",
            requested_by=requested_by,
        )

    # ── Check 2: Forbidden signals ─────────────────────────────
    for sig in signals:
        if sig in _SIGNAL_MAP:
            reason_enum, priority = _SIGNAL_MAP[sig]
            return _make_judgment(
                url=url,
                content_type=content_type,
                summary=summary,
                confidence=confidence,
                branch_reason=reason_enum,
                priority=priority,
                evidence=evidence,
                reason=f"Forbidden signal: {sig}",
                requested_by=requested_by,
            )

    # ── Check 3: Risk flags ────────────────────────────────────
    if risk_flags:
        # Determine reason from first matching risk flag
        branch_reason = BranchReason.CONFLICTING_INFORMATION
        for flag in risk_flags:
            if flag in _RISK_FLAG_MAP:
                branch_reason = _RISK_FLAG_MAP[flag]
                break
        return _make_judgment(
            url=url,
            content_type=content_type,
            summary=summary,
            confidence=confidence,
            branch_reason=branch_reason,
            priority=Priority.MEDIUM,
            evidence=evidence,
            reason=f"Risk flags present: {risk_flags}",
            requested_by=requested_by,
        )

    # ── Check 4: Insufficient evidence ─────────────────────────
    if not evidence:
        return _make_judgment(
            url=url,
            content_type=content_type,
            summary=summary,
            confidence=confidence,
            branch_reason=BranchReason.LOW_CONFIDENCE,
            priority=Priority.MEDIUM,
            evidence=[],
            reason="Insufficient evidence",
            requested_by=requested_by,
        )

    # ── Check 5: Low confidence ────────────────────────────────
    if confidence < confidence_threshold:
        return _make_judgment(
            url=url,
            content_type=content_type,
            summary=summary,
            confidence=confidence,
            branch_reason=BranchReason.LOW_CONFIDENCE,
            priority=Priority.MEDIUM,
            evidence=evidence,
            reason=f"Confidence {confidence} below threshold {confidence_threshold}",
            requested_by=requested_by,
            confidence_threshold=confidence_threshold,
        )

    # ── Default: Self-close ────────────────────────────────────
    return BranchDecision(
        state="self_close",
        branch_reason=None,
        priority="low",
        confidence=confidence,
        reason="High confidence, low risk, sufficient evidence",
        evidence=evidence,
        judgment_request_payload=None,
    )


# ─── Helper ────────────────────────────────────────────────────

def _make_judgment(
    *,
    url: str,
    content_type: str,
    summary: str,
    confidence: float,
    branch_reason: BranchReason,
    priority: Priority,
    evidence: List[str],
    reason: str,
    requested_by: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> BranchDecision:
    """Build a judgment-requested BranchDecision with a valid payload."""
    payload = build_judgment_request(
        source_url=url,
        content_summary=summary[:500],
        branch_reason=branch_reason.value,
        confidence=confidence,
        priority=priority.name.lower(),
        requested_by=requested_by,
        analysis_snapshot={
            "title": "",
            "content_type": content_type,
            "key_claims": [],
            "extracted_entities": [],
            "raw_text_preview": summary[:2000],
        },
    )
    return BranchDecision(
        state="judgment_requested",
        branch_reason=branch_reason.value,
        priority=priority.name.lower(),
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        judgment_request_payload=payload,
    )

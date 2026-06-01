"""Validation logic for Source Lab payloads (v0.3.1)."""

from typing import List

from .contracts import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    REQUEST_KNOWN_FIELDS,
    RESULT_KNOWN_FIELDS,
    Action,
    BranchReason,
    Judgment,
    Priority,
    is_known_content_type,
)


# ─── Request validation ────────────────────────────────────────

_REQUEST_REQUIRED = {
    "request_id", "source_url", "content_summary",
    "branch_reason", "confidence", "priority",
    "requested_at", "requested_by",
}


def validate_judgment_request(
    payload: dict,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> List[str]:
    """Return list of error strings. Empty = valid.

    Confidence threshold validation only applies when
    branch_reason == "low_confidence" — to catch contradictory
    claims (saying confidence is low but reporting a high value).
    """
    errors: List[str] = []

    # required fields
    for field in _REQUEST_REQUIRED:
        if field not in payload:
            errors.append(f"missing required field: {field}")

    # enum checks
    if "branch_reason" in payload:
        try:
            BranchReason(payload["branch_reason"])
        except ValueError:
            valid = [r.value for r in BranchReason]
            errors.append(
                f"invalid branch_reason: '{payload['branch_reason']}'. "
                f"must be one of {valid}"
            )

    if "priority" in payload:
        try:
            Priority.from_string(payload["priority"])
        except ValueError:
            valid = [p.name.lower() for p in Priority]
            errors.append(
                f"invalid priority: '{payload['priority']}'. "
                f"must be one of {valid}"
            )

    # confidence range check (always)
    if "confidence" in payload:
        conf = payload["confidence"]
        if not (0.0 <= conf <= 1.0):
            errors.append(
                f"confidence out of range: {conf}. must be 0.0~1.0"
            )

    # confidence threshold check (only when branch_reason == low_confidence)
    # low_confidence means the CONTENT has low confidence, so the reported
    # confidence must be BELOW threshold. If confidence >= threshold, it
    # contradicts the low_confidence claim.
    if "confidence" in payload and "branch_reason" in payload:
        if payload["branch_reason"] == BranchReason.LOW_CONFIDENCE.value:
            conf = payload["confidence"]
            if conf >= confidence_threshold:
                errors.append(
                    f"confidence {conf} >= threshold {confidence_threshold} "
                    f"contradicts low_confidence branch_reason"
                )

    # unknown root fields
    unknown = set(payload.keys()) - REQUEST_KNOWN_FIELDS
    if unknown:
        errors.append(f"unknown fields: {sorted(unknown)}")

    return errors


# ─── Result validation ─────────────────────────────────────────

_RESULT_REQUIRED = {
    "request_id", "judgment", "reason", "confidence",
    "action", "decided_at", "decided_by",
}


def validate_judgment_result(
    payload: dict,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> List[str]:
    """Return list of error strings. Empty = valid."""
    errors: List[str] = []

    # required fields
    for field in _RESULT_REQUIRED:
        if field not in payload:
            errors.append(f"missing required field: {field}")

    # enum checks
    if "judgment" in payload:
        try:
            Judgment(payload["judgment"])
        except ValueError:
            valid = [j.value for j in Judgment]
            errors.append(
                f"invalid judgment: '{payload['judgment']}'. "
                f"must be one of {valid}"
            )

    if "action" in payload:
        try:
            Action(payload["action"])
        except ValueError:
            valid = [a.value for a in Action]
            errors.append(
                f"invalid action: '{payload['action']}'. "
                f"must be one of {valid}"
            )

    # confidence range
    if "confidence" in payload:
        conf = payload["confidence"]
        if not (0.0 <= conf <= 1.0):
            errors.append(
                f"confidence out of range: {conf}. must be 0.0~1.0"
            )

    # unknown root fields
    unknown = set(payload.keys()) - RESULT_KNOWN_FIELDS
    if unknown:
        errors.append(f"unknown fields: {sorted(unknown)}")

    return errors


# ─── Content type (warning-level, not error) ───────────────────

def validate_content_type(content_type: str) -> List[str]:
    """Return list of warning strings. Empty = known type."""
    warnings: List[str] = []
    if not is_known_content_type(content_type):
        warnings.append(
            f"unknown content_type: '{content_type}'. "
            f"known types: news, opinion, technical, financial, other"
        )
    return warnings

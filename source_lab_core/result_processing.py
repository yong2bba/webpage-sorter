"""Judgment result processing for Source Lab (v0.3.5).

Processes judgment results from 용용봇, validates them,
transitions queue status, and determines outcomes.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .queue_storage import (
    CANCELLED,
    IN_REVIEW,
    PENDING,
    RESOLVED,
    QueueStorage,
)
from .validation import validate_judgment_result

# ─── Action → Outcome mapping ──────────────────────────────────

_ACTION_OUTCOME = {
    "close": "final_close",
    "queue_followup": "queued_followup",
    "reanalyze": "reanalyze",
    "escalate_to_human": "escalated",
    "archive": "archived",
}


# ─── Outcome model ─────────────────────────────────────────────

@dataclass
class ProcessingOutcome:
    """Result of processing a judgment result."""
    request_id: str = ""
    status: str = ""
    outcome: str = ""
    action: str = ""
    judgment: str = ""
    reason: str = ""
    errors: List[str] = field(default_factory=list)


# ─── Processor ─────────────────────────────────────────────────

class ResultProcessor:
    """Processes judgment results against the queue storage."""

    def __init__(self, storage: QueueStorage):
        self._storage = storage

    def process(self, result_payload: dict) -> ProcessingOutcome:
        """Validate and process a judgment result.

        Steps:
        1. Validate result payload
        2. Look up request by request_id
        3. Check request is not terminal
        4. Transition status to resolved
        5. Persist result JSON
        6. Return outcome

        Args:
            result_payload: Judgment result dict.

        Returns:
            ProcessingOutcome with status, outcome, errors.
        """
        request_id = result_payload.get("request_id", "")

        # 1. Validate
        errors = validate_judgment_result(result_payload)
        if errors:
            return ProcessingOutcome(
                request_id=request_id,
                errors=[f"validation failed: {errors}"],
            )

        # 2. Look up request
        row = self._storage.get_by_request_id(request_id)
        if row is None:
            return ProcessingOutcome(
                request_id=request_id,
                errors=[f"request not found: {request_id}"],
            )

        # 3. Check terminal
        current_status = row["status"]
        if current_status in (RESOLVED, CANCELLED):
            return ProcessingOutcome(
                request_id=request_id,
                status=current_status,
                errors=[f"request is in terminal status: {current_status}"],
            )

        # 4. Transition to resolved (pending → in_review → resolved)
        if current_status == PENDING:
            self._storage.transition(row["id"], IN_REVIEW)
        self._storage.transition(row["id"], RESOLVED)

        # 5. Persist result
        resolved_at = result_payload.get("decided_at", "")
        self._storage.save_result(row["id"], result_payload, resolved_at)

        # 6. Outcome
        action = result_payload.get("action", "")
        outcome = _ACTION_OUTCOME.get(action) or action

        return ProcessingOutcome(
            request_id=request_id,
            status=RESOLVED,
            outcome=outcome,
            action=action,
            judgment=result_payload.get("judgment", ""),
            reason=result_payload.get("reason", ""),
        )

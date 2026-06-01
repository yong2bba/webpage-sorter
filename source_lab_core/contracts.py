"""Contract enums and constants for Source Lab pipeline (v0.3.0)."""

from enum import Enum, IntEnum
from typing import Set


# ─── Confidence ────────────────────────────────────────────────

DEFAULT_CONFIDENCE_THRESHOLD = 0.8


# ─── Pipeline States ───────────────────────────────────────────

class PipelineState(str, Enum):
    URL_INTAKE = "url_intake"
    LOW_LEVEL_ANALYSIS = "low_level_analysis"
    SELF_CLOSE = "self_close"
    JUDGMENT_REQUESTED = "judgment_requested"
    JUDGMENT_RESULT_RECEIVED = "judgment_result_received"
    FINAL_CLOSE = "final_close"
    QUEUED_FOLLOWUP = "queued_followup"

    @classmethod
    def terminal_states(cls) -> Set["PipelineState"]:
        return {cls.SELF_CLOSE, cls.FINAL_CLOSE, cls.QUEUED_FOLLOWUP}


# ─── Branch Reasons ────────────────────────────────────────────

class BranchReason(str, Enum):
    INVESTMENT_JUDGMENT = "investment_judgment"
    REGULATORY_INTERPRETATION = "regulatory_interpretation"
    PERSONAL_DATA_SENSITIVITY = "personal_data_sensitivity"
    CONFLICTING_INFORMATION = "conflicting_information"
    LOW_CONFIDENCE = "low_confidence"
    EXPLICIT_YONGYONGBOT_REQUEST = "explicit_yongyongbot_request"


# ─── Priority (ordered: lower int value = higher priority) ─────

class Priority(IntEnum):
    URGENT = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3

    @classmethod
    def from_string(cls, value: str) -> "Priority":
        """Look up by lowercase name (e.g. 'high' → HIGH)."""
        try:
            return cls[value.upper()]
        except KeyError:
            raise ValueError(
                f"invalid priority: '{value}'. "
                f"must be one of {[p.name.lower() for p in cls]}"
            )


# ─── Judgment ──────────────────────────────────────────────────

class Judgment(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    ESCALATED = "escalated"
    DEFERRED = "deferred"


# ─── Action ────────────────────────────────────────────────────

class Action(str, Enum):
    CLOSE = "close"
    REANALYZE = "reanalyze"
    QUEUE_FOLLOWUP = "queue_followup"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    ARCHIVE = "archive"


# ─── Content Type (extensible policy) ─────────────────────────

_KNOWN_CONTENT_TYPES = {"news", "opinion", "technical", "financial", "other"}


class ContentType(str, Enum):
    NEWS = "news"
    OPINION = "opinion"
    TECHNICAL = "technical"
    FINANCIAL = "financial"
    OTHER = "other"


def is_known_content_type(value: str) -> bool:
    """Return True if value is in the known content-type set."""
    return value in _KNOWN_CONTENT_TYPES


# ─── Known field sets for unknown-field rejection ─────────────

REQUEST_KNOWN_FIELDS = {
    "request_id", "source_url", "content_summary",
    "analysis_snapshot", "branch_reason", "confidence",
    "priority", "requested_at", "requested_by",
}

RESULT_KNOWN_FIELDS = {
    "request_id", "judgment", "reason", "confidence",
    "evidence", "action", "followup_tasks",
    "decided_at", "decided_by",
}

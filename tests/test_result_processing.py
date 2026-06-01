"""Tests for source_lab result processing (v0.3.5)."""

import json
import sqlite3
from pathlib import Path

import pytest


# ─── Helpers ───────────────────────────────────────────────────

def _make_request(
    source_url="https://example.com/article",
    branch_reason="investment_judgment",
    confidence=0.5,
    priority="high",
    request_id="test-001",
) -> dict:
    return {
        "request_id": request_id,
        "source_url": source_url,
        "content_summary": "Test summary",
        "branch_reason": branch_reason,
        "confidence": confidence,
        "priority": priority,
        "requested_at": "2026-05-27T00:00:00Z",
        "requested_by": "coder01",
    }


def _make_result(
    request_id="test-001",
    judgment="approved",
    reason="Looks correct",
    confidence=0.9,
    action="close",
) -> dict:
    return {
        "request_id": request_id,
        "judgment": judgment,
        "reason": reason,
        "confidence": confidence,
        "action": action,
        "decided_at": "2026-05-27T01:00:00Z",
        "decided_by": "yongyongbot",
    }


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_result.db")


@pytest.fixture
def storage(db_path):
    from source_lab_core.queue_storage import QueueStorage
    return QueueStorage(db_path)


@pytest.fixture
def processor(storage):
    from source_lab_core.result_processing import ResultProcessor
    return ResultProcessor(storage)


# ─── Valid result resolves pending ─────────────────────────────

class TestValidResultResolves:
    def test_pending_to_resolved(self, storage, processor):
        row_id = storage.save(_make_request())
        result = _make_result()
        outcome = processor.process(result)
        assert outcome.errors == []
        assert outcome.status == "resolved"
        row = storage.get_by_id(row_id)
        assert row["status"] == "resolved"

    def test_in_review_to_resolved(self, storage, processor):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "in_review")
        result = _make_result()
        outcome = processor.process(result)
        assert outcome.errors == []
        assert outcome.status == "resolved"


# ─── Invalid result ────────────────────────────────────────────

class TestInvalidResult:
    def test_invalid_result_no_status_change(self, storage, processor):
        row_id = storage.save(_make_request())
        bad_result = {"request_id": "test-001"}  # missing required fields
        outcome = processor.process(bad_result)
        assert outcome.errors
        row = storage.get_by_id(row_id)
        assert row["status"] == "pending"  # unchanged


# ─── Missing request ───────────────────────────────────────────

class TestMissingRequest:
    def test_missing_request_id_fails(self, storage, processor):
        result = _make_result(request_id="nonexistent")
        outcome = processor.process(result)
        assert outcome.errors
        assert "not found" in outcome.errors[0].lower()


# ─── Terminal request ──────────────────────────────────────────

class TestTerminalRequest:
    def test_resolved_request_fails(self, storage, processor):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "in_review")
        storage.transition(row_id, "resolved")
        result = _make_result()
        outcome = processor.process(result)
        assert outcome.errors
        assert "terminal" in outcome.errors[0].lower() or "resolved" in outcome.errors[0].lower()

    def test_cancelled_request_fails(self, storage, processor):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "cancelled")
        result = _make_result()
        outcome = processor.process(result)
        assert outcome.errors
        assert "terminal" in outcome.errors[0].lower() or "cancelled" in outcome.errors[0].lower()


# ─── Action → Outcome mapping ──────────────────────────────────

class TestActionOutcomeMapping:
    def test_close_action_gives_final_close(self, storage, processor):
        storage.save(_make_request())
        result = _make_result(action="close")
        outcome = processor.process(result)
        assert outcome.outcome == "final_close"

    def test_queue_followup_action_gives_queued_followup(self, storage, processor):
        storage.save(_make_request())
        result = _make_result(action="queue_followup")
        outcome = processor.process(result)
        assert outcome.outcome == "queued_followup"

    def test_reanalyze_action_gives_reanalyze(self, storage, processor):
        storage.save(_make_request())
        result = _make_result(action="reanalyze")
        outcome = processor.process(result)
        assert outcome.outcome == "reanalyze"

    def test_escalate_to_human_action_gives_escalated(self, storage, processor):
        storage.save(_make_request())
        result = _make_result(action="escalate_to_human")
        outcome = processor.process(result)
        assert outcome.outcome == "escalated"

    def test_archive_action_gives_archived(self, storage, processor):
        storage.save(_make_request())
        result = _make_result(action="archive")
        outcome = processor.process(result)
        assert outcome.outcome == "archived"


# ─── Result persistence ────────────────────────────────────────

class TestResultPersistence:
    def test_result_json_roundtrip(self, storage, processor):
        storage.save(_make_request())
        result = _make_result()
        processor.process(result)
        row = storage.get_by_request_id("test-001")
        stored = json.loads(row["result_json"])
        assert stored["judgment"] == "approved"
        assert stored["confidence"] == 0.9

    def test_resolved_at_set(self, storage, processor):
        storage.save(_make_request())
        result = _make_result()
        processor.process(result)
        row = storage.get_by_request_id("test-001")
        assert row["resolved_at"] is not None


# ─── Outcome structure ─────────────────────────────────────────

class TestOutcomeStructure:
    def test_has_required_fields(self, storage, processor):
        storage.save(_make_request())
        result = _make_result()
        outcome = processor.process(result)
        assert hasattr(outcome, "request_id")
        assert hasattr(outcome, "status")
        assert hasattr(outcome, "outcome")
        assert hasattr(outcome, "action")
        assert hasattr(outcome, "judgment")
        assert hasattr(outcome, "reason")
        assert hasattr(outcome, "errors")

"""Tests for source_lab queue storage (v0.3.4)."""

import json
import sqlite3
import tempfile
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


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_queue.db")


@pytest.fixture
def storage(db_path):
    from source_lab_core.queue_storage import QueueStorage
    return QueueStorage(db_path)


# ─── Storage init ──────────────────────────────────────────────

class TestStorageInit:
    def test_creates_tables(self, storage):
        conn = storage._conn
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "judgment_requests" in tables

    def test_init_is_idempotent(self, db_path):
        from source_lab_core.queue_storage import QueueStorage
        s1 = QueueStorage(db_path)
        s2 = QueueStorage(db_path)
        s1.close()
        s2.close()


# ─── Save valid request ───────────────────────────────────────

class TestSaveValidRequest:
    def test_save_and_retrieve(self, storage):
        req = _make_request()
        row_id = storage.save(req)
        assert row_id > 0
        row = storage.get_by_id(row_id)
        assert row is not None
        assert row["request_id"] == "test-001"
        assert row["status"] == "pending"

    def test_payload_json_roundtrip(self, storage):
        req = _make_request()
        row_id = storage.save(req)
        row = storage.get_by_id(row_id)
        payload = json.loads(row["payload_json"])
        assert payload["source_url"] == req["source_url"]
        assert payload["confidence"] == req["confidence"]

    def test_created_updated_timestamps(self, storage):
        req = _make_request()
        row_id = storage.save(req)
        row = storage.get_by_id(row_id)
        assert row["created_at"] is not None
        assert row["updated_at"] is not None


# ─── Save invalid request ─────────────────────────────────────

class TestSaveInvalidRequest:
    def test_missing_required_field_raises(self, storage):
        req = {"request_id": "bad", "source_url": "https://example.com"}
        with pytest.raises(ValueError, match="validation"):
            storage.save(req)

    def test_invalid_enum_raises(self, storage):
        req = _make_request(branch_reason="not_a_real_reason")
        with pytest.raises(ValueError, match="validation"):
            storage.save(req)


# ─── Queue listing with priority sort ──────────────────────────

class TestPrioritySort:
    def test_high_before_low(self, storage):
        storage.save(_make_request(request_id="low", priority="low", source_url="https://a.com"))
        storage.save(_make_request(request_id="high", priority="high", source_url="https://b.com"))
        rows = storage.list_pending()
        assert rows[0]["priority"] == "high"
        assert rows[1]["priority"] == "low"

    def test_fifo_within_same_priority(self, storage):
        storage.save(_make_request(request_id="first", priority="medium", source_url="https://a.com"))
        storage.save(_make_request(request_id="second", priority="medium", source_url="https://b.com"))
        rows = storage.list_pending()
        assert rows[0]["request_id"] == "first"
        assert rows[1]["request_id"] == "second"


# ─── Get by id ─────────────────────────────────────────────────

class TestGetById:
    def test_existing_id(self, storage):
        req = _make_request()
        row_id = storage.save(req)
        row = storage.get_by_id(row_id)
        assert row is not None

    def test_nonexistent_id(self, storage):
        row = storage.get_by_id(999999)
        assert row is None


# ─── Status transitions ───────────────────────────────────────

class TestStatusTransitions:
    def test_pending_to_in_review(self, storage):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "in_review")
        row = storage.get_by_id(row_id)
        assert row["status"] == "in_review"

    def test_in_review_to_resolved(self, storage):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "in_review")
        storage.transition(row_id, "resolved")
        row = storage.get_by_id(row_id)
        assert row["status"] == "resolved"

    def test_pending_to_cancelled(self, storage):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "cancelled")
        row = storage.get_by_id(row_id)
        assert row["status"] == "cancelled"

    def test_in_review_to_cancelled(self, storage):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "in_review")
        storage.transition(row_id, "cancelled")
        row = storage.get_by_id(row_id)
        assert row["status"] == "cancelled"

    def test_resolved_to_any_raises(self, storage):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "in_review")
        storage.transition(row_id, "resolved")
        with pytest.raises(ValueError, match="transition"):
            storage.transition(row_id, "in_review")

    def test_cancelled_to_any_raises(self, storage):
        row_id = storage.save(_make_request())
        storage.transition(row_id, "cancelled")
        with pytest.raises(ValueError, match="transition"):
            storage.transition(row_id, "in_review")

    def test_invalid_target_status_raises(self, storage):
        row_id = storage.save(_make_request())
        with pytest.raises(ValueError, match="status"):
            storage.transition(row_id, "bogus")


# ─── Duplicate handling ────────────────────────────────────────

class TestDuplicateHandling:
    def test_unresolved_duplicate_blocked(self, storage):
        req = _make_request()
        storage.save(req)
        with pytest.raises(ValueError, match="duplicate"):
            storage.save(req)

    def test_resolved_then_reallowed(self, storage):
        req = _make_request()
        row_id = storage.save(req)
        storage.transition(row_id, "in_review")
        storage.transition(row_id, "resolved")
        # same canonical URL → should be allowed now
        req2 = _make_request(request_id="test-002")
        row_id2 = storage.save(req2)
        assert row_id2 > 0

    def test_cancelled_then_reallowed(self, storage):
        req = _make_request()
        row_id = storage.save(req)
        storage.transition(row_id, "cancelled")
        req2 = _make_request(request_id="test-002")
        row_id2 = storage.save(req2)
        assert row_id2 > 0

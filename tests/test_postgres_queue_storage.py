"""Tests for PostgreSQL SourceLab queue storage.

These tests are opt-in because they need SOURCELAB_TEST_DATABASE_URL and a
pre-applied sourcelab v0 schema.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest


def _make_request(
    source_url="https://example.com/postgres-storage-test",
    branch_reason="investment_judgment",
    confidence=0.5,
    priority="high",
    request_id="pg-test-001",
) -> dict:
    return {
        "request_id": request_id,
        "source_url": source_url,
        "content_summary": "PostgreSQL storage test summary",
        "branch_reason": branch_reason,
        "confidence": confidence,
        "priority": priority,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "requested_by": "pytest",
    }


@pytest.fixture
def database_url():
    value = os.environ.get("SOURCELAB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("SOURCELAB_TEST_DATABASE_URL is not set")
    return value


@pytest.fixture
def storage(database_url):
    from source_lab_core.postgres_queue_storage import PostgresQueueStorage

    s = PostgresQueueStorage(database_url)
    yield s
    s.close()


def test_save_list_transition_and_result(storage, database_url):
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    req = _make_request(
        request_id=f"pg-test-{suffix}",
        source_url=f"https://example.com/postgres-storage-test-{suffix}",
    )
    row_id = storage.save(req)
    row = storage.get_by_id(row_id)
    assert row is not None
    assert row["request_id"] == req["request_id"]
    assert row["status"] == "pending"

    pending = storage.list_pending(limit=1000)
    assert any(r["request_id"] == req["request_id"] for r in pending)

    storage.transition(row_id, "in_review")
    assert storage.get_by_id(row_id)["status"] == "in_review"
    storage.transition(row_id, "resolved")
    storage.save_result(
        row_id,
        {
            "request_id": req["request_id"],
            "judgment": "archive",
            "reason": "test result",
            "confidence": 0.9,
            "action": "archive",
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "decided_by": "pytest",
        },
        datetime.now(timezone.utc).isoformat(),
    )
    resolved = storage.get_by_request_id(req["request_id"])
    assert resolved["status"] == "resolved"
    assert resolved["result_json"]

    from source_lab_core.postgres_queue_storage import PostgresQueueStorage

    persisted = PostgresQueueStorage(database_url)
    try:
        persisted_row = persisted.get_by_request_id(req["request_id"])
        assert persisted_row is not None
        assert persisted_row["status"] == "resolved"
    finally:
        persisted.close()


def test_unresolved_duplicate_blocked(storage):
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    url = f"https://example.com/postgres-duplicate-{suffix}"
    storage.save(_make_request(request_id=f"pg-dupe-a-{suffix}", source_url=url))
    with pytest.raises(ValueError, match="duplicate"):
        storage.save(_make_request(request_id=f"pg-dupe-b-{suffix}", source_url=url))

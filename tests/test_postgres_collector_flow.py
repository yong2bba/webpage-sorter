"""Tests for PostgreSQL SourceLab full collector flow persistence.

These tests are opt-in because they need SOURCELAB_TEST_DATABASE_URL and a
pre-applied sourcelab v0 schema.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from source_lab_core.intake import intake_url
from source_lab_core.result_processing import ResultProcessor


def _suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


@pytest.fixture
def database_url():
    value = os.environ.get("SOURCELAB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("SOURCELAB_TEST_DATABASE_URL is not set")
    return value


@pytest.fixture
def repository(database_url):
    from source_lab_core.postgres_collector_flow import PostgresCollectorFlowRepository

    repo = PostgresCollectorFlowRepository(database_url)
    yield repo
    repo.close()


def test_records_self_close_flow_in_source_intake_artifact_analysis_and_branch(repository):
    suffix = _suffix()
    url = f"https://example.com/source-flow-self-close-{suffix}"

    def analyzer(canonical_url: str) -> dict:
        return {
            "url": canonical_url,
            "content_type": "website",
            "confidence": 0.94,
            "signals": ["open_source_tool"],
            "risk_flags": [],
            "evidence": ["official page describes a public utility"],
            "summary": "Safe high-confidence public-source utility page.",
            "title": "Self-close flow test",
            "raw_text_preview": "preview text",
        }

    result = intake_url(url, analyzer=analyzer, requested_by="pytest", confidence_threshold=0.8)
    assert result.state == "self_close"

    recorded = repository.record_intake_result(
        result,
        requested_by="pytest",
        submitted_via="pytest",
        request_id=f"flow-self-close-{suffix}",
    )

    assert recorded["source_id"]
    assert recorded["intake_event_id"]
    assert recorded["artifact_id"]
    assert recorded["analysis_id"]
    assert recorded["branch_decision_id"]
    assert recorded["judgment_request_id"] is None
    assert recorded["state"] == "self_close"

    state = repository.get_latest_source_state(url)
    assert state["canonical_url"] == result.canonical_url
    assert state["latest_branch_state"] == "self_close"
    assert state["analysis_summary"] == "Safe high-confidence public-source utility page."


def test_records_judgment_flow_and_final_decision(repository):
    suffix = _suffix()
    url = f"https://example.com/source-flow-judgment-{suffix}"

    def analyzer(canonical_url: str) -> dict:
        return {
            "url": canonical_url,
            "content_type": "financial",
            "confidence": 0.61,
            "signals": ["financial_forecast"],
            "risk_flags": ["unverified_claim"],
            "evidence": ["page contains a forecast without source documents"],
            "summary": "Financial forecast needs senior-agent judgment.",
            "title": "Judgment flow test",
            "key_claims": [{"claim": "projected return"}],
            "extracted_entities": [{"name": "Example Asset", "type": "asset"}],
        }

    result = intake_url(url, analyzer=analyzer, requested_by="pytest", confidence_threshold=0.8)
    assert result.state == "judgment_requested"

    recorded = repository.record_intake_result(
        result,
        requested_by="pytest",
        submitted_via="pytest",
        request_id=f"flow-judgment-{suffix}",
    )

    assert recorded["judgment_request_id"]
    request_id = recorded["judgment_request_request_id"]
    queue_row = repository.get_by_request_id(request_id)
    assert queue_row["analysis_id"] == recorded["analysis_id"]
    assert queue_row["branch_decision_id"] == recorded["branch_decision_id"]
    assert queue_row["intake_event_id"] == recorded["intake_event_id"]

    outcome = ResultProcessor(repository).process(
        {
            "request_id": request_id,
            "judgment": "approved",
            "reason": "Test decision archives the source after review.",
            "confidence": 0.93,
            "action": "archive",
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "decided_by": "pytest",
        }
    )
    assert outcome.errors == []
    assert outcome.outcome == "archived"

    state = repository.get_latest_source_state(url)
    assert state["latest_branch_state"] == "judgment_requested"
    assert state["judgment_status"] is None
    assert state["decision"] == "approved"
    assert state["action"] == "archive"

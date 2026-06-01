"""Tests for SourceLab OtterWiki Markdown projections."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from source_lab_core.intake import intake_url


def _suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def test_source_markdown_projection_is_lowercase_safe_and_human_readable():
    from source_lab_core.wiki_projection import build_source_report_markdown, markdown_path_for_source

    state = {
        "source_type": "github_repo",
        "canonical_key": "github:Harry0703/MoneyPrinterTurbo",
        "canonical_url": "https://github.com/harry0703/MoneyPrinterTurbo",
        "title": "MoneyPrinterTurbo",
        "source_summary": "AI short-form video generation pipeline.",
        "analysis_summary": "Collects script, voice, subtitles and video rendering into one workflow.",
        "signals": ["open_source_tool", "video_generation"],
        "risk_flags": ["requires_api_keys"],
        "latest_branch_state": "self_close",
        "branch_reason": None,
        "priority": "low",
        "decision": "approved",
        "action": "archive",
        "decision_reason": "Useful reference project.",
    }

    path = markdown_path_for_source(state)
    assert path == "sourcelab/sources/github/harry0703-moneyprinterturbo.md"
    assert path == path.lower()

    markdown = build_source_report_markdown(state, generated_at="2026-06-01 13:00 KST")
    assert markdown.startswith("# MoneyPrinterTurbo")
    assert "## 접수 정보" in markdown
    assert "## 1차 요약" in markdown
    assert "## Collector 판단" in markdown
    assert "## AI agent 판단" in markdown
    assert "https://github.com/harry0703/MoneyPrinterTurbo" in markdown
    assert "open_source_tool" in markdown
    assert "requires_api_keys" in markdown
    assert "```json" not in markdown
    assert "raw_html" not in markdown


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


def test_projector_writes_markdown_and_records_projection(repository, tmp_path):
    from source_lab_core.wiki_projection import WikiProjectionRenderer

    suffix = _suffix()
    url = f"https://github.com/harry0703/MoneyPrinterTurbo-{suffix}"

    def analyzer(canonical_url: str) -> dict:
        return {
            "url": canonical_url,
            "content_type": "technical",
            "confidence": 0.95,
            "signals": ["open_source_tool", "video_generation"],
            "risk_flags": [],
            "evidence": ["GitHub repository page", "README describes video generation workflow"],
            "summary": "MoneyPrinterTurbo-style smoke repo projection test.",
            "title": "MoneyPrinterTurbo Smoke",
            "license": "MIT",
            "language": "Python",
        }

    result = intake_url(url, analyzer=analyzer, requested_by="pytest", confidence_threshold=0.8)
    recorded = repository.record_intake_result(
        result,
        requested_by="pytest",
        submitted_via="pytest",
        request_id=f"wiki-projection-{suffix}",
    )

    renderer = WikiProjectionRenderer(
        repository,
        wiki_repo_path=tmp_path,
        wiki_base_url="https://example.com",
    )
    projection = renderer.render_source_report(recorded["source_id"], commit=False)

    assert projection["markdown_path"].startswith("sourcelab/sources/github/")
    assert projection["markdown_path"] == projection["markdown_path"].lower()
    output_path = tmp_path / projection["markdown_path"]
    assert output_path.exists()
    markdown = output_path.read_text(encoding="utf-8")
    assert "MoneyPrinterTurbo Smoke" in markdown
    assert "MoneyPrinterTurbo-style smoke repo projection test." in markdown
    assert projection["status"] == "rendered"
    assert projection["content_sha256"]

    db_row = repository.get_wiki_projection(recorded["source_id"])
    assert db_row["markdown_path"] == projection["markdown_path"]
    assert db_row["status"] == "rendered"


def test_queue_projection_writes_pending_judgment_queue(repository, tmp_path):
    from source_lab_core.wiki_projection import WikiProjectionRenderer

    suffix = _suffix()
    url = f"https://example.com/wiki-queue-projection-{suffix}"

    def analyzer(canonical_url: str) -> dict:
        return {
            "url": canonical_url,
            "content_type": "financial",
            "confidence": 0.43,
            "signals": ["financial_forecast"],
            "risk_flags": ["unverified_claim"],
            "evidence": ["forecast without source document"],
            "summary": "Queue projection test item requiring judgment.",
            "title": "Queue Projection Test",
        }

    result = intake_url(url, analyzer=analyzer, requested_by="pytest", confidence_threshold=0.8)
    recorded = repository.record_intake_result(
        result,
        requested_by="pytest",
        submitted_via="pytest",
        request_id=f"wiki-queue-{suffix}",
    )
    assert recorded["judgment_request_id"]

    renderer = WikiProjectionRenderer(repository, wiki_repo_path=tmp_path)
    projection = renderer.render_judgment_queue(commit=False)

    assert projection["markdown_path"] == "sourcelab/queue/judgmentrequested.md"
    output_path = tmp_path / projection["markdown_path"]
    assert output_path.exists()
    markdown = output_path.read_text(encoding="utf-8")
    assert "# Judgment Queue" in markdown
    assert "Queue projection test item requiring judgment." in markdown
    assert "investment_judgment" in markdown
    assert projection["status"] == "rendered"


def test_intake_handler_auto_projects_source_and_queue_when_wiki_path_is_supplied(database_url, tmp_path):
    import json
    from source_lab import handle_source_lab_intake_url

    suffix = _suffix()
    url = f"https://example.com/auto-project-{suffix}"
    response = json.loads(handle_source_lab_intake_url({
        "url": url,
        "analysis": {
            "url": url,
            "content_type": "financial",
            "confidence": 0.4,
            "signals": ["financial_forecast"],
            "risk_flags": ["unverified_claim"],
            "evidence": ["auto projection evidence"],
            "summary": "Auto projection handler test.",
            "title": "Auto Projection Handler Test",
        },
        "enqueue": True,
        "requested_by": "pytest",
        "database_url": database_url,
        "wiki_repo_path": str(tmp_path),
        "wiki_commit": False,
    }))

    assert response["success"] is True
    assert response["collector_flow"]["source_id"]
    assert response["wiki_projection"]["source_report"]["status"] == "rendered"
    assert response["wiki_projection"]["judgment_queue"]["status"] == "rendered"
    assert (tmp_path / response["wiki_projection"]["source_report"]["markdown_path"]).exists()
    assert (tmp_path / "sourcelab/queue/judgmentrequested.md").exists()


def test_process_result_auto_refreshes_source_and_queue_projection(database_url, tmp_path):
    import json
    from datetime import datetime, timezone
    from source_lab import handle_source_lab_intake_url, handle_source_lab_process_result

    suffix = _suffix()
    url = f"https://example.com/auto-process-project-{suffix}"
    intake = json.loads(handle_source_lab_intake_url({
        "url": url,
        "analysis": {
            "url": url,
            "content_type": "financial",
            "confidence": 0.35,
            "signals": ["financial_forecast"],
            "risk_flags": ["unverified_claim"],
            "evidence": ["process projection evidence"],
            "summary": "Process projection handler test.",
            "title": "Process Projection Handler Test",
        },
        "enqueue": True,
        "requested_by": "pytest",
        "database_url": database_url,
        "wiki_repo_path": str(tmp_path),
        "wiki_commit": False,
    }))
    request_id = intake["collector_flow"]["judgment_request_request_id"]

    processed = json.loads(handle_source_lab_process_result({
        "result": {
            "request_id": request_id,
            "judgment": "approved",
            "reason": "projection refresh after decision",
            "confidence": 0.9,
            "action": "archive",
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "decided_by": "pytest",
        },
        "database_url": database_url,
        "wiki_repo_path": str(tmp_path),
        "wiki_commit": False,
    }))

    assert processed["success"] is True
    assert processed["wiki_projection"]["source_report"]["status"] == "rendered"
    assert processed["wiki_projection"]["judgment_queue"]["status"] == "rendered"
    markdown = (tmp_path / processed["wiki_projection"]["source_report"]["markdown_path"]).read_text(encoding="utf-8")
    assert "Action: `archive`" in markdown
    assert "projection refresh after decision" in markdown

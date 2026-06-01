"""Tests for source_lab payload builders."""

import pytest


class TestJudgmentRequestBuilder:
    def test_build_minimal_request(self):
        from source_lab_core.builders import build_judgment_request
        req = build_judgment_request(
            source_url="https://example.com",
            content_summary="Test summary",
            branch_reason="investment_judgment",
            confidence=0.5,
            priority="high",
            requested_by="coder01",
        )
        assert req["source_url"] == "https://example.com"
        assert req["content_summary"] == "Test summary"
        assert req["branch_reason"] == "investment_judgment"
        assert req["confidence"] == 0.5
        assert req["priority"] == "high"
        assert req["requested_by"] == "coder01"
        assert "request_id" in req
        assert "requested_at" in req

    def test_build_request_with_analysis_snapshot(self):
        from source_lab_core.builders import build_judgment_request
        req = build_judgment_request(
            source_url="https://example.com",
            content_summary="Test",
            branch_reason="low_confidence",
            confidence=0.3,
            priority="medium",
            requested_by="coder01",
            analysis_snapshot={"title": "Article", "content_type": "news"},
        )
        assert req["analysis_snapshot"]["title"] == "Article"

    def test_build_request_generates_uuid(self):
        from source_lab_core.builders import build_judgment_request
        r1 = build_judgment_request(
            source_url="https://a.com", content_summary="A",
            branch_reason="low_confidence", confidence=0.5,
            priority="low", requested_by="coder01",
        )
        r2 = build_judgment_request(
            source_url="https://b.com", content_summary="B",
            branch_reason="low_confidence", confidence=0.5,
            priority="low", requested_by="coder01",
        )
        assert r1["request_id"] != r2["request_id"]


class TestJudgmentResultBuilder:
    def test_build_minimal_result(self):
        from source_lab_core.builders import build_judgment_result
        res = build_judgment_result(
            request_id="abc-123",
            judgment="approved",
            reason="Looks correct",
            confidence=0.9,
            action="close",
            decided_by="yongyongbot",
        )
        assert res["request_id"] == "abc-123"
        assert res["judgment"] == "approved"
        assert res["reason"] == "Looks correct"
        assert res["confidence"] == 0.9
        assert res["action"] == "close"
        assert res["decided_by"] == "yongyongbot"
        assert "decided_at" in res

    def test_build_result_with_evidence(self):
        from source_lab_core.builders import build_judgment_result
        res = build_judgment_result(
            request_id="abc-123",
            judgment="modified",
            reason="Adjusted",
            confidence=0.7,
            action="close",
            decided_by="yongyongbot",
            evidence=["source1", "source2"],
        )
        assert res["evidence"] == ["source1", "source2"]

    def test_build_result_with_followup_tasks(self):
        from source_lab_core.builders import build_judgment_result
        res = build_judgment_result(
            request_id="abc-123",
            judgment="deferred",
            reason="Need more data",
            confidence=0.5,
            action="queue_followup",
            decided_by="yongyongbot",
            followup_tasks=[{"task_type": "deep_analysis", "description": "Analyze deeper"}],
        )
        assert len(res["followup_tasks"]) == 1


class TestBuilderValidationIntegration:
    def test_built_request_passes_validation(self):
        from source_lab_core.builders import build_judgment_request
        from source_lab_core.validation import validate_judgment_request
        req = build_judgment_request(
            source_url="https://example.com",
            content_summary="Test",
            branch_reason="investment_judgment",
            confidence=0.5,
            priority="high",
            requested_by="coder01",
        )
        errors = validate_judgment_request(req)
        assert errors == []

    def test_built_result_passes_validation(self):
        from source_lab_core.builders import build_judgment_result
        from source_lab_core.validation import validate_judgment_result
        res = build_judgment_result(
            request_id="abc-123",
            judgment="approved",
            reason="Looks correct",
            confidence=0.9,
            action="close",
            decided_by="yongyongbot",
        )
        errors = validate_judgment_result(res)
        assert errors == []

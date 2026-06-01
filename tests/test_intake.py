"""Tests for source_lab intake pipeline (v0.3.3)."""

import pytest


# ─── Helpers ───────────────────────────────────────────────────

def _fake_analyzer(url: str) -> dict:
    """Deterministic fake analyzer for testing."""
    return {
        "url": url,
        "content_type": "news",
        "confidence": 0.9,
        "signals": [],
        "risk_flags": [],
        "evidence": ["fetched headline", "parsed body"],
        "summary": "Article about technology.",
    }


def _fake_analyzer_low_confidence(url: str) -> dict:
    return {
        "url": url,
        "content_type": "news",
        "confidence": 0.4,
        "signals": [],
        "risk_flags": [],
        "evidence": ["partial data"],
        "summary": "Unclear article.",
    }


def _fake_analyzer_financial(url: str) -> dict:
    return {
        "url": url,
        "content_type": "financial",
        "confidence": 0.95,
        "signals": [],
        "risk_flags": [],
        "evidence": ["SEC filing"],
        "summary": "Earnings report.",
    }


def _failing_analyzer(url: str) -> dict:
    raise ConnectionError("fetch failed")


# ─── Valid URL intake ──────────────────────────────────────────

class TestValidUrlIntake:
    def test_valid_url_calls_analyzer(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article",
            analyzer=_fake_analyzer,
        )
        assert result.errors == []
        assert result.analysis is not None
        assert result.analysis["content_type"] == "news"

    def test_valid_url_returns_branch_decision(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article",
            analyzer=_fake_analyzer,
        )
        assert result.branch_decision is not None
        assert result.state in ("self_close", "judgment_requested")


# ─── Invalid URL ───────────────────────────────────────────────

class TestInvalidUrl:
    def test_empty_url_returns_error(self):
        from source_lab_core.intake import intake_url
        result = intake_url("", analyzer=_fake_analyzer)
        assert result.errors
        assert result.analysis is None

    def test_no_scheme_returns_error(self):
        from source_lab_core.intake import intake_url
        result = intake_url("example.com/article", analyzer=_fake_analyzer)
        assert result.errors
        assert result.analysis is None

    def test_invalid_url_no_analyzer_call(self):
        from source_lab_core.intake import intake_url
        call_count = {"n": 0}

        def counting_analyzer(url: str) -> dict:
            call_count["n"] += 1
            return _fake_analyzer(url)

        result = intake_url("not-a-url", analyzer=counting_analyzer)
        assert call_count["n"] == 0
        assert result.errors


# ─── Duplicate URL ─────────────────────────────────────────────

class TestDuplicateUrl:
    def test_duplicate_url_skips_analyzer(self):
        from source_lab_core.intake import intake_url
        call_count = {"n": 0}

        def counting_analyzer(url: str) -> dict:
            call_count["n"] += 1
            return _fake_analyzer(url)

        seen = {"https://example.com/article"}
        result = intake_url(
            "https://example.com/article",
            analyzer=counting_analyzer,
            seen_urls=seen,
        )
        assert call_count["n"] == 0
        assert result.duplicate is True
        assert result.analysis is None

    def test_new_url_calls_analyzer(self):
        from source_lab_core.intake import intake_url
        seen = {"https://other.com"}
        result = intake_url(
            "https://example.com/article",
            analyzer=_fake_analyzer,
            seen_urls=seen,
        )
        assert result.duplicate is False
        assert result.analysis is not None


# ─── Analyzer failure ──────────────────────────────────────────

class TestAnalyzerFailure:
    def test_analyzer_exception_returns_error(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article",
            analyzer=_failing_analyzer,
        )
        assert result.errors
        assert result.analysis is None
        assert result.state == "error"


# ─── Canonical URL normalization ───────────────────────────────

class TestCanonicalUrl:
    def test_trailing_slash_normalized(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article/",
            analyzer=_fake_analyzer,
        )
        assert result.canonical_url == "https://example.com/article"

    def test_fragment_stripped(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article#section1",
            analyzer=_fake_analyzer,
        )
        assert result.canonical_url == "https://example.com/article"

    def test_whitespace_trimmed(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "  https://example.com/article  ",
            analyzer=_fake_analyzer,
        )
        assert result.canonical_url == "https://example.com/article"

    def test_lowercase_scheme_host(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "HTTPS://Example.COM/article",
            analyzer=_fake_analyzer,
        )
        assert result.canonical_url == "https://example.com/article"


# ─── Self-close path ───────────────────────────────────────────

class TestSelfClosePath:
    def test_high_confidence_self_closes(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article",
            analyzer=_fake_analyzer,
        )
        assert result.state == "self_close"
        assert result.branch_decision.state == "self_close"


# ─── Judgment requested path ───────────────────────────────────

class TestJudgmentRequestedPath:
    def test_low_confidence_requests_judgment(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article",
            analyzer=_fake_analyzer_low_confidence,
        )
        assert result.state == "judgment_requested"
        assert result.branch_decision.state == "judgment_requested"

    def test_judgment_payload_passes_validation(self):
        from source_lab_core.intake import intake_url
        from source_lab_core.validation import validate_judgment_request
        result = intake_url(
            "https://example.com/article",
            analyzer=_fake_analyzer_low_confidence,
        )
        payload = result.branch_decision.judgment_request_payload
        assert payload is not None
        errors = validate_judgment_request(payload)
        assert errors == []

    def test_financial_requests_judgment(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/earnings",
            analyzer=_fake_analyzer_financial,
        )
        assert result.state == "judgment_requested"


# ─── IntakeResult structure ────────────────────────────────────

class TestIntakeResultStructure:
    def test_has_required_fields(self):
        from source_lab_core.intake import intake_url
        result = intake_url(
            "https://example.com/article",
            analyzer=_fake_analyzer,
        )
        assert hasattr(result, "state")
        assert hasattr(result, "url")
        assert hasattr(result, "canonical_url")
        assert hasattr(result, "duplicate")
        assert hasattr(result, "analysis")
        assert hasattr(result, "branch_decision")
        assert hasattr(result, "errors")

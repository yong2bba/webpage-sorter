"""Tests for source_lab branching logic (v0.3.2)."""

import pytest


# ─── Fixtures ──────────────────────────────────────────────────

def _make_analysis(**overrides) -> dict:
    """Minimal valid analysis result with sensible defaults."""
    base = {
        "url": "https://example.com/article",
        "content_type": "news",
        "confidence": 0.9,
        "signals": [],
        "risk_flags": [],
        "evidence": ["fetched headline", "parsed body"],
        "summary": "Article about technology.",
    }
    base.update(overrides)
    return base


# ─── Self-close cases ─────────────────────────────────────────

class TestSelfClose:
    def test_high_confidence_low_risk_self_closes(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.95, risk_flags=[])
        result = decide_branch(analysis)
        assert result.state == "self_close"

    def test_news_with_evidence_self_closes(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            content_type="news",
            confidence=0.9,
            evidence=["headline", "body", "source"],
        )
        result = decide_branch(analysis)
        assert result.state == "self_close"

    def test_technical_doc_self_closes(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(content_type="technical", confidence=0.85)
        result = decide_branch(analysis)
        assert result.state == "self_close"


# ─── Low confidence → judgment ─────────────────────────────────

class TestLowConfidence:
    def test_below_threshold_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.5)
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "low_confidence"

    def test_just_below_threshold_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.79)
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "low_confidence"

    def test_threshold_override(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.7)
        # default threshold 0.8 → should request
        r1 = decide_branch(analysis)
        assert r1.state == "judgment_requested"
        # override to 0.6 → should self-close
        r2 = decide_branch(analysis, confidence_threshold=0.6)
        assert r2.state == "self_close"


# ─── Forbidden domains → judgment ──────────────────────────────

class TestForbiddenDomains:
    def test_financial_content_type_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(content_type="financial", confidence=0.95)
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "investment_judgment"

    def test_investment_signal_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            confidence=0.95,
            signals=["investment_advice"],
        )
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "investment_judgment"

    def test_legal_signal_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            confidence=0.95,
            signals=["legal_interpretation"],
        )
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "regulatory_interpretation"

    def test_personal_data_signal_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            confidence=0.95,
            signals=["personal_data"],
        )
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "personal_data_sensitivity"

    def test_health_signal_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            confidence=0.95,
            signals=["medical_advice"],
        )
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"


# ─── Risk flags → judgment ─────────────────────────────────────

class TestRiskFlags:
    def test_risk_flag_present_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            confidence=0.95,
            risk_flags=["contradicts_known_facts"],
        )
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "conflicting_information"

    def test_multiple_risk_flags_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            confidence=0.95,
            risk_flags=["unverified_claim", "bias_detected"],
        )
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"


# ─── Insufficient evidence → judgment ──────────────────────────

class TestInsufficientEvidence:
    def test_empty_evidence_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.9, evidence=[])
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.branch_reason == "low_confidence"

    def test_none_evidence_requests_judgment(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.9)
        analysis["evidence"] = None
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"


# ─── Priority assignment ───────────────────────────────────────

class TestPriorityAssignment:
    def test_financial_gets_high_priority(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(content_type="financial", confidence=0.95)
        result = decide_branch(analysis)
        assert result.priority == "high"

    def test_low_confidence_gets_medium_priority(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.3)
        result = decide_branch(analysis)
        assert result.priority == "medium"

    def test_risk_flag_gets_medium_priority(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(
            confidence=0.95,
            risk_flags=["unverified_claim"],
        )
        result = decide_branch(analysis)
        assert result.priority == "medium"

    def test_self_close_gets_low_priority(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.95)
        result = decide_branch(analysis)
        assert result.priority == "low"


# ─── Output payload validity ───────────────────────────────────

class TestOutputPayloadValidity:
    def test_judgment_request_payload_validates(self):
        from source_lab_core.branching import decide_branch
        from source_lab_core.validation import validate_judgment_request
        analysis = _make_analysis(confidence=0.5)
        result = decide_branch(analysis)
        assert result.state == "judgment_requested"
        assert result.judgment_request_payload is not None
        errors = validate_judgment_request(result.judgment_request_payload)
        assert errors == []

    def test_self_close_has_no_payload(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.95)
        result = decide_branch(analysis)
        assert result.state == "self_close"
        assert result.judgment_request_payload is None


# ─── Confidence preservation ───────────────────────────────────

class TestConfidencePreservation:
    def test_low_confidence_preserved_in_decision(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.5)
        result = decide_branch(analysis)
        assert result.confidence == 0.5

    def test_low_confidence_preserved_in_payload(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.5)
        result = decide_branch(analysis)
        assert result.judgment_request_payload["confidence"] == 0.5

    def test_low_confidence_payload_passes_validation(self):
        from source_lab_core.branching import decide_branch
        from source_lab_core.validation import validate_judgment_request
        analysis = _make_analysis(confidence=0.5)
        result = decide_branch(analysis)
        errors = validate_judgment_request(result.judgment_request_payload)
        assert errors == []


# ─── BranchDecision structure ──────────────────────────────────

class TestBranchDecisionStructure:
    def test_has_required_fields(self):
        from source_lab_core.branching import decide_branch
        analysis = _make_analysis(confidence=0.95)
        result = decide_branch(analysis)
        assert hasattr(result, "state")
        assert hasattr(result, "branch_reason")
        assert hasattr(result, "priority")
        assert hasattr(result, "confidence")
        assert hasattr(result, "reason")
        assert hasattr(result, "evidence")
        assert hasattr(result, "judgment_request_payload")

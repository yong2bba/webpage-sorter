"""Tests for source_lab validation logic."""

import pytest


class TestRequiredFieldValidation:
    def test_valid_request_passes(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com/article",
            "content_summary": "Test summary",
            "branch_reason": "investment_judgment",
            "confidence": 0.5,
            "priority": "high",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert errors == []

    def test_missing_required_field_fails(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            # source_url missing
            "content_summary": "Test summary",
            "branch_reason": "investment_judgment",
            "confidence": 0.5,
            "priority": "high",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert any("source_url" in e for e in errors)

    def test_valid_result_passes(self):
        from source_lab_core.validation import validate_judgment_result
        payload = {
            "request_id": "abc-123",
            "judgment": "approved",
            "reason": "Looks correct",
            "confidence": 0.9,
            "action": "close",
            "decided_at": "2026-05-26T00:00:00Z",
            "decided_by": "yongyongbot",
        }
        errors = validate_judgment_result(payload)
        assert errors == []


class TestEnumValidation:
    def test_invalid_branch_reason_fails(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "branch_reason": "invalid_reason",
            "confidence": 0.5,
            "priority": "high",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert any("branch_reason" in e for e in errors)

    def test_invalid_judgment_fails(self):
        from source_lab_core.validation import validate_judgment_result
        payload = {
            "request_id": "abc-123",
            "judgment": "invalid_judgment",
            "reason": "test",
            "confidence": 0.9,
            "action": "close",
            "decided_at": "2026-05-26T00:00:00Z",
            "decided_by": "yongyongbot",
        }
        errors = validate_judgment_result(payload)
        assert any("judgment" in e for e in errors)


class TestConfidenceValidation:
    def test_confidence_below_range_fails(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "branch_reason": "low_confidence",
            "confidence": -0.1,
            "priority": "low",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert any("confidence" in e for e in errors)

    def test_confidence_above_range_fails(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "branch_reason": "low_confidence",
            "confidence": 1.5,
            "priority": "low",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert any("confidence" in e for e in errors)

    def test_low_confidence_below_threshold_passes(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "branch_reason": "low_confidence",
            "confidence": 0.5,
            "priority": "low",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert errors == []

    def test_low_confidence_above_threshold_fails(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "branch_reason": "low_confidence",
            "confidence": 0.9,
            "priority": "low",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert any("confidence" in e for e in errors)

    def test_low_confidence_threshold_override(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "branch_reason": "low_confidence",
            "confidence": 0.7,
            "priority": "low",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        # default threshold 0.8 → 0.7 < 0.8 → low_confidence is valid → pass
        errors_default = validate_judgment_request(payload)
        assert not any("confidence" in e for e in errors_default)
        # override threshold to 0.6 → 0.7 >= 0.6 → contradicts low_confidence → fail
        errors_override = validate_judgment_request(payload, confidence_threshold=0.6)
        assert any("confidence" in e for e in errors_override)


class TestContentTypeValidation:
    def test_known_content_type_no_warning(self):
        from source_lab_core.validation import validate_content_type
        warnings = validate_content_type("news")
        assert warnings == []

    def test_unknown_content_type_warning(self):
        from source_lab_core.validation import validate_content_type
        warnings = validate_content_type("podcast")
        assert len(warnings) > 0
        assert any("unknown" in w.lower() for w in warnings)


class TestUnknownFieldPolicy:
    def test_unknown_root_field_fails(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "branch_reason": "low_confidence",
            "confidence": 0.9,
            "priority": "low",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
            "unknown_field": "should fail",
        }
        errors = validate_judgment_request(payload)
        assert any("unknown" in e.lower() for e in errors)

    def test_analysis_snapshot_subfields_allowed(self):
        from source_lab_core.validation import validate_judgment_request
        payload = {
            "request_id": "abc-123",
            "source_url": "https://example.com",
            "content_summary": "Test",
            "analysis_snapshot": {
                "title": "Test",
                "content_type": "news",
                "key_claims": ["claim1"],
                "extracted_entities": ["entity1"],
                "raw_text_preview": "preview",
            },
            "branch_reason": "investment_judgment",
            "confidence": 0.9,
            "priority": "low",
            "requested_at": "2026-05-26T00:00:00Z",
            "requested_by": "coder01",
        }
        errors = validate_judgment_request(payload)
        assert errors == []

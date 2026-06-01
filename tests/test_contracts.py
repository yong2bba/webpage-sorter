"""Tests for source_lab contracts — enum and payload type definitions."""

import pytest


class TestPipelineStateEnum:
    def test_all_states_exist(self):
        from source_lab_core.contracts import PipelineState
        expected = [
            "url_intake", "low_level_analysis", "self_close",
            "judgment_requested", "judgment_result_received",
            "final_close", "queued_followup",
        ]
        assert [s.value for s in PipelineState] == expected

    def test_terminal_states(self):
        from source_lab_core.contracts import PipelineState
        terminals = PipelineState.terminal_states()
        assert PipelineState.SELF_CLOSE in terminals
        assert PipelineState.FINAL_CLOSE in terminals
        assert PipelineState.QUEUED_FOLLOWUP in terminals
        assert PipelineState.URL_INTAKE not in terminals


class TestBranchReasonEnum:
    def test_all_reasons_exist(self):
        from source_lab_core.contracts import BranchReason
        expected = [
            "investment_judgment", "regulatory_interpretation",
            "personal_data_sensitivity", "conflicting_information",
            "low_confidence", "explicit_yongyongbot_request",
        ]
        assert [r.value for r in BranchReason] == expected


class TestPriorityEnum:
    def test_ordering(self):
        from source_lab_core.contracts import Priority
        assert Priority.URGENT < Priority.HIGH < Priority.MEDIUM < Priority.LOW

    def test_all_priorities_exist(self):
        from source_lab_core.contracts import Priority
        assert len(Priority) == 4


class TestJudgmentEnum:
    def test_all_judgments_exist(self):
        from source_lab_core.contracts import Judgment
        expected = ["approved", "rejected", "modified", "escalated", "deferred"]
        assert [j.value for j in Judgment] == expected


class TestActionEnum:
    def test_all_actions_exist(self):
        from source_lab_core.contracts import Action
        expected = ["close", "reanalyze", "queue_followup", "escalate_to_human", "archive"]
        assert [a.value for a in Action] == expected


class TestContentTypePolicy:
    def test_known_types_exist(self):
        from source_lab_core.contracts import ContentType
        known = ["news", "opinion", "technical", "financial", "other"]
        assert [ct.value for ct in ContentType] == known

    def test_unknown_content_type_allowed(self):
        from source_lab_core.contracts import is_known_content_type
        assert is_known_content_type("news") is True
        assert is_known_content_type("unknown_type") is False

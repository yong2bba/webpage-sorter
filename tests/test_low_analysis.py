"""Tests for SourceLab low-level auxiliary URL analysis."""

from __future__ import annotations

import json
from types import SimpleNamespace


def _response(payload: dict):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
    )


def test_normalize_analysis_filters_unknown_values():
    from source_lab_core.low_analysis import normalize_analysis

    analysis = normalize_analysis(
        "https://Example.com/a#frag",
        {
            "content_type": "unknown",
            "confidence": 1.5,
            "signals": ["investment_advice", "bogus"],
            "risk_flags": ["insufficient_source", "bogus"],
            "evidence": ["one", 2, "two"],
            "summary": "요약",
        },
    )

    assert analysis["url"] == "https://example.com/a"
    assert analysis["content_type"] == "other"
    assert analysis["confidence"] == 1.0
    assert analysis["signals"] == ["investment_advice"]
    assert analysis["risk_flags"] == ["insufficient_source"]
    assert analysis["evidence"] == ["one", "two"]


def test_analyze_url_low_level_uses_auxiliary_task(monkeypatch):
    from agent import auxiliary_client
    from source_lab_core import low_analysis

    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return _response(
            {
                "title": "Example",
                "content_type": "technical",
                "confidence": 0.91,
                "signals": [],
                "risk_flags": [],
                "evidence": ["observed docs text"],
                "summary": "기술 문서 요약",
            }
        )

    monkeypatch.setattr(auxiliary_client, "call_llm", fake_call_llm)

    analysis = low_analysis.analyze_url_low_level(
        "https://example.com/docs",
        text="Observed docs text about an API.",
        llm_timeout=7,
        max_tokens=333,
    )

    assert calls
    assert calls[0]["task"] == "source_lab_low_analysis"
    assert calls[0]["timeout"] == 7
    assert calls[0]["max_tokens"] == 333
    assert analysis["content_type"] == "technical"
    assert analysis["confidence"] == 0.91
    assert analysis["analysis_model_task"] == "source_lab_low_analysis"


def test_empty_fetched_text_returns_low_confidence_without_llm(monkeypatch):
    from agent import auxiliary_client
    from source_lab_core import low_analysis

    def fail_call_llm(**kwargs):  # pragma: no cover - should not be called
        raise AssertionError("LLM should not be called for empty fetched text")

    monkeypatch.setattr(auxiliary_client, "call_llm", fail_call_llm)

    analysis = low_analysis.analyze_url_low_level("https://example.com/empty", text="   ")

    assert analysis["confidence"] == 0.2
    assert analysis["risk_flags"] == ["insufficient_source"]

import asyncio
import json
from types import SimpleNamespace

import pytest

import source_lab


class _Platform:
    value = "slack"


class _Gateway:
    def __init__(self):
        self.notices = []

    async def _deliver_platform_notice(self, source, content):
        self.notices.append((source, content))


def _event(text="<https://github.com/nashsu/llm_wiki|https://github.com/nashsu/llm_wiki>"):
    source = SimpleNamespace(
        platform=_Platform(),
        chat_id="C0123456789",
        user_id="U123",
        user_name="용진",
        thread_id="1780289840.236399",
    )
    return SimpleNamespace(source=source, text=text, metadata={})


def test_extract_intake_url_prefers_github_over_slack_archive():
    text = (
        "<https://yongjin-hq.slack.com/archives/C0123456789/p1780289840236399|slack> "
        "📎 <https://github.com/nashsu/llm_wiki|https://github.com/nashsu/llm_wiki>"
    )
    assert source_lab._extract_intake_url(text) == "https://github.com/nashsu/llm_wiki"


@pytest.mark.asyncio
async def test_pre_gateway_dispatch_auto_intakes_and_skips(monkeypatch):
    monkeypatch.setenv("SOURCELAB_SLACK_AUTO_INTAKE", "true")
    monkeypatch.setenv("SOURCELAB_SLACK_AUTO_CHANNELS", "C0123456789")

    def fake_analyze(args):
        assert args["url"] == "https://github.com/nashsu/llm_wiki"
        assert args["submitted_via"] == "slack"
        assert args["slack_channel_id"] == "C0123456789"
        assert args["request_id"] == "slack-C0123456789-1780289840.236399"
        return json.dumps({
            "success": True,
            "intake": {
                "queued": True,
                "result": {"state": "judgment_requested"},
                "wiki_projection": {
                    "source_report": {"public_url": "https://example.com/sourcelab/sources/github/nashsu-llm_wiki"},
                    "judgment_queue": {"public_url": "https://example.com/sourcelab/queue/judgmentrequested"},
                },
            },
        })

    monkeypatch.setattr(source_lab, "handle_source_lab_analyze_url", fake_analyze)
    gateway = _Gateway()
    result = source_lab._on_pre_gateway_dispatch(event=_event(), gateway=gateway)
    assert result["action"] == "skip"
    assert result["reason"] == "source_lab_slack_auto_intake"

    await asyncio.sleep(0.1)
    assert gateway.notices
    assert "SourceLab 자동 접수 완료" in gateway.notices[0][1]
    assert "상태: judgment_requested" in gateway.notices[0][1]


def test_pre_gateway_dispatch_ignores_other_channels(monkeypatch):
    monkeypatch.setenv("SOURCELAB_SLACK_AUTO_INTAKE", "true")
    monkeypatch.setenv("SOURCELAB_SLACK_AUTO_CHANNELS", "OTHER")
    assert source_lab._on_pre_gateway_dispatch(event=_event(), gateway=_Gateway()) is None

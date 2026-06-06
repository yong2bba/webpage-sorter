"""Tests for SourceLab social collection handler integration."""

import json


def test_collect_social_handler_returns_items_and_seed_analyses(monkeypatch):
    import source_lab
    from source_lab_core.social_collectors import SocialRawItem

    def fake_collect(source, target, *, limit=10):
        assert source == "x_search"
        assert target == "AI agent"
        assert limit == 2
        return [
            SocialRawItem(
                source="x",
                source_type="x_search",
                url="https://x.com/OpenAI/status/1",
                title="@OpenAI: Memory update",
                text="We researched memory.",
                author="@OpenAI",
            )
        ]

    monkeypatch.setattr(source_lab, "collect_social_items", fake_collect)

    payload = json.loads(
        source_lab.handle_source_lab_collect_social(
            {"source": "x_search", "target": "AI agent", "limit": 2, "run_intake": False}
        )
    )

    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["items"][0]["url"] == "https://x.com/OpenAI/status/1"
    assert payload["analyses"][0]["url"] == "https://x.com/OpenAI/status/1"
    assert payload["analyses"][0]["summary"].startswith("social: x_search")
    assert "intake_results" not in payload


def test_collect_social_handler_can_feed_items_to_intake(monkeypatch, tmp_path):
    import source_lab
    from source_lab_core.social_collectors import SocialRawItem

    monkeypatch.setattr(
        source_lab,
        "collect_social_items",
        lambda source, target, *, limit=10: [
            SocialRawItem(
                source="reddit",
                source_type="reddit_search",
                url="https://www.reddit.com/r/AI_Agents/comments/1taei9m/stop_building_ai_agents/",
                title="Stop building AI agents.",
                text="Long operator post.",
                author="u/builder42",
            )
        ],
    )

    payload = json.loads(
        source_lab.handle_source_lab_collect_social(
            {
                "source": "reddit_search",
                "target": "AI agent",
                "limit": 1,
                "run_intake": True,
                "enqueue": True,
                "db_path": str(tmp_path / "queue.db"),
            }
        )
    )

    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["intake_results"][0]["success"] is True
    assert payload["intake_results"][0]["result"]["canonical_url"].startswith("https://www.reddit.com/")

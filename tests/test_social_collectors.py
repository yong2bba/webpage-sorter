"""Tests for SourceLab social-source collector adapters."""

import json


def _completed(payload: dict):
    class Result:
        def __init__(self, stdout: str):
            self.returncode = 0
            self.stderr = ""
            self.stdout = stdout

    return Result(json.dumps(payload))


def test_twitter_search_normalizes_cli_json_to_raw_items():
    from source_lab_core.social_collectors import run_twitter_search

    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _completed(
            {
                "ok": True,
                "data": [
                    {
                        "id": "2062567556524003631",
                        "text": "We’ve been researching new ways for ChatGPT memory.",
                        "author": {"screenName": "OpenAI", "name": "OpenAI"},
                        "createdAtISO": "2026-06-05T19:00:00+00:00",
                        "metrics": {"likes": 123, "replies": 4},
                        "urls": [{"expanded_url": "https://example.com/report"}],
                    }
                ],
            }
        )

    items = run_twitter_search("AI agent", limit=3, runner=runner)

    assert calls == [["twitter", "search", "AI agent", "-n", "3", "--json"]]
    assert len(items) == 1
    item = items[0]
    assert item.source == "x"
    assert item.source_type == "x_search"
    assert item.url == "https://x.com/OpenAI/status/2062567556524003631"
    assert item.title == "@OpenAI: We’ve been researching new ways for ChatGPT memory."
    assert item.text == "We’ve been researching new ways for ChatGPT memory."
    assert item.author == "@OpenAI"
    assert item.raw_meta["metrics"]["likes"] == 123


def test_reddit_search_normalizes_listing_children_to_raw_items():
    from source_lab_core.social_collectors import run_reddit_search

    def runner(cmd, **kwargs):
        assert cmd == ["rdt", "search", "AI agent", "--limit", "2", "--json"]
        return _completed(
            {
                "ok": True,
                "data": {
                    "kind": "Listing",
                    "data": {
                        "children": [
                            {
                                "kind": "t3",
                                "data": {
                                    "name": "t3_1taei9m",
                                    "subreddit": "AI_Agents",
                                    "title": "Stop building AI agents.",
                                    "selftext": "Long post body",
                                    "author": "builder42",
                                    "permalink": "/r/AI_Agents/comments/1taei9m/stop_building_ai_agents/",
                                    "created_utc": 1778529387.0,
                                    "score": 1506,
                                    "num_comments": 327,
                                },
                            }
                        ]
                    },
                },
            }
        )

    items = run_reddit_search("AI agent", limit=2, runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.source == "reddit"
    assert item.source_type == "reddit_search"
    assert item.url == "https://www.reddit.com/r/AI_Agents/comments/1taei9m/stop_building_ai_agents/"
    assert item.title == "Stop building AI agents."
    assert item.text == "Long post body"
    assert item.author == "u/builder42"
    assert item.raw_meta["score"] == 1506
    assert item.raw_meta["num_comments"] == 327


def test_reddit_read_strips_t3_prefix_and_includes_comment_text():
    from source_lab_core.social_collectors import run_reddit_read

    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _completed(
            {
                "ok": True,
                "data": [
                    {
                        "kind": "Listing",
                        "data": {
                            "children": [
                                {
                                    "kind": "t3",
                                    "data": {
                                        "name": "t3_1txxgpq",
                                        "subreddit": "LocalLLaMA",
                                        "title": "OpenLumara",
                                        "selftext": "Post body",
                                        "author": "rose22",
                                        "permalink": "/r/LocalLLaMA/comments/1txxgpq/openlumara/",
                                        "score": 108,
                                        "num_comments": 58,
                                    },
                                }
                            ]
                        },
                    },
                    {
                        "kind": "Listing",
                        "data": {
                            "children": [
                                {"kind": "t1", "data": {"author": "reader1", "body": "Great local-first design."}},
                                {"kind": "more", "data": {}},
                            ]
                        },
                    },
                ],
            }
        )

    item = run_reddit_read("t3_1txxgpq", runner=runner)

    assert calls == [["rdt", "read", "1txxgpq", "--json"]]
    assert item.source_type == "reddit_read"
    assert item.text == "Post body\n\nTop comments:\n- u/reader1: Great local-first design."
    assert item.raw_meta["comments_fetched"] == 1


def test_raw_item_to_analysis_text_and_seed_analysis_are_sorter_ready():
    from source_lab_core.social_collectors import SocialRawItem, build_seed_analysis, raw_item_to_analysis_text

    item = SocialRawItem(
        source="x",
        source_type="x_search",
        url="https://x.com/OpenAI/status/1",
        title="@OpenAI: Memory update",
        text="We researched memory.",
        author="@OpenAI",
        published_at="2026-06-05T19:00:00+00:00",
        raw_meta={"metrics": {"likes": 10}},
    )

    text = raw_item_to_analysis_text(item)
    analysis = build_seed_analysis(item)

    assert "Source: x / x_search" in text
    assert "Author: @OpenAI" in text
    assert analysis["url"] == item.url
    assert analysis["content_type"] == "technical"
    assert analysis["confidence"] == 0.55
    assert "social_source:x" in analysis["evidence"]
    assert "social: x_search" in analysis["summary"]

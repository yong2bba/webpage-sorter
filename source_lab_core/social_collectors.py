"""Social-source collectors for SourceLab.

These adapters normalize authenticated CLI output from X/Twitter and Reddit into
one small raw-item contract that the existing SourceLab sorter/analyzer can
consume. They intentionally avoid credential storage: callers provide credentials
through the upstream CLIs' normal environment/config files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class SocialRawItem:
    """Normalized social source item before low-level sorter analysis."""

    source: str
    source_type: str
    url: str
    title: str
    text: str
    author: str | None = None
    published_at: str | None = None
    collected_at: str = ""
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.collected_at:
            self.collected_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SocialCollectorError(RuntimeError):
    """Raised when an upstream social CLI fails or returns an unexpected shape."""


_SOCIAL_ENV_KEYS = {
    "AUTH_TOKEN",
    "CT0",
    "TWITTER_AUTH_TOKEN",
    "TWITTER_CT0",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "HOME",
    "PATH",
}


def _default_social_env_file() -> Path:
    explicit = os.environ.get("SOURCELAB_SOCIAL_ENV_FILE") or os.environ.get("WEBPAGE_SORTER_SOCIAL_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".config" / "sourcelab" / "social.env"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in _SOCIAL_ENV_KEYS:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def social_subprocess_env(*, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build subprocess env for social CLIs from process env plus local env file.

    The optional env file keeps X cookies and rdt-cli config location out of git
    while still letting Hermes/plugin subprocesses use them without restarting
    with global environment variables.
    """

    env = dict(base_env or os.environ)
    env.update(_parse_env_file(_default_social_env_file()))
    if env.get("TWITTER_AUTH_TOKEN") and not env.get("AUTH_TOKEN"):
        env["AUTH_TOKEN"] = env["TWITTER_AUTH_TOKEN"]
    if env.get("TWITTER_CT0") and not env.get("CT0"):
        env["CT0"] = env["TWITTER_CT0"]
    return env


def _default_runner(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - command list is fixed by adapter functions.
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=kwargs.pop("timeout", 60),
        env=kwargs.pop("env", social_subprocess_env()),
        **kwargs,
    )


def _load_cli_json(result: subprocess.CompletedProcess[str], *, tool: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise SocialCollectorError(f"{tool} failed with exit {result.returncode}: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SocialCollectorError(f"{tool} returned non-JSON output: {exc}") from exc
    if not isinstance(payload, dict):
        raise SocialCollectorError(f"{tool} JSON root must be an object")
    if payload.get("ok") is not True:
        raw_error = payload.get("error")
        error = raw_error if isinstance(raw_error, dict) else {}
        code = error.get("code") or "unknown_error"
        message = error.get("message") or "upstream CLI returned ok=false"
        raise SocialCollectorError(f"{tool} {code}: {message}")
    return payload


def _short_title(prefix: str, text: str, *, limit: int = 96) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return f"{prefix}: {text[:limit]}" if text else prefix


def _reddit_url(item: dict[str, Any]) -> str:
    permalink = str(item.get("permalink") or "").strip()
    if permalink.startswith("http://") or permalink.startswith("https://"):
        return permalink
    if permalink.startswith("/"):
        return f"https://www.reddit.com{permalink}"
    name = str(item.get("name") or "")
    subreddit = str(item.get("subreddit") or "")
    if name.startswith("t3_") and subreddit:
        return f"https://www.reddit.com/r/{subreddit}/comments/{name[3:]}/"
    return "https://www.reddit.com/"


def _reddit_children(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    listing_data = data.get("data") if isinstance(data.get("data"), dict) else data
    children = listing_data.get("children") if isinstance(listing_data, dict) else []
    return children if isinstance(children, list) else []


def _reddit_post_to_item(post: dict[str, Any], *, source_type: str, comments: list[dict[str, Any]] | None = None) -> SocialRawItem:
    title = str(post.get("title") or "").strip()
    text = str(post.get("selftext") or post.get("url") or "").strip()
    normalized_comments: list[str] = []
    for comment in comments or []:
        if comment.get("kind") != "t1":
            continue
        raw_cdata = comment.get("data")
        cdata = raw_cdata if isinstance(raw_cdata, dict) else {}
        body = str(cdata.get("body") or "").strip()
        author = str(cdata.get("author") or "").strip()
        if body:
            normalized_comments.append(f"- u/{author}: {body}" if author else f"- {body}")
    if normalized_comments:
        text = f"{text}\n\nTop comments:\n" + "\n".join(normalized_comments)
    author = str(post.get("author") or "").strip()
    return SocialRawItem(
        source="reddit",
        source_type=source_type,
        url=_reddit_url(post),
        title=title,
        text=text,
        author=f"u/{author}" if author else None,
        published_at=str(post.get("created_utc")) if post.get("created_utc") is not None else None,
        raw_meta={
            "id": post.get("name"),
            "subreddit": post.get("subreddit"),
            "score": post.get("score"),
            "num_comments": post.get("num_comments"),
            "comments_fetched": len(normalized_comments),
        },
    )


def run_twitter_search(query: str, *, limit: int = 10, runner: Runner = _default_runner) -> list[SocialRawItem]:
    result = runner(["twitter", "search", query, "-n", str(limit), "--json"])
    payload = _load_cli_json(result, tool="twitter search")
    return [_twitter_tweet_to_item(item, source_type="x_search") for item in _as_list(payload.get("data"))]


def run_twitter_user_posts(username: str, *, limit: int = 10, runner: Runner = _default_runner) -> list[SocialRawItem]:
    user = username.lstrip("@")
    result = runner(["twitter", "user-posts", user, "-n", str(limit), "--json"])
    payload = _load_cli_json(result, tool="twitter user-posts")
    return [_twitter_tweet_to_item(item, source_type="x_user_posts") for item in _as_list(payload.get("data"))]


def run_twitter_tweet(tweet_url_or_id: str, *, runner: Runner = _default_runner) -> list[SocialRawItem]:
    result = runner(["twitter", "tweet", tweet_url_or_id, "--json"])
    payload = _load_cli_json(result, tool="twitter tweet")
    return [_twitter_tweet_to_item(item, source_type="x_tweet") for item in _as_list(payload.get("data"))]


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _twitter_tweet_to_item(item: dict[str, Any], *, source_type: str) -> SocialRawItem:
    raw_author = item.get("author")
    author = raw_author if isinstance(raw_author, dict) else {}
    screen_name = str(author.get("screenName") or author.get("username") or "").strip()
    tweet_id = str(item.get("id") or "").strip()
    url = f"https://x.com/{screen_name}/status/{tweet_id}" if screen_name and tweet_id else "https://x.com/"
    text = str(item.get("text") or "").strip()
    return SocialRawItem(
        source="x",
        source_type=source_type,
        url=url,
        title=_short_title(f"@{screen_name}" if screen_name else "X post", text),
        text=text,
        author=f"@{screen_name}" if screen_name else None,
        published_at=str(item.get("createdAtISO") or item.get("createdAt") or "") or None,
        raw_meta={
            "id": tweet_id or None,
            "metrics": item.get("metrics") if isinstance(item.get("metrics"), dict) else {},
            "urls": item.get("urls") if isinstance(item.get("urls"), list) else [],
            "media": item.get("media") if isinstance(item.get("media"), list) else [],
            "lang": item.get("lang"),
        },
    )


def run_reddit_search(query: str, *, limit: int = 10, runner: Runner = _default_runner) -> list[SocialRawItem]:
    result = runner(["rdt", "search", query, "--limit", str(limit), "--json"])
    payload = _load_cli_json(result, tool="rdt search")
    items = []
    for child in _reddit_children(payload):
        data = child.get("data") if isinstance(child.get("data"), dict) else {}
        if data:
            items.append(_reddit_post_to_item(data, source_type="reddit_search"))
    return items


def run_reddit_subreddit(subreddit: str, *, limit: int = 10, runner: Runner = _default_runner) -> list[SocialRawItem]:
    result = runner(["rdt", "sub", subreddit, "--limit", str(limit), "--json"])
    payload = _load_cli_json(result, tool="rdt sub")
    items = []
    for child in _reddit_children(payload):
        data = child.get("data") if isinstance(child.get("data"), dict) else {}
        if data:
            items.append(_reddit_post_to_item(data, source_type="reddit_subreddit"))
    return items


def run_reddit_read(post_id: str, *, runner: Runner = _default_runner) -> SocialRawItem:
    bare_id = post_id.strip()
    if bare_id.startswith("t3_"):
        bare_id = bare_id[3:]
    result = runner(["rdt", "read", bare_id, "--json"])
    payload = _load_cli_json(result, tool="rdt read")
    listings = payload.get("data") if isinstance(payload.get("data"), list) else []
    if not listings:
        raise SocialCollectorError("rdt read returned no listings")
    post_children = listings[0].get("data", {}).get("children", []) if isinstance(listings[0], dict) else []
    if not post_children:
        raise SocialCollectorError("rdt read returned no post children")
    post = post_children[0].get("data") if isinstance(post_children[0].get("data"), dict) else {}
    comment_children: list[dict[str, Any]] = []
    if len(listings) > 1 and isinstance(listings[1], dict):
        maybe_comments = listings[1].get("data", {}).get("children", [])
        if isinstance(maybe_comments, list):
            comment_children = [c for c in maybe_comments if isinstance(c, dict)]
    return _reddit_post_to_item(post, source_type="reddit_read", comments=comment_children)


def raw_item_to_analysis_text(item: SocialRawItem, *, max_chars: int = 12_000) -> str:
    """Render a raw social item as plain text for low-level sorter analysis."""

    lines = [
        f"Source: {item.source} / {item.source_type}",
        f"URL: {item.url}",
        f"Title: {item.title}",
    ]
    if item.author:
        lines.append(f"Author: {item.author}")
    if item.published_at:
        lines.append(f"Published: {item.published_at}")
    lines.extend(["", item.text.strip()])
    return "\n".join(lines).strip()[:max_chars]


def build_seed_analysis(item: SocialRawItem) -> dict[str, Any]:
    """Build a conservative sorter-ready analysis without an LLM call.

    This lets collector jobs enqueue social items even when the LLM analyzer is
    unavailable. Confidence is intentionally low so higher-level judgment remains
    available for interesting items.
    """

    summary_text = re.sub(r"\s+", " ", item.text).strip()[:240]
    return {
        "url": item.url,
        "title": item.title[:200],
        "content_type": "technical",
        "confidence": 0.55,
        "signals": [],
        "risk_flags": ["insufficient_source"] if not item.text.strip() else [],
        "evidence": [f"social_source:{item.source}", f"source_type:{item.source_type}"],
        "summary": f"social: {item.source_type} — {summary_text}",
        "source_raw_item": item.to_dict(),
    }


def collect_social_items(source: str, target: str, *, limit: int = 10, runner: Runner = _default_runner) -> list[SocialRawItem]:
    """Collect social items by adapter name.

    Supported sources: x_search, x_user_posts, x_tweet, reddit_search,
    reddit_subreddit, reddit_read.
    """

    if source == "x_search":
        return run_twitter_search(target, limit=limit, runner=runner)
    if source == "x_user_posts":
        return run_twitter_user_posts(target, limit=limit, runner=runner)
    if source == "x_tweet":
        return run_twitter_tweet(target, runner=runner)
    if source == "reddit_search":
        return run_reddit_search(target, limit=limit, runner=runner)
    if source == "reddit_subreddit":
        return run_reddit_subreddit(target, limit=limit, runner=runner)
    if source == "reddit_read":
        return [run_reddit_read(target, runner=runner)]
    raise ValueError(f"unsupported social source: {source}")


__all__ = [
    "SocialCollectorError",
    "SocialRawItem",
    "build_seed_analysis",
    "collect_social_items",
    "raw_item_to_analysis_text",
    "run_reddit_read",
    "run_reddit_search",
    "run_reddit_subreddit",
    "run_twitter_search",
    "run_twitter_tweet",
    "run_twitter_user_posts",
    "social_subprocess_env",
]

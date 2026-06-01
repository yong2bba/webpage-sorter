"""Low-level URL analysis for SourceLab.

This module is deliberately small and plugin-local: it fetches a URL, asks the
configured auxiliary LLM task to extract routing signals, then normalizes the
result to the existing SourceLab analysis contract consumed by ``intake_url``.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .contracts import is_known_content_type
from .intake import canonicalize

AUXILIARY_TASK = "source_lab_low_analysis"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_PROVIDER = "openrouter"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_CHARS = 12_000

_ALLOWED_SIGNALS = {
    "investment_advice",
    "financial_forecast",
    "legal_interpretation",
    "regulatory_compliance",
    "personal_data",
    "medical_advice",
    "health_recommendation",
}
_ALLOWED_RISK_FLAGS = {
    "contradicts_known_facts",
    "unverified_claim",
    "conflicting_sources",
    "bias_detected",
    "insufficient_source",
}


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor with script/style suppression."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - HTMLParser signature
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1
        elif tag.lower() in {"p", "br", "li", "div", "section", "article", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag.lower() in {"p", "li", "div", "section", "article"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape(" ".join(self.parts))
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def _validate_fetchable_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an http(s) URL with a host")


def fetch_url_text(url: str, *, timeout: float = 20.0, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Fetch a URL and return plain-ish text capped to ``max_chars``."""

    _validate_fetchable_url(url)
    req = Request(
        url,
        headers={
            "User-Agent": "Hermes SourceLab/0.4 (+https://github.com/NousResearch/hermes-agent)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        },
    )
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - URL is scheme-validated above.
        raw = response.read(max_chars * 4)
        content_type = response.headers.get("content-type", "")

    charset = "utf-8"
    match = re.search(r"charset=([^;]+)", content_type, flags=re.I)
    if match:
        charset = match.group(1).strip()
    decoded = raw.decode(charset, errors="replace")

    if "html" in content_type.lower() or "<html" in decoded[:1000].lower():
        parser = _TextExtractor()
        parser.feed(decoded)
        text = parser.text()
    else:
        text = decoded.strip()
    return text[:max_chars]


def _coerce_string_list(value: Any, *, allowed: set[str] | None = None) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item:
            continue
        if allowed is not None and item not in allowed:
            continue
        out.append(item)
    return out


def _extract_response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except Exception as exc:  # pragma: no cover - defensive shape guard
        raise ValueError(f"LLM response did not include choices[0].message.content: {exc}") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response content is empty")
    return content.strip()


def _parse_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object")
    return parsed


def normalize_analysis(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize arbitrary LLM JSON to the SourceLab low-analysis contract."""

    content_type = str(payload.get("content_type") or "other").strip().lower()
    if not is_known_content_type(content_type):
        content_type = "other"

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    evidence = _coerce_string_list(payload.get("evidence"))[:8]
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = "No summary returned by low-level analyzer."

    return {
        "url": canonicalize(url),
        "content_type": content_type,
        "confidence": confidence,
        "signals": _coerce_string_list(payload.get("signals"), allowed=_ALLOWED_SIGNALS),
        "risk_flags": _coerce_string_list(payload.get("risk_flags"), allowed=_ALLOWED_RISK_FLAGS),
        "evidence": evidence,
        "summary": summary[:1200],
        "title": str(payload.get("title") or "").strip()[:200],
        "analysis_model_task": AUXILIARY_TASK,
    }


def build_low_analysis_messages(url: str, text: str) -> List[Dict[str, str]]:
    """Build a deterministic JSON-only extraction prompt."""

    return [
        {
            "role": "system",
            "content": (
                "You are SourceLab's low-level triage extractor. "
                "Return only compact JSON. Do not make final recommendations. "
                "Your job is to classify content and surface risk signals for a higher-level agent."
            ),
        },
        {
            "role": "user",
            "content": (
                "Analyze this fetched URL text for SourceLab triage.\n"
                f"URL: {url}\n\n"
                "Return exactly this JSON object shape:\n"
                "{\n"
                "  \"title\": string,\n"
                "  \"content_type\": one of [\"news\",\"opinion\",\"technical\",\"financial\",\"other\"],\n"
                "  \"confidence\": number between 0 and 1,\n"
                "  \"signals\": array containing only relevant values from "
                "[\"investment_advice\",\"financial_forecast\",\"legal_interpretation\","
                "\"regulatory_compliance\",\"personal_data\",\"medical_advice\",\"health_recommendation\"],\n"
                "  \"risk_flags\": array containing only relevant values from "
                "[\"contradicts_known_facts\",\"unverified_claim\",\"conflicting_sources\","
                "\"bias_detected\",\"insufficient_source\"],\n"
                "  \"evidence\": array of short quoted/observed evidence snippets,\n"
                "  \"summary\": concise Korean summary, max 5 sentences\n"
                "}\n\n"
                "Rules:\n"
                "- If the page includes investment, forecast, legal, privacy, medical, or conflicting claims, add the matching signal/risk flag.\n"
                "- If the fetched text is too thin, set confidence <= 0.5 and include insufficient_source.\n"
                "- Do not answer the user's domain question; only prepare triage metadata.\n\n"
                "Fetched text:\n"
                f"{text}"
            ),
        },
    ]


def analyze_url_low_level(
    url: str,
    *,
    text: str | None = None,
    fetch_timeout: float = 20.0,
    max_chars: int = DEFAULT_MAX_CHARS,
    llm_timeout: float | None = None,
    max_tokens: int = 1000,
) -> Dict[str, Any]:
    """Fetch ``url`` if needed and run the configured low-level auxiliary model."""

    canonical_url = canonicalize(url)
    fetched_text = text if text is not None else fetch_url_text(
        canonical_url,
        timeout=fetch_timeout,
        max_chars=max_chars,
    )
    if not fetched_text.strip():
        return normalize_analysis(
            canonical_url,
            {
                "content_type": "other",
                "confidence": 0.2,
                "signals": [],
                "risk_flags": ["insufficient_source"],
                "evidence": [],
                "summary": "Fetched content was empty.",
            },
        )

    from agent.auxiliary_client import call_llm  # lazy import: keep plugin import light

    response = call_llm(
        task=AUXILIARY_TASK,
        messages=build_low_analysis_messages(canonical_url, fetched_text[:max_chars]),
        temperature=0,
        max_tokens=max_tokens,
        timeout=llm_timeout,
    )
    payload = _parse_json_object(_extract_response_text(response))
    return normalize_analysis(canonical_url, payload)

"""URL intake pipeline for Source Lab (v0.3.3).

Orchestration layer — no network fetch, no LLM calls.
Analyzer and seen_urls are injected by the caller.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse, urlunparse

from .branching import BranchDecision, decide_branch


# ─── Result model ──────────────────────────────────────────────

@dataclass
class IntakeResult:
    """Result of the URL intake pipeline."""
    state: str                                    # "self_close" | "judgment_requested" | "error"
    url: str                                      # original URL
    canonical_url: str = ""                       # normalized URL
    duplicate: bool = False
    analysis: Optional[Dict[str, Any]] = None
    branch_decision: Optional[BranchDecision] = None
    errors: List[str] = field(default_factory=list)


# ─── URL validation ────────────────────────────────────────────

def _validate_url(url: str) -> List[str]:
    """Check that URL has scheme and netloc."""
    errors: List[str] = []
    if not url or not url.strip():
        errors.append("URL is empty")
        return errors
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        errors.append(f"URL has no scheme: '{url}'")
    elif parsed.scheme not in ("http", "https"):
        errors.append(f"URL has unsupported scheme: '{parsed.scheme}'")
    if not parsed.netloc:
        errors.append(f"URL has no host: '{url}'")
    return errors


# ─── Canonical normalization ───────────────────────────────────

def canonicalize(url: str) -> str:
    """Normalize URL: trim, lowercase scheme/host, strip fragment, strip trailing slash."""
    url = url.strip()
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    # strip fragment
    normalized = urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
    return normalized


# ─── Analyzer protocol ─────────────────────────────────────────

AnalyzerFn = Callable[[str], Dict[str, Any]]
"""Callable that takes a URL string and returns an analysis dict."""


# ─── Core intake function ──────────────────────────────────────

def intake_url(
    url: str,
    analyzer: AnalyzerFn,
    seen_urls: Optional[Set[str]] = None,
    confidence_threshold: float = 0.8,
    requested_by: str = "source_lab",
) -> IntakeResult:
    """Process a URL through the intake pipeline.

    Steps:
    1. Validate URL format
    2. Canonicalize URL
    3. Check for duplicates (via seen_urls set)
    4. Call analyzer
    5. Run branching decision

    Args:
        url: Raw URL string.
        analyzer: Callable that fetches/parses and returns analysis dict.
        seen_urls: Optional set of already-processed canonical URLs.
        confidence_threshold: Passed to decide_branch().
        requested_by: Agent ID for judgment request payloads.

    Returns:
        IntakeResult with state, analysis, branch_decision, errors.
    """
    # 1. Validate
    errors = _validate_url(url)
    if errors:
        return IntakeResult(state="error", url=url, errors=errors)

    # 2. Canonicalize
    canonical = canonicalize(url)

    # 3. Duplicate check
    if seen_urls is not None and canonical in seen_urls:
        return IntakeResult(
            state="duplicate",
            url=url,
            canonical_url=canonical,
            duplicate=True,
        )

    # 4. Analyze
    try:
        analysis = analyzer(canonical)
    except Exception as e:
        return IntakeResult(
            state="error",
            url=url,
            canonical_url=canonical,
            errors=[f"Analyzer failed: {type(e).__name__}: {e}"],
        )

    # 5. Branch
    branch = decide_branch(
        analysis,
        confidence_threshold=confidence_threshold,
        requested_by=requested_by,
    )

    return IntakeResult(
        state=branch.state,
        url=url,
        canonical_url=canonical,
        duplicate=False,
        analysis=analysis,
        branch_decision=branch,
    )

"""OtterWiki Markdown projection renderer for SourceLab.

The renderer treats PostgreSQL as the source of truth and writes a safe,
human-readable Markdown projection into an OtterWiki Git repository.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from psycopg.types.json import Jsonb


def _safe_text(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _list_lines(values: Any) -> str:
    if not values:
        return "- -"
    if isinstance(values, str):
        values = [values]
    return "\n".join(f"- `{_safe_text(v)}`" for v in values)


def _slug_part(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9._/-]+", "-", value)
    value = value.strip("-_/.")
    return value or "unknown"


def _now_kst_string() -> str:
    # Avoid zoneinfo dependency in older environments; KST has no DST.
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def markdown_path_for_source(state: Dict[str, Any]) -> str:
    """Return canonical lower-case OtterWiki markdown path for a source row."""
    source_type = _safe_text(state.get("source_type"), "other")
    canonical_key = _safe_text(state.get("canonical_key"), "source:unknown")
    canonical_url = _safe_text(state.get("canonical_url"), "")

    if canonical_key.lower().startswith("github:"):
        repo = canonical_key.split(":", 1)[1]
        repo = repo.replace("/", "-")
        return f"sourcelab/sources/github/{_slug_part(repo)}.md"

    if source_type == "github_repo" and "github.com" in canonical_url:
        parts = [p for p in canonical_url.split("github.com/", 1)[-1].split("/") if p]
        if len(parts) >= 2:
            return f"sourcelab/sources/github/{_slug_part(parts[0] + '-' + parts[1])}.md"

    host = "source"
    if "://" in canonical_url:
        host = canonical_url.split("://", 1)[1].split("/", 1)[0]
    slug = _slug_part(canonical_key.replace(":", "-"))
    return f"sourcelab/sources/articles/{_slug_part(host)}-{slug}.md"


def public_url_for_path(markdown_path: str, wiki_base_url: str = "") -> str:
    page_path = markdown_path[:-3] if markdown_path.endswith(".md") else markdown_path
    return f"{wiki_base_url.rstrip('/')}/{page_path}"


def build_source_report_markdown(
    state: Dict[str, Any], *, generated_at: Optional[str] = None
) -> str:
    """Build safe, human-readable SourceLab source report Markdown."""
    generated_at = generated_at or _now_kst_string()
    title = _safe_text(state.get("title"), "SourceLab Source")
    canonical_url = _safe_text(state.get("canonical_url"))
    canonical_key = _safe_text(state.get("canonical_key"))
    source_type = _safe_text(state.get("source_type"))
    final_action = state.get("action")
    status = _safe_text(final_action or state.get("latest_branch_state") or state.get("judgment_status"))
    priority = _safe_text(state.get("priority"))
    summary = _safe_text(state.get("analysis_summary") or state.get("source_summary"))
    collector_reason = _safe_text(state.get("branch_reason_text"))
    branch_reason = _safe_text(state.get("branch_reason"))
    decision = _safe_text(state.get("decision"), "pending")
    action = _safe_text(state.get("action"), "pending")
    decision_reason = _safe_text(state.get("decision_reason"), "아직 최종 판단 없음")
    confidence = _safe_text(state.get("analysis_confidence"))
    content_type = _safe_text(state.get("content_type"))
    markdown_path = markdown_path_for_source(state)

    return f"""# {title}

- 위키 발행일: {generated_at}
- 원본 URL: {canonical_url}
- Canonical ID: `{canonical_key}`
- Projection path: `{markdown_path}`

## 접수 정보

| 항목 | 내용 |
|---|---|
| 유형 | `{source_type}` |
| 콘텐츠 타입 | `{content_type}` |
| 현재 상태 | `{status}` |
| 우선순위 | `{priority}` |
| 분석 confidence | `{confidence}` |

## 1차 요약

{summary}

## 근거 / 신호

### Signals
{_list_lines(state.get('signals'))}

### Evidence
{_list_lines(state.get('evidence'))}

## 리스크

{_list_lines(state.get('risk_flags'))}

## Collector 판단

- 분류: `{status}`
- Branch reason: `{branch_reason}`
- 판단 이유: {collector_reason}

## AI agent 판단

- Decision: `{decision}`
- Action: `{action}`
- 이유: {decision_reason}

## 다음 액션

- `archive`: 재사용 가치가 있으면 SourceLab archive/reference 후보로 유지
- `pending`: judgment queue에 남아 있으면 human/senior-agent 판단 후 재생성
- `reanalyze`: 분석 조건이 바뀌면 collector가 다시 분석

## 원본 아티팩트

- Raw HTML / 긴 JSON / screenshot binary는 위키에 저장하지 않음
- PostgreSQL `artifacts`와 외부 artifact URI를 source of truth로 사용
"""


def build_judgment_queue_markdown(rows: list[Dict[str, Any]], *, generated_at: Optional[str] = None) -> str:
    """Build read-only Markdown projection of pending SourceLab judgment queue."""
    generated_at = generated_at or _now_kst_string()
    if rows:
        table_rows = []
        for row in rows:
            request_id = _safe_text(row.get("request_id"))
            source_type = _safe_text(row.get("source_type"))
            title = _safe_text(row.get("title") or row.get("canonical_key"))
            confidence = _safe_text(row.get("confidence"))
            reason = _safe_text(row.get("branch_reason"))
            summary = _safe_text(row.get("content_summary"))
            url = _safe_text(row.get("canonical_url"))
            table_rows.append(
                f"| `{request_id}` | `{source_type}` | {title} | `{confidence}` | `{reason}` | {summary}<br>{url} |"
            )
        body = "\n".join(table_rows)
    else:
        body = "| - | - | - | - | - | 현재 pending judgment 없음 |"

    return f"""# Judgment Queue

- 마지막 갱신: {generated_at}

현재 이 위키 페이지는 표시용입니다. 실제 상태 원장은 PostgreSQL `sourcelab.judgment_requests`와 `sourcelab.v_judgment_queue`입니다.

| ID | 유형 | 제목 | Confidence | 이유 | 다음 액션 |
|---|---|---|---:|---|---|
{body}
"""


class WikiProjectionRenderer:
    """Render SourceLab PostgreSQL state into an OtterWiki repository."""

    def __init__(
        self,
        repository: Any,
        *,
        wiki_repo_path: str | Path,
        wiki_base_url: str = "",
    ) -> None:
        self.repository = repository
        self.wiki_repo_path = Path(wiki_repo_path)
        self.wiki_base_url = wiki_base_url

    def render_source_report(self, source_id: str, *, commit: bool = False) -> Dict[str, Any]:
        state = self._state_for_source_id(source_id)
        markdown_path = markdown_path_for_source(state)
        public_url = public_url_for_path(markdown_path, self.wiki_base_url)
        markdown = build_source_report_markdown(state)
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

        output_path = self.wiki_repo_path / markdown_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")

        git_commit_sha = None
        status = "rendered"
        if commit:
            git_commit_sha = self._commit(markdown_path, f"render sourcelab source {state.get('canonical_key')}")
            status = "committed"

        projection = self._record_projection(
            source_id=source_id,
            judgment_request_id=state.get("open_judgment_request_id"),
            projection_kind="source_report",
            markdown_path=markdown_path,
            public_url=public_url,
            title=_safe_text(state.get("title"), "SourceLab Source"),
            status=status,
            rendered_from_analysis_id=state.get("latest_analysis_id"),
            rendered_from_decision_id=state.get("latest_decision_id"),
            content_sha256=digest,
            git_commit_sha=git_commit_sha,
        )
        return projection

    def render_judgment_queue(self, *, commit: bool = False) -> Dict[str, Any]:
        rows = self.repository._conn.execute("SELECT * FROM sourcelab.v_judgment_queue").fetchall()
        rows = [self.repository._normalize_row(row) for row in rows]
        markdown_path = "sourcelab/queue/judgmentrequested.md"
        public_url = public_url_for_path(markdown_path, self.wiki_base_url)
        markdown = build_judgment_queue_markdown(rows)
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

        output_path = self.wiki_repo_path / markdown_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")

        git_commit_sha = None
        status = "rendered"
        if commit:
            git_commit_sha = self._commit(markdown_path, "render sourcelab judgment queue")
            status = "committed"

        return self._record_projection(
            source_id=None,
            judgment_request_id=None,
            projection_kind="judgment_queue",
            markdown_path=markdown_path,
            public_url=public_url,
            title="Judgment Queue",
            status=status,
            rendered_from_analysis_id=None,
            rendered_from_decision_id=None,
            content_sha256=digest,
            git_commit_sha=git_commit_sha,
        )

    def _state_for_source_id(self, source_id: str) -> Dict[str, Any]:
        row = self.repository._conn.execute(
            "SELECT * FROM sourcelab.v_latest_source_state WHERE source_id = %s",
            (source_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"source not found: {source_id}")
        state = self.repository._normalize_row(row)
        analysis = self.repository._conn.execute(
            """
            SELECT evidence FROM sourcelab.analyses
            WHERE source_id = %s
            ORDER BY analyzed_at DESC, created_at DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        if analysis and analysis.get("evidence") is not None:
            state["evidence"] = analysis["evidence"]
        return state

    def _record_projection(
        self,
        *,
        source_id: Optional[str],
        judgment_request_id: Optional[str],
        projection_kind: str,
        markdown_path: str,
        public_url: str,
        title: str,
        status: str,
        rendered_from_analysis_id: Optional[str],
        rendered_from_decision_id: Optional[str],
        content_sha256: str,
        git_commit_sha: Optional[str],
    ) -> Dict[str, Any]:
        with self.repository._conn.transaction():
            row = self.repository._conn.execute(
                """
                INSERT INTO sourcelab.wiki_projections (
                    source_id,
                    judgment_request_id,
                    projection_kind,
                    wiki_base_url,
                    markdown_path,
                    public_url,
                    title,
                    status,
                    rendered_from_analysis_id,
                    rendered_from_decision_id,
                    content_sha256,
                    git_commit_sha,
                    rendered_at,
                    committed_at,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(),
                        CASE WHEN %s = 'committed' THEN now() ELSE NULL END,
                        %s)
                ON CONFLICT (markdown_path) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    judgment_request_id = EXCLUDED.judgment_request_id,
                    public_url = EXCLUDED.public_url,
                    title = EXCLUDED.title,
                    status = EXCLUDED.status,
                    rendered_from_analysis_id = EXCLUDED.rendered_from_analysis_id,
                    rendered_from_decision_id = EXCLUDED.rendered_from_decision_id,
                    content_sha256 = EXCLUDED.content_sha256,
                    git_commit_sha = EXCLUDED.git_commit_sha,
                    rendered_at = EXCLUDED.rendered_at,
                    committed_at = EXCLUDED.committed_at,
                    error_message = NULL,
                    metadata = sourcelab.wiki_projections.metadata || EXCLUDED.metadata
                RETURNING *
                """,
                (
                    source_id,
                    judgment_request_id,
                    projection_kind,
                    self.wiki_base_url,
                    markdown_path,
                    public_url,
                    title,
                    status,
                    rendered_from_analysis_id,
                    rendered_from_decision_id,
                    content_sha256,
                    git_commit_sha,
                    status,
                    Jsonb({"renderer": "source_lab_core.wiki_projection"}),
                ),
            ).fetchone()
        return self.repository._normalize_row(row)

    def _commit(self, markdown_path: str, message: str) -> str:
        subprocess.run(["git", "add", markdown_path], cwd=self.wiki_repo_path, check=True)
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=self.wiki_repo_path
        )
        if diff.returncode == 0:
            existing = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.wiki_repo_path,
                check=True,
                text=True,
                capture_output=True,
            )
            return existing.stdout.strip()
        subprocess.run(["git", "commit", "-m", message], cwd=self.wiki_repo_path, check=True)
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=self.wiki_repo_path,
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip()

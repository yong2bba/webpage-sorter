"""SQLite-only demo CLI for Webpage Sorter.

This intentionally avoids network and LLM calls so a fresh checkout can show the
triage/queue/projection mechanics with only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from source_lab_core.intake import intake_url
from source_lab_core.queue_storage import QueueStorage
from source_lab_core.wiki_projection import build_judgment_queue_markdown, build_source_report_markdown, markdown_path_for_source


def _model_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _model_to_dict(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _model_to_dict(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_model_to_dict(item) for item in value]
    return value


def _default_analysis(url: str, confidence: float) -> dict[str, Any]:
    parsed = urlparse(url)
    is_github = parsed.netloc.lower() == "github.com"
    name = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else parsed.netloc
    return {
        "url": url,
        "title": name or url,
        "content_type": "technical" if is_github else "article",
        "confidence": confidence,
        "signals": ["open_source_tool"] if is_github else ["webpage"],
        "risk_flags": [],
        "evidence": ["SQLite demo uses caller-provided URL metadata only."],
        "summary": f"SQLite-only demo analysis for {url}.",
        "key_claims": [],
        "extracted_entities": [],
    }


def _state_for_projection(result, analysis: dict[str, Any]) -> dict[str, Any]:
    branch = _model_to_dict(result.branch_decision) if result.branch_decision else {}
    return {
        "source_type": "github_repo" if "github.com/" in result.canonical_url else "webpage",
        "canonical_key": _canonical_key(result.canonical_url),
        "canonical_url": result.canonical_url,
        "title": analysis.get("title") or result.canonical_url,
        "source_summary": analysis.get("summary") or "",
        "analysis_summary": analysis.get("summary") or "",
        "signals": analysis.get("signals") or [],
        "risk_flags": analysis.get("risk_flags") or [],
        "latest_branch_state": result.state,
        "branch_reason": branch.get("reason"),
        "priority": branch.get("priority"),
        "decision": "pending" if result.state == "judgment_requested" else "self_close",
        "action": "pending" if result.state == "judgment_requested" else "close",
        "decision_reason": branch.get("summary") or branch.get("reason") or "SQLite demo decision.",
    }


def _canonical_key(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower() == "github.com":
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 2:
            return f"github:{parts[0]}/{parts[1]}"
    return f"url:{url}"


def _write_projection(out_dir: Path, result, analysis: dict[str, Any], storage: QueueStorage) -> dict[str, str]:
    state = _state_for_projection(result, analysis)
    source_rel = markdown_path_for_source(state)
    source_path = out_dir / source_rel
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(build_source_report_markdown(state), encoding="utf-8")

    pending = []
    for row in storage.list_pending():
        payload = json.loads(row["payload_json"])
        pending.append(
            {
                "request_id": row["request_id"],
                "source_type": "webpage",
                "title": payload.get("title") or payload.get("source_url"),
                "confidence": payload.get("confidence"),
                "branch_reason": payload.get("reason") or payload.get("branch_reason"),
                "content_summary": payload.get("reason") or payload.get("summary") or "",
                "canonical_url": payload.get("source_url"),
            }
        )
    queue_rel = "sourcelab/queue/judgmentrequested.md"
    queue_path = out_dir / queue_rel
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(build_judgment_queue_markdown(pending), encoding="utf-8")
    return {"source_report": source_rel, "judgment_queue": queue_rel}


def command_demo(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out_dir)
    analysis = _default_analysis(args.url, args.confidence)
    storage = QueueStorage(str(db_path))
    try:
        result = intake_url(
            args.url,
            analyzer=lambda _url: analysis,
            confidence_threshold=args.confidence_threshold,
            requested_by="webpage_sorter_cli",
        )
        queued = False
        judgment_payload = result.branch_decision.judgment_request_payload if result.branch_decision else None
        if result.state == "judgment_requested" and judgment_payload:
            try:
                storage.save(judgment_payload)
                queued = True
            except ValueError as exc:
                if "duplicate" not in str(exc):
                    raise
        projection = _write_projection(out_dir, result, analysis, storage)
        print(json.dumps({
            "success": result.state not in {"error"},
            "url": result.url,
            "canonical_url": result.canonical_url,
            "state": result.state,
            "queued": queued,
            "storage_backend": "sqlite",
            "db_path": str(db_path),
            "out_dir": str(out_dir),
            "projection": projection,
        }, ensure_ascii=False))
        return 0
    finally:
        storage.close()


def command_queue(args: argparse.Namespace) -> int:
    storage = QueueStorage(str(Path(args.db_path)))
    try:
        items = []
        for row in storage.list_pending(limit=args.limit):
            payload = json.loads(row["payload_json"])
            items.append({
                "request_id": row["request_id"],
                "url": payload.get("source_url"),
                "priority": row["priority"],
                "status": row["status"],
                "summary": payload.get("reason") or payload.get("summary") or "",
            })
        print(json.dumps({"success": True, "count": len(items), "items": items}, ensure_ascii=False))
        return 0
    finally:
        storage.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Webpage Sorter SQLite-only demo CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Intake a URL using deterministic demo analysis and SQLite storage")
    demo.add_argument("url")
    demo.add_argument("--db-path", default="out/webpage-sorter.db")
    demo.add_argument("--out-dir", default="out")
    demo.add_argument("--confidence", type=float, default=0.95)
    demo.add_argument("--confidence-threshold", type=float, default=0.8)
    demo.set_defaults(func=command_demo)

    queue = sub.add_parser("queue", help="List pending SQLite judgment requests")
    queue.add_argument("--db-path", default="out/webpage-sorter.db")
    queue.add_argument("--limit", type=int, default=100)
    queue.set_defaults(func=command_queue)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

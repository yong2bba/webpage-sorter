"""Webpage Sorter: token-frugal URL intake and judgment queues for AI agents."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from hermes_constants import get_hermes_home
except ImportError:  # Standalone checkout fallback for CLI/tests outside Hermes.
    def get_hermes_home() -> Path:
        return Path.home() / ".hermes"

try:
    from .source_lab_core.intake import intake_url
    from .source_lab_core.low_analysis import (
        AUXILIARY_TASK,
        DEFAULT_MODEL,
        DEFAULT_PROVIDER,
        DEFAULT_TIMEOUT_SECONDS,
        analyze_url_low_level,
    )
    from .source_lab_core.postgres_collector_flow import PostgresCollectorFlowRepository
    from .source_lab_core.queue_storage import QueueStorage
    from .source_lab_core.result_processing import ResultProcessor
    from .source_lab_core.wiki_projection import WikiProjectionRenderer
except ImportError:  # Allows pytest to import the repository-root plugin file directly.
    from source_lab_core.intake import intake_url
    from source_lab_core.low_analysis import (
        AUXILIARY_TASK,
        DEFAULT_MODEL,
        DEFAULT_PROVIDER,
        DEFAULT_TIMEOUT_SECONDS,
        analyze_url_low_level,
    )
    from source_lab_core.postgres_collector_flow import PostgresCollectorFlowRepository
    from source_lab_core.queue_storage import QueueStorage
    from source_lab_core.result_processing import ResultProcessor
    from source_lab_core.wiki_projection import WikiProjectionRenderer

logger = logging.getLogger(__name__)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _model_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        result: Dict[str, Any] = asdict(value)
        return {k: _model_to_dict(v) for k, v in result.items()}
    if isinstance(value, dict):
        return {k: _model_to_dict(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_model_to_dict(v) for v in value]
    return value


def _db_path(args: Optional[dict] = None) -> str:
    args = args or {}
    explicit = str(args.get("db_path") or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    state_dir = get_hermes_home() / "state" / "source_lab"
    state_dir.mkdir(parents=True, exist_ok=True)
    return str(state_dir / "queue.db")


def _database_url(args: Optional[dict] = None) -> str:
    args = args or {}
    explicit = str(args.get("database_url") or args.get("db_url") or "").strip()
    if explicit:
        return explicit
    return str(
        os.environ.get("WEBPAGE_SORTER_DATABASE_URL")
        or os.environ.get("SOURCELAB_DATABASE_URL")
        or ""
    ).strip()


def _storage_info(args: Optional[dict] = None) -> Dict[str, str]:
    database_url = _database_url(args)
    if database_url:
        return {
            "storage_backend": "postgres",
            "storage_location": "SOURCELAB_DATABASE_URL",
        }
    return {
        "storage_backend": "sqlite",
        "storage_location": _db_path(args),
    }


def _with_storage(args: dict):
    database_url = _database_url(args)
    if database_url:
        return PostgresCollectorFlowRepository(database_url)
    return QueueStorage(_db_path(args))


def _wiki_repo_path(args: Optional[dict] = None) -> str:
    args = args or {}
    explicit = str(args.get("wiki_repo_path") or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    env_path = str(
        os.environ.get("WEBPAGE_SORTER_WIKI_REPO_PATH")
        or os.environ.get("SOURCELAB_WIKI_REPO_PATH")
        or ""
    ).strip()
    if env_path:
        return str(Path(env_path).expanduser())
    return ""


def _wiki_commit(args: Optional[dict] = None) -> bool:
    args = args or {}
    if "wiki_commit" in args:
        return bool(args.get("wiki_commit"))
    value = str(os.environ.get("SOURCELAB_WIKI_COMMIT") or "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _wiki_base_url(args: Optional[dict] = None) -> str:
    args = args or {}
    return str(
        args.get("wiki_base_url")
        or os.environ.get("WEBPAGE_SORTER_WIKI_BASE_URL")
        or os.environ.get("SOURCELAB_WIKI_BASE_URL")
        or ""
    )


def _project_wiki(storage, args: dict, *, source_id: Optional[str] = None, queue: bool = True) -> Dict[str, Any]:
    path = _wiki_repo_path(args)
    if not path or not hasattr(storage, "get_wiki_projection"):
        return {}
    renderer = WikiProjectionRenderer(
        storage,
        wiki_repo_path=path,
        wiki_base_url=_wiki_base_url(args),
    )
    result: Dict[str, Any] = {}
    if source_id:
        result["source_report"] = renderer.render_source_report(source_id, commit=_wiki_commit(args))
    if queue:
        result["judgment_queue"] = renderer.render_judgment_queue(commit=_wiki_commit(args))
    return result


_URL_RE = re.compile(r"https?://[^\s<>|)]+")


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _csv_contains(value: str, item: str) -> bool:
    values = {part.strip() for part in value.split(",") if part.strip()}
    return not values or item in values


def _extract_intake_url(text: str) -> str:
    """Pick the first source candidate URL from a Slack message.

    Slack often sends both `<url|label>` and rich preview text. Prefer real source
    URLs over Slack archive links and the dump wiki itself.
    """
    candidates = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(".,;]}")
        if "slack.com/archives/" in url:
            continue
        if os.environ.get("WEBPAGE_SORTER_PUBLIC_HOST") and os.environ["WEBPAGE_SORTER_PUBLIC_HOST"] in url:
            continue
        candidates.append(url)
    github = [u for u in candidates if "github.com/" in u]
    return (github or candidates or [""])[0]


def _source_lab_slack_auto_args(event, url: str) -> Dict[str, Any]:
    source = getattr(event, "source", None)
    chat_id = getattr(source, "chat_id", None)
    user_name = getattr(source, "user_name", None)
    user_id = getattr(source, "user_id", None)
    metadata = getattr(event, "metadata", None) or {}
    message_ts = str(
        getattr(source, "thread_id", None)
        or metadata.get("thread_ts")
        or metadata.get("ts")
        or ""
    ).strip()
    return {
        "url": url,
        "run_intake": True,
        "enqueue": True,
        "confidence_threshold": float(os.environ.get("SOURCELAB_SLACK_CONFIDENCE_THRESHOLD") or 0.8),
        "requested_by": os.environ.get("SOURCELAB_SLACK_REQUESTED_BY") or "collector_slack_auto",
        "submitted_via": "slack",
        "submitted_by": user_name or user_id or "slack_user",
        "slack_channel_id": chat_id,
        "slack_channel_name": os.environ.get("SOURCELAB_SLACK_CHANNEL_NAME") or "sourcelab",
        "slack_thread_ts": message_ts,
        "slack_message_ts": message_ts,
        "request_id": f"slack-{chat_id}-{message_ts}" if chat_id and message_ts else None,
    }


def _format_auto_intake_reply(result: Dict[str, Any], url: str) -> str:
    if not result.get("success"):
        return "SourceLab 자동 접수 실패\n" + f"URL: {url}\n" + f"error: {result.get('error') or result.get('intake', {}).get('error') or 'unknown'}"
    intake = result.get("intake") or {}
    body = intake.get("result") or {}
    branch = body.get("branch_decision") or {}
    projection = intake.get("wiki_projection") or {}
    source_report = projection.get("source_report") or {}
    queue_report = projection.get("judgment_queue") or {}
    state = body.get("state") or branch.get("state") or "unknown"
    queued = intake.get("queued")
    source_url = source_report.get("public_url") or ""
    queue_url = queue_report.get("public_url") or ""
    lines = [
        "SourceLab 자동 접수 완료",
        f"URL: {url}",
        f"상태: {state}",
        f"queued: {queued}",
    ]
    if source_url:
        lines.append(f"source: {source_url}")
    if queue_url:
        lines.append(f"queue: {queue_url}")
    return "\n".join(lines)


def _schedule_slack_auto_intake(event, gateway, url: str) -> None:
    async def _worker() -> None:
        try:
            args = _source_lab_slack_auto_args(event, url)
            raw = await asyncio.to_thread(handle_source_lab_analyze_url, args)
            result = json.loads(raw)
            reply = _format_auto_intake_reply(result, url)
        except Exception as exc:
            logger.exception("SourceLab Slack auto intake failed")
            reply = f"SourceLab 자동 접수 실패\nURL: {url}\nerror: {type(exc).__name__}: {exc}"
        try:
            await gateway._deliver_platform_notice(event.source, reply)
        except Exception:
            logger.exception("SourceLab Slack auto intake reply delivery failed")

    asyncio.get_running_loop().create_task(_worker())


def _on_pre_gateway_dispatch(event=None, gateway=None, **kwargs) -> Optional[Dict[str, Any]]:
    if not _env_bool("SOURCELAB_SLACK_AUTO_INTAKE", False):
        return None
    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", ""))
    if str(platform).lower() != "slack":
        return None
    chat_id = str(getattr(source, "chat_id", "") or "")
    allowed = os.environ.get("SOURCELAB_SLACK_AUTO_CHANNELS") or os.environ.get("SLACK_FREE_RESPONSE_CHANNELS") or ""
    if allowed and not _csv_contains(allowed, chat_id):
        return None
    text = str(getattr(event, "text", "") or "")
    if text.strip().startswith("/"):
        return None
    url = _extract_intake_url(text)
    if not url:
        return None
    if gateway is None:
        return None
    _schedule_slack_auto_intake(event, gateway, url)
    return {"action": "skip", "reason": "source_lab_slack_auto_intake", "url": url}


def handle_source_lab_intake_url(args: dict, **kwargs) -> str:
    """Run SourceLab URL intake with caller-supplied low-model analysis.

    SourceLab deliberately keeps network fetch/LLM analysis outside this tool. The caller
    passes an `analysis` object produced elsewhere; this tool validates, branches, and
    optionally enqueues a judgment request.
    """
    url = str(args.get("url") or "").strip()
    analysis = args.get("analysis")
    enqueue = bool(args.get("enqueue", True))
    threshold = float(args.get("confidence_threshold", 0.8))
    requested_by = str(args.get("requested_by") or "source_lab")

    if not isinstance(analysis, dict):
        return _json({
            "success": False,
            "error": "analysis object is required; SourceLab intake does not fetch or analyze URLs itself",
        })

    def analyzer(_url: str) -> dict:
        merged = dict(analysis)
        merged.setdefault("url", _url)
        return merged

    result = intake_url(
        url=url,
        analyzer=analyzer,
        confidence_threshold=threshold,
        requested_by=requested_by,
    )
    payload = _model_to_dict(result)

    row = None
    collector_flow = None
    wiki_projection = None
    wiki_projection_error = None
    queue_error = None
    branch = payload.get("branch_decision") or {}
    judgment_payload = branch.get("judgment_request_payload")
    should_persist = enqueue and result.state in {"self_close", "judgment_requested"}
    if should_persist:
        storage = _with_storage(args)
        try:
            if hasattr(storage, "record_intake_result"):
                collector_flow = storage.record_intake_result(
                    result,
                    requested_by=requested_by,
                    submitted_via=str(args.get("submitted_via") or "tool"),
                    request_id=str(args.get("request_id") or "").strip() or None,
                    submitted_by=args.get("submitted_by"),
                    slack_channel_id=args.get("slack_channel_id"),
                    slack_channel_name=args.get("slack_channel_name"),
                    slack_thread_ts=args.get("slack_thread_ts"),
                    slack_message_ts=args.get("slack_message_ts"),
                )
                request_id = collector_flow.get("judgment_request_request_id")
                if request_id:
                    row = storage.get_by_request_id(request_id)
                try:
                    wiki_projection = _project_wiki(
                        storage,
                        args,
                        source_id=collector_flow.get("source_id"),
                        queue=True,
                    )
                except Exception as exc:
                    wiki_projection_error = str(exc)
            elif result.state == "judgment_requested" and judgment_payload:
                row_id = storage.save(judgment_payload)
                row = storage.get_by_id(row_id)
        except Exception as exc:  # duplicate/validation errors should be visible to caller
            queue_error = str(exc)
        finally:
            storage.close()

    return _json({
        "success": queue_error is None and result.state != "error",
        "result": payload,
        "collector_flow": collector_flow,
        "wiki_projection": wiki_projection,
        "wiki_projection_error": wiki_projection_error,
        "queued": row is not None,
        "queue_row": row,
        "queue_error": queue_error,
        **_storage_info(args),
    })


def handle_source_lab_analyze_url(args: dict, **kwargs) -> str:
    """Fetch a URL, run the configured low-level analyzer, then optionally intake it."""
    url = str(args.get("url") or "").strip()
    run_intake = bool(args.get("run_intake", True))
    enqueue = bool(args.get("enqueue", True))
    threshold = float(args.get("confidence_threshold", 0.8))
    requested_by = str(args.get("requested_by") or "source_lab")
    fetch_timeout = float(args.get("fetch_timeout", 20.0))
    max_chars = int(args.get("max_chars") or 12000)
    max_tokens = int(args.get("max_tokens") or 1000)
    llm_timeout = args.get("llm_timeout")
    if llm_timeout is not None:
        llm_timeout = float(llm_timeout)

    try:
        analysis = analyze_url_low_level(
            url,
            fetch_timeout=fetch_timeout,
            max_chars=max_chars,
            llm_timeout=llm_timeout,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        return _json({
            "success": False,
            "error": f"low-level analysis failed: {type(exc).__name__}: {exc}",
            "auxiliary_task": AUXILIARY_TASK,
        })

    payload: Dict[str, Any] = {
        "success": True,
        "analysis": analysis,
        "auxiliary_task": AUXILIARY_TASK,
    }
    if not run_intake:
        return _json(payload)

    intake_args = dict(args)
    intake_args["analysis"] = analysis
    intake_args["enqueue"] = enqueue
    intake_args["confidence_threshold"] = threshold
    intake_args["requested_by"] = requested_by
    intake_result = json.loads(handle_source_lab_intake_url(intake_args, **kwargs))
    payload["intake"] = intake_result
    payload["success"] = bool(intake_result.get("success"))
    return _json(payload)


def handle_source_lab_queue_list(args: dict, **kwargs) -> str:
    limit = int(args.get("limit") or 100)
    storage = _with_storage(args)
    try:
        rows = storage.list_pending(limit=limit)
        for row in rows:
            for key in ("payload_json", "result_json"):
                if row.get(key):
                    try:
                        row[key.replace("_json", "")] = json.loads(row[key])
                    except Exception:
                        pass
        return _json({
            "success": True,
            "status": "pending",
            "count": len(rows),
            "rows": rows,
            **_storage_info(args),
        })
    finally:
        storage.close()


def handle_source_lab_process_result(args: dict, **kwargs) -> str:
    result_payload = args.get("result")
    if not isinstance(result_payload, dict):
        return _json({"success": False, "error": "result object is required"})

    storage = _with_storage(args)
    try:
        outcome = ResultProcessor(storage).process(result_payload)
        payload = _model_to_dict(outcome)
        wiki_projection = None
        wiki_projection_error = None
        if not outcome.errors and hasattr(storage, "get_by_request_id"):
            try:
                row = storage.get_by_request_id(result_payload.get("request_id", ""))
                wiki_projection = _project_wiki(
                    storage,
                    args,
                    source_id=(row or {}).get("source_id"),
                    queue=True,
                )
            except Exception as exc:
                wiki_projection_error = str(exc)
        return _json({
            "success": not bool(outcome.errors),
            "outcome": payload,
            "wiki_projection": wiki_projection,
            "wiki_projection_error": wiki_projection_error,
            **_storage_info(args),
        })
    finally:
        storage.close()


handle_webpage_sorter_analyze_url = handle_source_lab_analyze_url
handle_webpage_sorter_intake_url = handle_source_lab_intake_url
handle_webpage_sorter_queue_list = handle_source_lab_queue_list
handle_webpage_sorter_process_result = handle_source_lab_process_result


INTAKE_SCHEMA = {
    "name": "source_lab_intake_url",
    "description": "Run Webpage Sorter URL intake against caller-supplied analysis; optionally enqueue a judgment request.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Source URL to validate and canonicalize."},
            "analysis": {
                "type": "object",
                "description": "Low-model analysis object with content_type, confidence, signals, risk_flags, evidence, summary, and optional url.",
            },
            "enqueue": {"type": "boolean", "description": "Whether to save judgment_requested payloads to the queue DB.", "default": True},
            "confidence_threshold": {"type": "number", "description": "Confidence threshold below which judgment is requested.", "default": 0.8},
            "requested_by": {"type": "string", "description": "Agent/user id recorded in judgment request payload.", "default": "source_lab"},
            "database_url": {"type": "string", "description": "Optional PostgreSQL DSN. Defaults to SOURCELAB_DATABASE_URL when set."},
            "wiki_repo_path": {"type": "string", "description": "Optional Markdown/Git repository path for generated projections. Defaults to WEBPAGE_SORTER_WIKI_REPO_PATH or SOURCELAB_WIKI_REPO_PATH."},
            "wiki_base_url": {"type": "string", "description": "Optional public wiki/report base URL. Defaults to WEBPAGE_SORTER_WIKI_BASE_URL or SOURCELAB_WIKI_BASE_URL."},
            "wiki_commit": {"type": "boolean", "description": "Whether to git commit generated wiki projections. Defaults to true unless SOURCELAB_WIKI_COMMIT disables it."},
            "db_path": {"type": "string", "description": "Optional SQLite queue path when PostgreSQL is not configured; defaults to $HERMES_HOME/state/source_lab/queue.db."},
        },
        "required": ["url", "analysis"],
    },
}

ANALYZE_SCHEMA = {
    "name": "source_lab_analyze_url",
    "description": "Fetch a URL, run SourceLab's configured low-level auxiliary model, and optionally feed the result into intake/queue branching.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "HTTP(S) URL to fetch and analyze."},
            "run_intake": {"type": "boolean", "description": "Whether to feed the analysis into source_lab_intake_url after low-level analysis.", "default": True},
            "enqueue": {"type": "boolean", "description": "Whether to save judgment_requested payloads to the queue DB when run_intake is true.", "default": True},
            "confidence_threshold": {"type": "number", "description": "Confidence threshold below which judgment is requested.", "default": 0.8},
            "requested_by": {"type": "string", "description": "Agent/user id recorded in judgment request payload.", "default": "source_lab"},
            "fetch_timeout": {"type": "number", "description": "URL fetch timeout in seconds.", "default": 20},
            "llm_timeout": {"type": "number", "description": "Low-level auxiliary LLM timeout in seconds; defaults to auxiliary.source_lab_low_analysis.timeout."},
            "max_chars": {"type": "integer", "description": "Maximum fetched text characters sent to the low-level model.", "default": 12000},
            "max_tokens": {"type": "integer", "description": "Maximum low-level model output tokens.", "default": 1000},
            "database_url": {"type": "string", "description": "Optional PostgreSQL DSN. Defaults to SOURCELAB_DATABASE_URL when set."},
            "wiki_repo_path": {"type": "string", "description": "Optional Markdown/Git repository path for generated projections. Defaults to WEBPAGE_SORTER_WIKI_REPO_PATH or SOURCELAB_WIKI_REPO_PATH."},
            "wiki_base_url": {"type": "string", "description": "Optional public wiki/report base URL. Defaults to WEBPAGE_SORTER_WIKI_BASE_URL or SOURCELAB_WIKI_BASE_URL."},
            "wiki_commit": {"type": "boolean", "description": "Whether to git commit generated wiki projections. Defaults to true unless SOURCELAB_WIKI_COMMIT disables it."},
            "db_path": {"type": "string", "description": "Optional SQLite queue path when PostgreSQL is not configured; defaults to $HERMES_HOME/state/source_lab/queue.db."},
        },
        "required": ["url"],
    },
}

QUEUE_LIST_SCHEMA = {
    "name": "source_lab_queue_list",
    "description": "List pending SourceLab judgment requests from the local queue DB.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum rows to return.", "default": 100},
            "database_url": {"type": "string", "description": "Optional PostgreSQL DSN. Defaults to SOURCELAB_DATABASE_URL when set."},
            "wiki_repo_path": {"type": "string", "description": "Optional Markdown/Git repository path for generated projections. Defaults to WEBPAGE_SORTER_WIKI_REPO_PATH or SOURCELAB_WIKI_REPO_PATH."},
            "wiki_base_url": {"type": "string", "description": "Optional public wiki/report base URL. Defaults to WEBPAGE_SORTER_WIKI_BASE_URL or SOURCELAB_WIKI_BASE_URL."},
            "wiki_commit": {"type": "boolean", "description": "Whether to git commit generated wiki projections. Defaults to true unless SOURCELAB_WIKI_COMMIT disables it."},
            "db_path": {"type": "string", "description": "Optional SQLite queue path when PostgreSQL is not configured; defaults to $HERMES_HOME/state/source_lab/queue.db."},
        },
    },
}

PROCESS_RESULT_SCHEMA = {
    "name": "source_lab_process_result",
    "description": "Validate and apply a human/senior-agent judgment result to a Webpage Sorter queued request.",
    "parameters": {
        "type": "object",
        "properties": {
            "result": {"type": "object", "description": "Judgment result payload with request_id, judgment, reason, confidence, action, decided_at, decided_by."},
            "database_url": {"type": "string", "description": "Optional PostgreSQL DSN. Defaults to SOURCELAB_DATABASE_URL when set."},
            "wiki_repo_path": {"type": "string", "description": "Optional Markdown/Git repository path for generated projections. Defaults to WEBPAGE_SORTER_WIKI_REPO_PATH or SOURCELAB_WIKI_REPO_PATH."},
            "wiki_base_url": {"type": "string", "description": "Optional public wiki/report base URL. Defaults to WEBPAGE_SORTER_WIKI_BASE_URL or SOURCELAB_WIKI_BASE_URL."},
            "wiki_commit": {"type": "boolean", "description": "Whether to git commit generated wiki projections. Defaults to true unless SOURCELAB_WIKI_COMMIT disables it."},
            "db_path": {"type": "string", "description": "Optional SQLite queue path when PostgreSQL is not configured; defaults to $HERMES_HOME/state/source_lab/queue.db."},
        },
        "required": ["result"],
    },
}


def _schema_alias(schema: Dict[str, Any], name: str) -> Dict[str, Any]:
    alias = dict(schema)
    alias["name"] = name
    return alias


def _register_tool_pair(ctx, *, legacy_name: str, alias_name: str, schema: Dict[str, Any], handler, description: str, emoji: str) -> None:
    ctx.register_tool(
        name=legacy_name,
        toolset="source_lab",
        schema=schema,
        handler=handler,
        description=description,
        emoji=emoji,
    )
    ctx.register_tool(
        name=alias_name,
        toolset="webpage_sorter",
        schema=_schema_alias(schema, alias_name),
        handler=handler,
        description=description.replace("SourceLab", "Webpage Sorter"),
        emoji=emoji,
    )


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_auxiliary_task(
        key=AUXILIARY_TASK,
        display_name="SourceLab low-level analysis",
        description="URL triage extraction before SourceLab self-close/judgment branching",
        defaults={
            "provider": DEFAULT_PROVIDER,
            "model": DEFAULT_MODEL,
            "timeout": DEFAULT_TIMEOUT_SECONDS,
            "extra_body": {},
        },
    )
    _register_tool_pair(
        ctx,
        legacy_name="source_lab_analyze_url",
        alias_name="webpage_sorter_analyze_url",
        schema=ANALYZE_SCHEMA,
        handler=handle_source_lab_analyze_url,
        description="SourceLab low-level URL analysis with configured auxiliary model",
        emoji="🔎",
    )
    _register_tool_pair(
        ctx,
        legacy_name="source_lab_intake_url",
        alias_name="webpage_sorter_intake_url",
        schema=INTAKE_SCHEMA,
        handler=handle_source_lab_intake_url,
        description="SourceLab URL intake and branch decision",
        emoji="🧪",
    )
    _register_tool_pair(
        ctx,
        legacy_name="source_lab_queue_list",
        alias_name="webpage_sorter_queue_list",
        schema=QUEUE_LIST_SCHEMA,
        handler=handle_source_lab_queue_list,
        description="List SourceLab pending judgment requests",
        emoji="📥",
    )
    _register_tool_pair(
        ctx,
        legacy_name="source_lab_process_result",
        alias_name="webpage_sorter_process_result",
        schema=PROCESS_RESULT_SCHEMA,
        handler=handle_source_lab_process_result,
        description="Process SourceLab judgment result",
        emoji="✅",
    )

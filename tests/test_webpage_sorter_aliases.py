import json


def _tool_names(ctx):
    return [tool["name"] for tool in ctx.tools]


class RecordingContext:
    def __init__(self):
        self.tools = []
        self.hooks = []
        self.auxiliary = []

    def register_hook(self, name, handler):
        self.hooks.append((name, handler))

    def register_auxiliary_task(self, **kwargs):
        self.auxiliary.append(kwargs)

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def test_registers_webpage_sorter_tool_aliases():
    import source_lab

    ctx = RecordingContext()
    source_lab.register(ctx)

    names = _tool_names(ctx)
    assert "source_lab_analyze_url" in names
    assert "webpage_sorter_analyze_url" in names
    assert "source_lab_intake_url" in names
    assert "webpage_sorter_intake_url" in names
    assert "source_lab_queue_list" in names
    assert "webpage_sorter_queue_list" in names
    assert "source_lab_process_result" in names
    assert "webpage_sorter_process_result" in names

    alias_tools = [tool for tool in ctx.tools if tool["name"].startswith("webpage_sorter_")]
    assert alias_tools
    assert {tool["toolset"] for tool in alias_tools} == {"webpage_sorter"}


def test_webpage_sorter_alias_handlers_match_legacy_handlers(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCELAB_DATABASE_URL", raising=False)
    monkeypatch.delenv("WEBPAGE_SORTER_DATABASE_URL", raising=False)
    monkeypatch.delenv("SOURCELAB_WIKI_REPO_PATH", raising=False)
    monkeypatch.delenv("WEBPAGE_SORTER_WIKI_REPO_PATH", raising=False)
    import source_lab

    analysis = {
        "content_type": "technical",
        "confidence": 0.95,
        "signals": ["open_source_tool"],
        "risk_flags": [],
        "evidence": ["demo evidence"],
        "summary": "Demo page for alias test.",
    }
    args = {
        "url": "https://example.com/alias-test",
        "analysis": analysis,
        "db_path": str(tmp_path / "queue.db"),
    }
    legacy = json.loads(source_lab.handle_source_lab_intake_url(args))
    alias = json.loads(source_lab.handle_webpage_sorter_intake_url(args | {"url": "https://example.com/alias-test-2"}))

    assert legacy["success"] is True
    assert alias["success"] is True
    assert alias["storage_backend"] == "sqlite"

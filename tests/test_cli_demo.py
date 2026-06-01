import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "webpage_sorter_cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_demo_intakes_url_with_sqlite_and_writes_projection(tmp_path):
    db_path = tmp_path / "webpage-sorter.db"
    out_dir = tmp_path / "out"

    result = run_cli(
        "demo",
        "https://github.com/D4Vinci/Scrapling",
        "--db-path",
        str(db_path),
        "--out-dir",
        str(out_dir),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["storage_backend"] == "sqlite"
    assert payload["state"] == "self_close"
    assert db_path.exists()
    assert (out_dir / "sourcelab/sources/github/d4vinci-scrapling.md").exists()
    assert (out_dir / "sourcelab/queue/judgmentrequested.md").exists()


def test_cli_queue_lists_pending_requests_from_sqlite(tmp_path):
    db_path = tmp_path / "webpage-sorter.db"
    out_dir = tmp_path / "out"
    demo = run_cli(
        "demo",
        "https://example.com/uncertain",
        "--db-path",
        str(db_path),
        "--out-dir",
        str(out_dir),
        "--confidence",
        "0.2",
    )
    assert demo.returncode == 0, demo.stderr

    listed = run_cli("queue", "--db-path", str(db_path))

    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["items"][0]["url"] == "https://example.com/uncertain"

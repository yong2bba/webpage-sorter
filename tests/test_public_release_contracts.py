from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_repository_has_mit_license():
    license_text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in license_text
    assert "Copyright" in license_text


def test_github_actions_runs_pytest():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    assert "python3 -m pytest -q" in workflow or "pytest -q" in workflow
    assert "actions/checkout" in workflow
    assert "actions/setup-python" in workflow


def test_readme_has_quickstart_and_architecture_diagram():
    readme = (ROOT / "README.md").read_text()
    assert "## Quickstart" in readme
    assert "```mermaid" in readme
    assert "python3 -m webpage_sorter_cli demo" in readme
    assert "![demo" in readme.lower()

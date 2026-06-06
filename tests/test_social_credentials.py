"""Tests for SourceLab social collector local credential loading."""

import os


def test_social_env_loader_reads_chmod_600_style_key_value_file(tmp_path, monkeypatch):
    from source_lab_core.social_collectors import social_subprocess_env

    env_file = tmp_path / "social.env"
    env_file.write_text(
        "# local only\n"
        "TWITTER_AUTH_TOKEN=token123\n"
        "TWITTER_CT0=ct0abc\n"
        "XDG_CONFIG_HOME=/tmp/rdt-config\n"
        "IGNORED=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SOURCELAB_SOCIAL_ENV_FILE", str(env_file))

    env = social_subprocess_env(base_env={"PATH": os.environ.get("PATH", "")})

    assert env["TWITTER_AUTH_TOKEN"] == "token123"
    assert env["TWITTER_CT0"] == "ct0abc"
    assert env["AUTH_TOKEN"] == "token123"
    assert env["CT0"] == "ct0abc"
    assert env["XDG_CONFIG_HOME"] == "/tmp/rdt-config"
    assert "IGNORED" not in env


def test_social_env_loader_preserves_existing_env_when_explicit_file_missing(tmp_path, monkeypatch):
    from source_lab_core.social_collectors import social_subprocess_env

    monkeypatch.setenv("SOURCELAB_SOCIAL_ENV_FILE", str(tmp_path / "missing.env"))

    env = social_subprocess_env(base_env={"PATH": "x", "TWITTER_AUTH_TOKEN": "already"})

    assert env["TWITTER_AUTH_TOKEN"] == "already"
    assert env["AUTH_TOKEN"] == "already"
    assert env["PATH"] == "x"

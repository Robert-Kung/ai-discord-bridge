"""egress-exec-isolation 3.x — apiKeyHelper provisioning.

The key leaves the subprocess env (test_env_scrub) and materializes as a 0600
config-dir file behind an executable helper wired into the config-dir
settings.json. Subscription mode never touches any of it (the deferred-task
precedence concern is excluded by construction). Mechanism live-verified
2026-07-08: `claude -p` consults a config-dir settings.json apiKeyHelper.
"""
import json
import os
import subprocess

import pytest

from bridge import config, runner


def _cfg(tmp_path, key="sk-test-123"):
    return {"config_dir": str(tmp_path / "botcfg"), "api_key": key}


def test_provision_writes_key_helper_and_settings(tmp_path):
    cfg = _cfg(tmp_path)
    runner.provision_api_key_helper(cfg)
    d = tmp_path / "botcfg"
    key_file = d / config.API_KEY_FILENAME
    helper = d / config.API_KEY_HELPER_FILENAME
    assert key_file.read_text().strip() == "sk-test-123"
    assert oct(key_file.stat().st_mode & 0o777) == "0o600"
    assert os.access(helper, os.X_OK)
    assert json.loads((d / "settings.json").read_text())["apiKeyHelper"] == str(helper)
    # the helper actually emits the key (what the CLI will exec)
    out = subprocess.run([str(helper)], capture_output=True, text=True)
    assert out.stdout.strip() == "sk-test-123"


def test_provision_preserves_existing_settings(tmp_path):
    cfg = _cfg(tmp_path)
    d = tmp_path / "botcfg"
    d.mkdir()
    (d / "settings.json").write_text('{"model": "claude-opus-4-8"}')
    runner.provision_api_key_helper(cfg)
    merged = json.loads((d / "settings.json").read_text())
    assert merged["model"] == "claude-opus-4-8"     # pre-existing keys survive
    assert "apiKeyHelper" in merged


def test_provision_corrupt_settings_fails_loud(tmp_path):
    cfg = _cfg(tmp_path)
    d = tmp_path / "botcfg"
    d.mkdir()
    (d / "settings.json").write_text("{not json")
    with pytest.raises(SystemExit):
        runner.provision_api_key_helper(cfg)


def test_provision_file_seeded_key_accepted(tmp_path):
    """No env key, but the operator dropped the key file: keep it, wire the helper."""
    cfg = {"config_dir": str(tmp_path / "botcfg"), "api_key": None}
    d = tmp_path / "botcfg"
    d.mkdir()
    (d / config.API_KEY_FILENAME).write_text("sk-dropped\n")
    runner.provision_api_key_helper(cfg)
    helper = d / config.API_KEY_HELPER_FILENAME
    out = subprocess.run([str(helper)], capture_output=True, text=True)
    assert out.stdout.strip() == "sk-dropped"


def test_provision_no_key_source_refuses(tmp_path):
    cfg = {"config_dir": str(tmp_path / "botcfg"), "api_key": None}
    with pytest.raises(SystemExit):
        runner.provision_api_key_helper(cfg)


def test_validate_config_accepts_file_seeded_key(set_env, tmp_state, tmp_path, monkeypatch):
    """3.4: USE_API_KEY without env keys passes validation when the key FILE exists."""
    d = tmp_path / "cfg-a"
    d.mkdir()
    (d / config.API_KEY_FILENAME).write_text("sk-file\n")
    monkeypatch.setattr(config, "BOT_CONFIG_DIRS", {"A": str(d)})
    set_env(USE_API_KEY="1")
    config.load_config()
    assert config.USE_API_KEY is True


def test_validate_config_no_key_source_exits(set_env, tmp_state, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BOT_CONFIG_DIRS", {"A": str(tmp_path / "empty-cfg")})
    set_env(USE_API_KEY="1")
    with pytest.raises(SystemExit):
        config.load_config()


def test_subscription_mode_provisions_nothing(tmp_path, monkeypatch):
    """The deferral concern, excluded by construction: in subscription mode the
    provisioner is never invoked (bot.main gates on USE_API_KEY), and nothing in
    build_subprocess_env references helper files."""
    monkeypatch.setattr(config, "USE_API_KEY", False)
    cfg = _cfg(tmp_path)
    env = runner.build_subprocess_env(cfg, base_env={"PATH": "/usr/bin"})
    assert not (tmp_path / "botcfg").exists()  # no side effects from env building
    assert "ANTHROPIC_API_KEY" not in env

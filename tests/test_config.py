"""L2 — config loading + fail-closed validation (the security-critical guards)."""
import pytest

from bridge import config


def test_load_config_populates_globals(set_env, tmp_state):
    set_env(ALLOWED_USER_IDS="111,222")
    config.load_config()
    assert config.CHANNEL_ID == 123456
    assert config.ALLOWED_USER_IDS == {111, 222}
    assert set(config.BOTS) == {"A", "B"}
    assert config.BOTS["A"]["token"] == "fake-A-token"
    assert config.USE_API_KEY is False


def test_empty_whitelist_refuses_to_start(set_env, tmp_state):
    set_env(ALLOWED_USER_IDS="")
    with pytest.raises(SystemExit):
        config.load_config()


def test_whitespace_only_whitelist_refuses(set_env, tmp_state):
    set_env(ALLOWED_USER_IDS="  ,  ")
    with pytest.raises(SystemExit):
        config.load_config()


def test_missing_channel_id_raises(set_env, tmp_state, monkeypatch):
    set_env()
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    with pytest.raises(KeyError):
        config.load_config()


def test_missing_bot_token_raises(set_env, tmp_state, monkeypatch):
    set_env()
    monkeypatch.delenv("DISCORD_BOT_A_TOKEN", raising=False)
    with pytest.raises(KeyError):
        config.load_config()


def test_use_api_key_without_keys_refuses(set_env, tmp_state):
    set_env(USE_API_KEY="true")  # no ANTHROPIC_API_KEY_A/_B
    with pytest.raises(SystemExit):
        config.load_config()


def test_use_api_key_partial_keys_refuses(set_env, tmp_state):
    set_env(USE_API_KEY="true", ANTHROPIC_API_KEY_A="sk-A")  # B missing
    with pytest.raises(SystemExit):
        config.load_config()


def test_use_api_key_with_both_keys_ok(set_env, tmp_state):
    set_env(USE_API_KEY="true", ANTHROPIC_API_KEY_A="sk-A", ANTHROPIC_API_KEY_B="sk-B")
    config.load_config()
    assert config.USE_API_KEY is True
    assert config.BOTS["A"]["api_key"] == "sk-A"


def test_subscription_mode_missing_keys_ok(set_env, tmp_state):
    set_env(USE_API_KEY="false")
    config.load_config()
    assert config.USE_API_KEY is False


def test_project_dirs_parsed_and_resolved_from_env(set_env, tmp_state, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    set_env(PROJECT_DIRS=f"{a} , {b}")  # whitespace around entries tolerated
    config.load_config()
    assert a.resolve() in config.PROJECT_DIRS
    assert b.resolve() in config.PROJECT_DIRS


def test_project_dirs_empty_when_unset(set_env, tmp_state, monkeypatch):
    set_env()
    monkeypatch.delenv("PROJECT_DIRS", raising=False)
    config.load_config()
    assert config.PROJECT_DIRS == []


def test_import_is_side_effect_free():
    # The refactor's core guarantee: importing the package reads no env. Outside a
    # load_config() call every config global holds its fail-closed default (the
    # autouse _restore_globals fixture guarantees no cross-test leakage).
    assert config.CHANNEL_ID is None
    assert config.ALLOWED_USER_IDS == set()
    assert config.USE_API_KEY is False
    assert config.BOTS == {}
    assert config.PROJECT_DIRS == []

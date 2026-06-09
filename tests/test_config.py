"""L2 — config loading + fail-closed validation (the security-critical guards)."""
import pytest

import bot


def test_load_config_populates_globals(set_env, tmp_state):
    set_env(ALLOWED_USER_IDS="111,222")
    bot.load_config()
    assert bot.CHANNEL_ID == 123456
    assert bot.ALLOWED_USER_IDS == {111, 222}
    assert set(bot.BOTS) == {"A", "B"}
    assert bot.BOTS["A"]["token"] == "fake-A-token"
    assert bot.USE_API_KEY is False


def test_empty_whitelist_refuses_to_start(set_env, tmp_state):
    set_env(ALLOWED_USER_IDS="")
    with pytest.raises(SystemExit):
        bot.load_config()


def test_whitespace_only_whitelist_refuses(set_env, tmp_state):
    set_env(ALLOWED_USER_IDS="  ,  ")
    with pytest.raises(SystemExit):
        bot.load_config()


def test_missing_channel_id_raises(set_env, tmp_state, monkeypatch):
    set_env()
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    with pytest.raises(KeyError):
        bot.load_config()


def test_missing_bot_token_raises(set_env, tmp_state, monkeypatch):
    set_env()
    monkeypatch.delenv("DISCORD_BOT_A_TOKEN", raising=False)
    with pytest.raises(KeyError):
        bot.load_config()


def test_use_api_key_without_keys_refuses(set_env, tmp_state):
    set_env(USE_API_KEY="true")  # no ANTHROPIC_API_KEY_A/_B
    with pytest.raises(SystemExit):
        bot.load_config()


def test_use_api_key_partial_keys_refuses(set_env, tmp_state):
    set_env(USE_API_KEY="true", ANTHROPIC_API_KEY_A="sk-A")  # B missing
    with pytest.raises(SystemExit):
        bot.load_config()


def test_use_api_key_with_both_keys_ok(set_env, tmp_state):
    set_env(USE_API_KEY="true", ANTHROPIC_API_KEY_A="sk-A", ANTHROPIC_API_KEY_B="sk-B")
    bot.load_config()
    assert bot.USE_API_KEY is True
    assert bot.BOTS["A"]["api_key"] == "sk-A"


def test_subscription_mode_missing_keys_ok(set_env, tmp_state):
    # keys absent + USE_API_KEY false → legal (subscription auth)
    set_env(USE_API_KEY="false")
    bot.load_config()
    assert bot.USE_API_KEY is False


def test_project_dirs_parsed_and_resolved_from_env(set_env, tmp_state, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    set_env(PROJECT_DIRS=f"{a} , {b}")  # whitespace around entries tolerated
    bot.load_config()
    assert a.resolve() in bot.PROJECT_DIRS
    assert b.resolve() in bot.PROJECT_DIRS


def test_project_dirs_empty_when_unset(set_env, tmp_state, monkeypatch):
    set_env()
    monkeypatch.delenv("PROJECT_DIRS", raising=False)
    bot.load_config()
    assert bot.PROJECT_DIRS == []


def test_import_is_side_effect_free():
    # The refactor's core guarantee: importing bot.py reads no env. Before
    # load_config() runs, every config global holds its fail-closed default.
    assert bot.CHANNEL_ID is None
    assert bot.ALLOWED_USER_IDS == set()
    assert bot.USE_API_KEY is False
    assert bot.BOTS == {}
    assert bot.PROJECT_DIRS == []

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

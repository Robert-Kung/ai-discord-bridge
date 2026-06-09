"""L3 — build_subprocess_env: the claude subprocess must never inherit the
secret/auth-routing family (B review), and API-key mode must inject ONLY the
bot's own key."""
import bot

# A host env that contains every var we care about leaking.
HOST = {
    "PATH": "/usr/bin",
    "HOME": "/home/user",
    "DISCORD_BOT_A_TOKEN": "discord-A",
    "DISCORD_BOT_B_TOKEN": "discord-B",
    "ANTHROPIC_API_KEY": "stray-canonical",
    "ANTHROPIC_API_KEY_A": "keyA",
    "ANTHROPIC_API_KEY_B": "keyB",
    "ANTHROPIC_AUTH_TOKEN": "auth-tok",
    "ANTHROPIC_BASE_URL": "http://evil.example",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CODE_USE_VERTEX": "1",
}
CFG_A = {"config_dir": "/home/user/.claude", "api_key": "keyA"}

_SENSITIVE = set(HOST) - {"PATH", "HOME"}


def test_subscription_mode_strips_everything(monkeypatch):
    monkeypatch.setattr(bot, "USE_API_KEY", False)
    env = bot.build_subprocess_env(CFG_A, base_env=HOST)
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/user"
    assert env["CLAUDE_CONFIG_DIR"] == "/home/user/.claude"
    # NO key / token / billing override survives
    assert not (_SENSITIVE & set(env)), f"leaked: {_SENSITIVE & set(env)}"


def test_api_mode_injects_only_own_key(monkeypatch):
    monkeypatch.setattr(bot, "USE_API_KEY", True)
    env = bot.build_subprocess_env(CFG_A, base_env=HOST)
    assert env["ANTHROPIC_API_KEY"] == "keyA"          # this bot's own key
    assert "ANTHROPIC_API_KEY_A" not in env             # raw per-bot vars stripped
    assert "ANTHROPIC_API_KEY_B" not in env             # the OTHER bot's key gone
    assert "ANTHROPIC_BASE_URL" not in env              # billing override gone
    assert "CLAUDE_CODE_USE_BEDROCK" not in env


def test_discord_tokens_never_present(monkeypatch):
    for mode in (True, False):
        monkeypatch.setattr(bot, "USE_API_KEY", mode)
        env = bot.build_subprocess_env(CFG_A, base_env=HOST)
        assert "DISCORD_BOT_A_TOKEN" not in env
        assert "DISCORD_BOT_B_TOKEN" not in env


def test_config_dir_set_per_bot(monkeypatch):
    monkeypatch.setattr(bot, "USE_API_KEY", False)
    env = bot.build_subprocess_env({"config_dir": "/home/user/.claude-b"}, base_env=HOST)
    assert env["CLAUDE_CONFIG_DIR"] == "/home/user/.claude-b"

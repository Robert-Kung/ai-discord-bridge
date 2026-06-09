"""Shared fixtures. bot.py is import-side-effect-free (env is read lazily in
load_config), so we can import it once here and exercise the pure helpers +
config loading directly."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot  # noqa: E402

# Minimal env that makes load_config() succeed.
MINIMAL_ENV = {
    "DISCORD_CHANNEL_ID": "123456",
    "DISCORD_BOT_A_TOKEN": "fake-A-token",
    "DISCORD_BOT_B_TOKEN": "fake-B-token",
    "ALLOWED_USER_IDS": "111",
}
# Anything that could flip auth/billing — always cleared so the host env can't
# leak into a test run.
_AUTH_ENV = ("USE_API_KEY", "ANTHROPIC_API_KEY_A", "ANTHROPIC_API_KEY_B",
             "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
             "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


@pytest.fixture(autouse=True)
def _restore_globals():
    """load_config()/validate_config() mutate module globals; snapshot & restore
    so tests don't leak state into each other."""
    saved = {k: getattr(bot, k) for k in
             ("CHANNEL_ID", "ALLOWED_USER_IDS", "USE_API_KEY", "BOTS")}
    yield
    for k, v in saved.items():
        setattr(bot, k, v)


@pytest.fixture
def set_env(monkeypatch):
    """Return a setter: set_env(**overrides) writes MINIMAL_ENV + overrides into
    the environment (value None deletes the var)."""
    def _set(**overrides):
        for k in _AUTH_ENV:
            monkeypatch.delenv(k, raising=False)
        for k, v in {**MINIMAL_ENV, **overrides}.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
    return _set


@pytest.fixture
def tmp_state(monkeypatch, tmp_path):
    """Redirect the state dirs load_config() mkdirs so tests never touch the real
    ~/.claude-shared."""
    for name in ("STATE_DIR", "SUMMARIES_DIR", "PROJECT_NOTES_DIR"):
        monkeypatch.setattr(bot, name, tmp_path / name.lower())

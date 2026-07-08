"""Shared fixtures. The bridge is import-side-effect-free (env is read lazily in
config.load_config), so we import the modules once here and exercise the pure helpers
+ config loading directly."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge import config  # noqa: E402

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
# Opt-in feature tiers — cleared so a host that dogfoods a tier (e.g. exports
# ENABLE_EXEC_EVALUATOR=1) can't flip the "off by default" tests.
_TIER_ENV = ("ENABLE_BYPASS_TIER", "ENABLE_APPROVER_TIER", "ENABLE_EXEC_EVALUATOR")


@pytest.fixture(autouse=True)
def _restore_globals():
    """load_config()/validate_config() mutate config globals; snapshot & restore so
    tests don't leak state into each other. Driven by config._CONFIG_GLOBALS so a new
    config global can't silently fall out of the restore set."""
    saved = {k: getattr(config, k) for k in config._CONFIG_GLOBALS}
    yield
    for k, v in saved.items():
        setattr(config, k, v)


@pytest.fixture
def set_env(monkeypatch):
    """Return a setter: set_env(**overrides) writes MINIMAL_ENV + overrides into the
    environment (value None deletes the var)."""
    def _set(**overrides):
        for k in _AUTH_ENV + _TIER_ENV:
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
    ~/.claude-shared. Patched on config — the consuming module reads them as attributes."""
    for name in ("STATE_DIR", "SUMMARIES_DIR", "PROJECT_NOTES_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())

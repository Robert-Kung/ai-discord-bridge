"""L1 — _is_trusted: only whitelisted humans + our OWN A/B bots influence context
/ flush. A third-party bot/webhook must be dropped (message.author.bot is true for
ANY bot, so trust is matched by our bot user ids)."""
import pytest

from bridge import config, state, trust


@pytest.fixture(autouse=True)
def _trust_world(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(state, "bot_user_ids", {"A": 1001, "B": 1002})


def test_own_recorded_reply_trusted():
    # record_bot_reply stores author_id=None, bot=True
    assert trust._is_trusted({"author_id": None, "bot": True}) is True


def test_own_ab_bot_incoming_trusted():
    assert trust._is_trusted({"author_id": 1001, "bot": True}) is True
    assert trust._is_trusted({"author_id": 1002, "bot": True}) is True


def test_whitelisted_human_trusted():
    assert trust._is_trusted({"author_id": 111, "bot": False}) is True


def test_third_party_bot_dropped():
    assert trust._is_trusted({"author_id": 9999, "bot": True}) is False


def test_random_human_dropped():
    assert trust._is_trusted({"author_id": 222, "bot": False}) is False


def test_malformed_none_author_nonbot_dropped():
    assert trust._is_trusted({"author_id": None, "bot": False}) is False

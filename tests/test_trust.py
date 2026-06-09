"""L1 — _is_trusted: only whitelisted humans + our OWN A/B bots influence context
/ flush. A third-party bot/webhook must be dropped (B review #1: message.author.bot
is true for ANY bot, so trust is matched by our bot user ids)."""
import pytest

import bot


@pytest.fixture(autouse=True)
def _trust_world(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(bot, "bot_user_ids", {"A": 1001, "B": 1002})


def test_own_recorded_reply_trusted():
    # record_bot_reply stores author_id=None, bot=True
    assert bot._is_trusted({"author_id": None, "bot": True}) is True


def test_own_ab_bot_incoming_trusted():
    assert bot._is_trusted({"author_id": 1001, "bot": True}) is True
    assert bot._is_trusted({"author_id": 1002, "bot": True}) is True


def test_whitelisted_human_trusted():
    assert bot._is_trusted({"author_id": 111, "bot": False}) is True


def test_third_party_bot_dropped():
    # GitHub/RSS/translator integration — bot=True but not our id
    assert bot._is_trusted({"author_id": 9999, "bot": True}) is False


def test_random_human_dropped():
    assert bot._is_trusted({"author_id": 222, "bot": False}) is False


def test_malformed_none_author_nonbot_dropped():
    assert bot._is_trusted({"author_id": None, "bot": False}) is False

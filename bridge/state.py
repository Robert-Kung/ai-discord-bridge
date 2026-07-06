"""Shared in-memory mutable state for the bridge.

Every structure here has a documented single writer/reader contract. Consumers read
these as attributes (`state.pending_actions`), never via from-import, so the live
object is shared, not captured by value.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque

import discord

from bridge import config

# Per-bot serialization lock (A/B each). Writer/reader: frontend, discuss,
# run_plan_then_execute, approver flows — held around a bot's call so concurrent
# messages for the same bot don't interleave.
bot_locks: dict[str, asyncio.Lock] = {n: asyncio.Lock() for n in config.BOT_CONFIG_DIRS}
# Guards the bot-turn budget counter below. Writer/reader: frontend.on_message only.
turn_lock = asyncio.Lock()
# Per-channel debate lock. Writer/reader: discuss.run_discuss only.
discuss_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
# Per-resolved-cwd lock (A/B on the same project → no concurrent writes).
# Writer/reader: runner._call_claude.
cwd_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Bot-to-bot turn budget. A bare int rebound under turn_lock — wrapped in
# accessors because a module attribute cannot be from-imported and rebound.
# Writer/reader: frontend.on_message (under turn_lock).
_bot_turns_since_human: int = 0


def get_bot_turns() -> int:
    return _bot_turns_since_human


def set_bot_turns(value: int) -> None:
    global _bot_turns_since_human
    _bot_turns_since_human = value


# Rolling per-channel transcript buffer. Writer: memory.buffer_append /
# memory.record_bot_reply. Reader: memory.format_buffer_transcript / build_context_prefix.
channel_msg_log: dict[int, deque] = defaultdict(lambda: deque(maxlen=300))
# Messages since last auto-flush, per channel. Writer/reader: frontend + memory.do_flush.
messages_since_flush: dict[int, int] = defaultdict(int)
# last-seen resumed-context size per (bot, cwd), from each call's usage report.
# Writer: runner._call_claude. Reader: memory.maybe_token_flush, frontend.cmd_state.
session_ctx_tokens: dict[tuple[str, str], int] = defaultdict(int)
# whether a 400k checkpoint summary was already written this growth cycle.
# Writer/reader: memory.maybe_token_flush.
token_checkpointed: dict[tuple[str, str], bool] = {}
# Pending ✅/❌ reaction futures keyed by message id. Writer: frontend
# (run_plan_then_execute, request_discord_approval) + resolver on_raw_reaction_add.
pending_actions: dict[int, asyncio.Future] = {}
# Our own bot user ids, set at on_ready. Writer: frontend.on_ready. Reader: trust,
# frontend routing.
bot_user_ids: dict[str, int] = {}
# The live discord.Client objects. Writer: bot.main. Reader: frontend.request_discord_approval.
clients: dict[str, discord.Client] = {}

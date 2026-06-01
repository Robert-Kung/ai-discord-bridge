"""Discord ↔ dual-account Claude Code bridge — v2.

Features:
  - Channel message buffer + manual !flush + auto-flush every N messages
  - Summary persistence in ~/.claude-shared/discord-summaries/<channel>/
  - --append-system-prompt-file prepends latest summary into every call
  - !discuss <topic>     sequential A↔B debate
  - !mode plan|edit|bypass  per-channel default permission mode
  - !once <mode>         single-message override
  - bypass triggers plan-then-execute: bot posts plan + ✅/❌ reactions
  - !yolo                skip plan-then-execute confirm step (single message)
  - !reset               clear current bot session id (keep summary)
  - !flush               force a summary write now
  - !state               show current channel state
  - !help                command reference
  - Startup announcement (Bot-A only) on every container restart
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import discord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("bridge")

# ── Config ──────────────────────────────────────────────────────────────
SHARED_DIR = Path("/home/user/.claude-shared")
STATE_DIR = SHARED_DIR / "discord-state"
SUMMARIES_DIR = SHARED_DIR / "discord-summaries"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}
MAX_BOT_TURNS = int(os.environ.get("MAX_BOT_TURNS", "6"))
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
AUTO_FLUSH_THRESHOLD = int(os.environ.get("AUTO_FLUSH_THRESHOLD", "20"))
PLAN_REACTION_TIMEOUT = int(os.environ.get("PLAN_REACTION_TIMEOUT", "300"))

BOTS: dict[str, dict] = {
    "A": {"token": os.environ["DISCORD_BOT_A_TOKEN"], "config_dir": "/home/user/.claude"},
    "B": {"token": os.environ["DISCORD_BOT_B_TOKEN"], "config_dir": "/home/user/.claude-b"},
}

# ── State (in-memory + per-bot lock) ────────────────────────────────────
bot_locks: dict[str, asyncio.Lock] = {n: asyncio.Lock() for n in BOTS}
turn_lock = asyncio.Lock()
discuss_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
bot_turns_since_human: int = 0
channel_msg_log: dict[int, deque] = defaultdict(lambda: deque(maxlen=300))
messages_since_flush: dict[int, int] = defaultdict(int)
pending_actions: dict[int, asyncio.Future] = {}
bot_user_ids: dict[str, int] = {}
clients: dict[str, discord.Client] = {}

VALID_MODES = {"plan", "edit", "bypass"}
MODE_ALIASES = {
    "plan": "plan",
    "edit": "acceptEdits",
    "acceptedits": "acceptEdits",
    "bypass": "bypassPermissions",
    "bypasspermissions": "bypassPermissions",
}

DEFAULT_CHANNEL_MODE = "plan"  # safe default; bypass requires opt-in


# ── Channel state file persistence ──────────────────────────────────────
def channel_state_path(channel_id: int) -> Path:
    return STATE_DIR / f"channel_{channel_id}.json"


def load_channel_state(channel_id: int) -> dict:
    p = channel_state_path(channel_id)
    if not p.exists():
        return {"mode": DEFAULT_CHANNEL_MODE}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"mode": DEFAULT_CHANNEL_MODE}


def save_channel_state(channel_id: int, state: dict) -> None:
    p = channel_state_path(channel_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(p)


# ── Per-bot session id persistence ──────────────────────────────────────
def load_session(bot_name: str) -> str | None:
    p = STATE_DIR / f"{bot_name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("session_id")
    except (json.JSONDecodeError, OSError):
        return None


def save_session(bot_name: str, sid: str) -> None:
    p = STATE_DIR / f"{bot_name}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"session_id": sid}))
    tmp.replace(p)


def clear_session(bot_name: str) -> None:
    p = STATE_DIR / f"{bot_name}.json"
    p.unlink(missing_ok=True)


# ── Summary persistence ─────────────────────────────────────────────────
def channel_summary_dir(channel_id: int) -> Path:
    d = SUMMARIES_DIR / str(channel_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_summary_path(channel_id: int) -> Path | None:
    p = channel_summary_dir(channel_id) / "latest.md"
    return p if p.exists() else None


def save_summary(channel_id: int, content: str) -> Path:
    d = channel_summary_dir(channel_id)
    ts = time.strftime("%Y%m%d-%H%M%S")
    target = d / f"{ts}.md"
    target.write_text(content)
    latest = d / "latest.md"
    latest.write_text(content)  # plain copy avoids bind-mount symlink quirks
    return target


# ── Message buffer ──────────────────────────────────────────────────────
def buffer_append(message: discord.Message) -> None:
    log_q = channel_msg_log[message.channel.id]
    if log_q and log_q[-1]["id"] == message.id:
        return
    log_q.append({
        "id": message.id,
        "author": message.author.display_name,
        "bot": message.author.bot,
        "content": message.content,
        "ts": message.created_at.isoformat(),
    })
    messages_since_flush[message.channel.id] += 1


def format_buffer_transcript(channel_id: int, limit: int = 80) -> str:
    items = list(channel_msg_log[channel_id])[-limit:]
    return "\n".join(
        f"[{m['ts'][:19]}] {m['author']}{' (bot)' if m['bot'] else ''}: {m['content']}"
        for m in items
    )


# ── Claude CLI wrapper ──────────────────────────────────────────────────
async def call_claude(
    bot_name: str,
    prompt: str,
    *,
    mode: str = "plan",
    use_session: bool = True,
    prepend_summary_from_channel: int | None = None,
    extra_args: list[str] | None = None,
) -> tuple[str, bool]:
    """Run `claude -p` for the given bot. Returns (reply_text, ok)."""
    cfg = BOTS[bot_name]
    api_mode = MODE_ALIASES.get(mode, mode)

    args = ["claude", "-p", "--output-format", "json", "--permission-mode", api_mode]
    if use_session:
        sid = load_session(bot_name)
        if sid:
            args += ["--resume", sid]
    if prepend_summary_from_channel is not None:
        latest = latest_summary_path(prepend_summary_from_channel)
        if latest:
            args += ["--append-system-prompt-file", str(latest)]
    if extra_args:
        args += extra_args
    args.append(prompt)

    env = {**os.environ, "CLAUDE_CONFIG_DIR": cfg["config_dir"]}
    log.info("[%s] call mode=%s session=%s prompt_len=%d",
             bot_name, api_mode, use_session, len(prompt))

    proc = await asyncio.create_subprocess_exec(
        *args, env=env, cwd="/home/user",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLAUDE_TIMEOUT)
    except asyncio.TimeoutError:
        log.error("[%s] timeout — killing pid %s", bot_name, proc.pid)
        proc.kill()
        try:
            await proc.communicate()
        except Exception:
            pass
        return (f"⏱️ 響應超時（{CLAUDE_TIMEOUT}s）", False)

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:500]
        log.error("[%s] exit=%d stderr=%s", bot_name, proc.returncode, err)
        return (f"❌ Claude 呼叫失敗 (exit {proc.returncode})：```{err}```", False)

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        out = stdout.decode("utf-8", errors="replace")[:500]
        return (f"❌ 解析失敗：```{out}```", False)

    reply = data.get("result") or "(空回覆)"
    new_sid = data.get("session_id")
    if new_sid and use_session:
        save_session(bot_name, new_sid)
    return (reply, True)


# ── Summary generation (uses Bot-B as worker by convention) ─────────────
async def do_flush(channel_id: int, *, manual: bool = False) -> str:
    transcript = format_buffer_transcript(channel_id)
    if not transcript or len(transcript) < 200:
        return "(對話太短，跳過 flush)"

    prompt = (
        "請整理以下 Discord 頻道對話為精煉 markdown 知識文件。\n"
        "保留：(1) 決策與結論 (2) 進行中的任務 / open questions "
        "(3) 提到的關鍵檔案/路徑 (4) 參與者與角色脈絡。\n"
        "不要逐字複述，500 字內。\n\n--- 對話原文 ---\n" + transcript
    )
    reply, ok = await call_claude(
        "B", prompt, mode="plan", use_session=False,
        prepend_summary_from_channel=None,
    )
    if not ok:
        return reply

    path = save_summary(channel_id, reply)
    messages_since_flush[channel_id] = 0
    log.info("flushed channel=%d -> %s (manual=%s)", channel_id, path, manual)
    return f"📝 已寫入 summary：`{path.name}` ({len(reply)} chars)"


# ── Discuss mode ────────────────────────────────────────────────────────
async def run_discuss(channel: discord.TextChannel, topic: str) -> None:
    """A→B→A→B sequential debate, each turn sees the other's prior reply."""
    async with discuss_locks[channel.id]:
        history: list[tuple[str, str]] = []  # [(bot, text), ...]
        for round_num in range(MAX_BOT_TURNS):
            bot_name = "A" if round_num % 2 == 0 else "B"
            if round_num == 0:
                prompt = (
                    f"使用者發起辯論：{topic}\n\n"
                    "請給你的初步立場，2-4 句話。直接觀點，不要客套。"
                )
            else:
                last_other = history[-1]
                prompt = (
                    f"辯論主題：{topic}\n\n"
                    f"輪 {round_num}（對方 {last_other[0]} 剛說）：\n{last_other[1]}\n\n"
                    "請接話：同意/反對/補充，2-4 句。提到具體點，不要只說「對」。"
                )
            async with bot_locks[bot_name]:
                async with channel.typing():
                    reply, ok = await call_claude(
                        bot_name, prompt, mode="plan", use_session=False,
                        prepend_summary_from_channel=channel.id,
                    )
            if not ok:
                await channel.send(f"⚠️ discuss 中斷：{reply}")
                return
            history.append((bot_name, reply))
            prefix = f"**[discuss {round_num + 1}/{MAX_BOT_TURNS} · {bot_name}]**"
            for chunk in chunk_message(f"{prefix}\n{reply}"):
                await channel.send(chunk)
            low = reply.lower()
            if any(kw in low for kw in ["結束辯論", "no further objections", "辯論結束"]):
                break
        await channel.send(f"_discuss 結束_（{len(history)} 輪）")


# ── Plan-then-execute (for bypass mode) ─────────────────────────────────
async def run_plan_then_execute(
    channel: discord.TextChannel,
    bot_name: str,
    prompt: str,
    skip_plan: bool,
) -> None:
    """For bypass mode: post plan, await reaction, then execute."""
    if skip_plan:
        async with bot_locks[bot_name]:
            async with channel.typing():
                reply, _ = await call_claude(
                    bot_name, prompt, mode="bypass",
                    prepend_summary_from_channel=channel.id,
                )
        for c in chunk_message(f"🚀 **[mode=bypass · yolo]**\n{reply}"):
            await channel.send(c)
        return

    # Phase 1: plan
    async with bot_locks[bot_name]:
        async with channel.typing():
            plan_reply, ok = await call_claude(
                bot_name, prompt, mode="plan",
                prepend_summary_from_channel=channel.id,
            )
    if not ok:
        await channel.send(plan_reply)
        return

    plan_msg = await channel.send(
        f"📋 **[計畫 · 等待你 ✅ 執行 / ❌ 取消（{PLAN_REACTION_TIMEOUT}s）]**\n{plan_reply[:1800]}"
    )
    await plan_msg.add_reaction("✅")
    await plan_msg.add_reaction("❌")

    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_actions[plan_msg.id] = fut

    try:
        decision = await asyncio.wait_for(fut, timeout=PLAN_REACTION_TIMEOUT)
    except asyncio.TimeoutError:
        pending_actions.pop(plan_msg.id, None)
        await channel.send("⏱️ 5 分鐘無人 react，取消執行")
        return

    if decision != "confirm":
        await channel.send("❌ 已取消")
        return

    # Phase 2: execute
    async with bot_locks[bot_name]:
        async with channel.typing():
            exec_reply, _ = await call_claude(
                bot_name, prompt, mode="bypass",
                prepend_summary_from_channel=channel.id,
            )
    for c in chunk_message(f"🚀 **[mode=bypass · executed]**\n{exec_reply}"):
        await channel.send(c)


# ── Utility ─────────────────────────────────────────────────────────────
def chunk_message(text: str, size: int = 1900) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


def parse_command(content: str) -> tuple[str, str] | None:
    stripped = content.strip()
    if not stripped.startswith("!"):
        return None
    parts = stripped[1:].split(None, 1)
    if not parts:
        return None
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


def extract_once_override(content: str) -> tuple[str, str | None]:
    """Strip `!once <mode>` suffix from message, return (cleaned, mode_or_None)."""
    parts = content.rsplit("!once", 1)
    if len(parts) != 2:
        return content, None
    tail = parts[1].strip().split()
    if not tail:
        return content, None
    mode = tail[0].lower()
    if mode not in VALID_MODES:
        return content, None
    return parts[0].rstrip(), mode


def extract_yolo_flag(content: str) -> tuple[str, bool]:
    if "!yolo" in content.lower():
        return content.replace("!yolo", "").replace("!YOLO", "").strip(), True
    return content, False


# ── Command handlers ────────────────────────────────────────────────────
HELP_TEXT = """**Bridge 指令參考**
`!mode plan|edit|bypass` — 設 channel 預設模式（bypass 需 whitelist）
`!once <mode>` — 單一訊息使用該模式（末尾加，不獨佔一行）
`!yolo` — bypass 跳過 plan-then-execute（單訊息）
`!discuss <topic>` — A↔B 強制輪流辯論
`!flush` — 立即整理對話到 summary 知識檔
`!reset` — 清掉當前 bot session id（保留 summary）
`!state` — 看當前 channel 狀態
`!help` — 顯示這份說明

**模式說明**
`plan` 只讀規劃；`edit` 可寫檔但 bash 仍要過 bypass；`bypass` 全自動，預設會 plan-then-execute 等你 ✅
"""


async def cmd_mode(channel, args: str, author_id: int) -> str:
    target = args.strip().lower()
    if target not in VALID_MODES:
        return f"❓ 用法：`!mode plan|edit|bypass`（目前 valid: {sorted(VALID_MODES)}）"
    if target == "bypass" and ALLOWED_USER_IDS and author_id not in ALLOWED_USER_IDS:
        return "🛡 `bypass` 需要 whitelist 權限"
    state = load_channel_state(channel.id)
    state["mode"] = target
    save_channel_state(channel.id, state)
    return f"✅ channel 模式 → **{target}**"


async def cmd_reset(channel, bot_name: str) -> str:
    clear_session(bot_name)
    return f"♻️ {bot_name} session 清除（summary 保留，下輪會 prepend）"


async def cmd_state(channel) -> str:
    state = load_channel_state(channel.id)
    summary = latest_summary_path(channel.id)
    return (
        f"**Channel state**\n"
        f"• mode: `{state.get('mode', DEFAULT_CHANNEL_MODE)}`\n"
        f"• buffered messages: {len(channel_msg_log[channel.id])}\n"
        f"• messages since last flush: {messages_since_flush[channel.id]}\n"
        f"• latest summary: `{summary.name if summary else '(none)'}`"
    )


# ── Discord client factory ──────────────────────────────────────────────
def make_client(bot_name: str) -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.reactions = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        bot_user_ids[bot_name] = client.user.id
        log.info("[%s] logged in as %s (id=%d)", bot_name, client.user, client.user.id)
        # Bot-A posts startup announcement once both bots are ready
        if bot_name == "A":
            await asyncio.sleep(2)  # let Bot-B finish login too
            channel = client.get_channel(CHANNEL_ID)
            if channel:
                await channel.send(STARTUP_ANNOUNCEMENT)

    @client.event
    async def on_message(message: discord.Message):
        global bot_turns_since_human
        if message.channel.id != CHANNEL_ID:
            return
        if message.author.id == client.user.id:
            return

        # Only one bot writes to buffer + handles commands (Bot-A is primary)
        if bot_name == "A":
            buffer_append(message)
            # auto-flush
            if messages_since_flush[message.channel.id] >= AUTO_FLUSH_THRESHOLD:
                asyncio.create_task(do_flush(message.channel.id, manual=False))

        # mention check (user mention or bot's role)
        mentioned = client.user in message.mentions
        if not mentioned and message.guild is not None:
            bot_member = message.guild.me
            if bot_member is not None:
                bot_role_ids = {r.id for r in bot_member.roles}
                if bot_role_ids & {r.id for r in message.role_mentions}:
                    mentioned = True

        # Commands (only Bot-A processes them, to avoid double-handling)
        if bot_name == "A":
            cmd = parse_command(message.content)
            if cmd is not None:
                name, args = cmd
                # Only mentioned-or-whitelisted users can run commands
                if ALLOWED_USER_IDS and message.author.id not in ALLOWED_USER_IDS:
                    return
                if name == "help":
                    await message.channel.send(HELP_TEXT)
                    return
                if name == "mode":
                    await message.channel.send(await cmd_mode(message.channel, args, message.author.id))
                    return
                if name == "reset":
                    target_bot = args.strip().upper() or "A"
                    if target_bot not in BOTS:
                        await message.channel.send(f"用法：`!reset A|B`")
                        return
                    await message.channel.send(await cmd_reset(message.channel, target_bot))
                    return
                if name == "state":
                    await message.channel.send(await cmd_state(message.channel))
                    return
                if name == "flush":
                    await message.channel.send("⏳ flushing...")
                    result = await do_flush(message.channel.id, manual=True)
                    await message.channel.send(result)
                    return
                if name == "discuss":
                    if not args.strip():
                        await message.channel.send("用法：`!discuss <主題>`")
                        return
                    asyncio.create_task(run_discuss(message.channel, args.strip()))
                    return
                # Unknown command: don't reply (may be a typo)

        if not mentioned:
            return

        is_bot_msg = message.author.bot

        # Turn budget under turn_lock
        async with turn_lock:
            if is_bot_msg:
                if bot_turns_since_human >= MAX_BOT_TURNS:
                    log.info("[%s] turn budget exhausted (%d)", bot_name, bot_turns_since_human)
                    return
            else:
                if ALLOWED_USER_IDS and message.author.id not in ALLOWED_USER_IDS:
                    return
                bot_turns_since_human = 0
            bot_turns_since_human += 1

        # Determine mode for this call: once override > channel default
        cleaned_content, once_mode = extract_once_override(message.content)
        cleaned_content, yolo = extract_yolo_flag(cleaned_content)
        if once_mode:
            effective_mode = once_mode
        else:
            effective_mode = load_channel_state(message.channel.id).get("mode", DEFAULT_CHANNEL_MODE)

        if effective_mode == "bypass" and once_mode == "bypass" and ALLOWED_USER_IDS and message.author.id not in ALLOWED_USER_IDS:
            await message.channel.send("🛡 `!once bypass` 需要 whitelist")
            return

        prompt = f"[from {message.author.display_name}] {cleaned_content}"

        # bypass mode → plan-then-execute (unless !yolo)
        if effective_mode == "bypass":
            await run_plan_then_execute(message.channel, bot_name, prompt, skip_plan=yolo)
            return

        # Standard call
        async with bot_locks[bot_name]:
            async with message.channel.typing():
                reply, _ok = await call_claude(
                    bot_name, prompt, mode=effective_mode,
                    prepend_summary_from_channel=message.channel.id,
                )
        prefix = ""
        if once_mode:
            prefix = f"**[mode={effective_mode} · once]** "
        for c in chunk_message(prefix + reply):
            await message.channel.send(c)

    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.message_id not in pending_actions:
            return
        if ALLOWED_USER_IDS and payload.user_id not in ALLOWED_USER_IDS:
            return
        if payload.user_id in bot_user_ids.values():
            return  # ignore bot's own reactions
        fut = pending_actions.pop(payload.message_id, None)
        if fut is None or fut.done():
            return
        emoji = payload.emoji.name
        if emoji == "✅":
            fut.set_result("confirm")
        elif emoji == "❌":
            fut.set_result("cancel")

    return client


# ── Startup announcement ────────────────────────────────────────────────
STARTUP_ANNOUNCEMENT = """🚀 **Bridge v2 上線**

**🧠 記憶**
• `!flush` 立即整理本 channel 對話到 summary
• `!reset A|B` 清掉 bot session（summary 保留）
• 每 {auto} 則訊息自動 flush
• 每次回應自動 prepend 最新 summary 當 context

**🎭 多模式對話**
• `@A`、`@B` 單獨叫 — 自然回，被 @ 才接話
• `@A @B` 一起 — 並行雙視角
• `!discuss <主題>` — 強制 A↔B 輪流辯論

**🔐 授權執行**
• `!mode plan|edit|bypass` 切 channel 模式
• `!once <mode>` 單訊息 override
• bypass 預設會先給 plan 等你 ✅ 才執行
• `!yolo` 跳過 plan 確認

**ℹ️ 其他**
• `!state` 看當前狀態  •  `!help` 完整指令參考

預設模式：**`plan`**（只讀規劃）。要寫檔請先 `!mode edit`。
""".format(auto=AUTO_FLUSH_THRESHOLD)


# ── Main ────────────────────────────────────────────────────────────────
async def main():
    global clients
    for name in BOTS:
        clients[name] = make_client(name)
    log.info("starting bridge v2: channel=%d allowed=%s max_turns=%d auto_flush=%d",
             CHANNEL_ID, sorted(ALLOWED_USER_IDS), MAX_BOT_TURNS, AUTO_FLUSH_THRESHOLD)
    await asyncio.gather(*(clients[n].start(BOTS[n]["token"]) for n in BOTS))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("shutdown")

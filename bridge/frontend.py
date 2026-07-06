"""Discord frontend: client factory, event handlers, command handlers, the
plan-then-execute bypass flow, and the human-approval round-trip. Thin I/O + routing —
business logic lives in the service modules. This is the ONLY module that imports
`execute` (the execution-layer entry).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path

import discord

from bridge import config, discuss, jobs, memory, runner, sessions, state, trust
from bridge.util import chunk_message

log = logging.getLogger("bridge.frontend")


# ── Command / flag parsing ──────────────────────────────────────────────
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
    if mode not in config.VALID_MODES:
        return content, None
    return parts[0].rstrip(), mode


def extract_yolo_flag(content: str) -> tuple[str, bool]:
    if "!yolo" in content.lower():
        return content.replace("!yolo", "").replace("!YOLO", "").strip(), True
    return content, False


# ── M4 per-command approver: Discord round-trip (the injected ask_human) ──────
async def request_discord_approval(command: str, tool_name: str = "Bash") -> bool:
    """Post a single command to the bridge channel with ✅/❌ and await a whitelisted
    human's decision. Returns True only on an explicit ✅; timeout / no channel → False
    (fail-closed). Reuses pending_actions + on_raw_reaction_add, so only ALLOWED_USER_IDS
    reactions count."""
    channel = state.clients["A"].get_channel(config.CHANNEL_ID) if state.clients.get("A") else None
    if channel is None:
        return False
    _MAX = 1500
    shown = command[:_MAX]
    trunc = "" if len(command) <= _MAX else (
        f"\n⚠️ **指令過長，已截斷 {len(command) - _MAX} 字元——未顯示部分可能藏惡意內容；"
        "不確定就按 ❌**")
    msg = await channel.send(
        f"🔐 **[逐指令核可] {tool_name} 要執行：**\n```\n{shown}\n```{trunc}\n"
        f"✅ 允許 / ❌ 拒絕（{config.PLAN_REACTION_TIMEOUT}s，逾時＝拒絕）"
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_actions[msg.id] = fut
    try:
        decision = await asyncio.wait_for(fut, timeout=config.PLAN_REACTION_TIMEOUT)
    except asyncio.TimeoutError:
        state.pending_actions.pop(msg.id, None)
        await channel.send("⏱️ 逐指令核可逾時 → 拒絕")
        return False
    return decision == "confirm"


# ── Plan-then-execute (for bypass mode) ─────────────────────────────────
async def run_plan_then_execute(
    channel: discord.TextChannel,
    bot_name: str,
    prompt: str,
    skip_plan: bool,
    cwd: str | None = None,
) -> None:
    """For bypass mode: post plan, await reaction, then execute."""
    cwd = config.DEFAULT_CWD if cwd is None else cwd
    # Build the summary/notes snapshot per call (matching the original, which rebuilt
    # it inside each _call_claude): the plan and execute phases are separated by the
    # human-reaction wait, during which a flush may refresh the on-disk context.
    if skip_plan:
        spf = memory.build_combined_system_prompt(channel.id, cwd, bot_name)
        async with state.bot_locks[bot_name]:
            async with channel.typing():
                reply, _ = await runner.execute(
                    bot_name, prompt, mode="bypass",
                    system_prompt_file=spf, cwd=cwd,
                )
        memory.record_bot_reply(channel.id, bot_name, reply[:1000], cwd=cwd)
        for c in chunk_message(f"🚀 **[mode=bypass · yolo]**\n{reply}"):
            await channel.send(c)
        return

    # Phase 1: plan
    async with state.bot_locks[bot_name]:
        async with channel.typing():
            plan_reply, ok = await runner.converse(
                bot_name, prompt,
                system_prompt_file=memory.build_combined_system_prompt(channel.id, cwd, bot_name),
                cwd=cwd,
            )
    if not ok:
        await channel.send(plan_reply)
        return

    plan_msg = await channel.send(
        f"📋 **[計畫 · 等待你 ✅ 執行 / ❌ 取消（{config.PLAN_REACTION_TIMEOUT}s）]**\n{plan_reply[:1800]}"
    )
    await plan_msg.add_reaction("✅")
    await plan_msg.add_reaction("❌")

    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_actions[plan_msg.id] = fut

    try:
        decision = await asyncio.wait_for(fut, timeout=config.PLAN_REACTION_TIMEOUT)
    except asyncio.TimeoutError:
        state.pending_actions.pop(plan_msg.id, None)
        await channel.send("⏱️ 5 分鐘無人 react，取消執行")
        return

    if decision != "confirm":
        await channel.send("❌ 已取消")
        return

    # Phase 2: execute (rebuild the snapshot — a flush may have run during the wait)
    async with state.bot_locks[bot_name]:
        async with channel.typing():
            exec_reply, _ = await runner.execute(
                bot_name, prompt, mode="bypass",
                system_prompt_file=memory.build_combined_system_prompt(channel.id, cwd, bot_name),
                cwd=cwd,
            )
    memory.record_bot_reply(channel.id, bot_name, exec_reply[:1000], cwd=cwd)
    for c in chunk_message(f"🚀 **[mode=bypass · executed]**\n{exec_reply}"):
        await channel.send(c)


# ── Execution-tier background jobs (agent-exec-loop M1) ──────────────────────
def _where(cwd: str) -> str:
    return "~" if cwd == config.DEFAULT_CWD else Path(cwd).name


def _render_job_status(job, where: str, trace) -> str:
    head = (f"🚀 **[job `{job.id}` · Bot-{job.bot} · `{where}`]** 執行中…"
            f"（`!cancel {job.id}` 取消）")
    if not trace:
        return head
    body = "\n".join(f"• {t}" for t in trace)
    return f"{head}\n{body}"[:1990]


async def start_exec_job(message: discord.Message, bot_name: str, prompt: str,
                         mode: str, cwd: str) -> None:
    """Spawn an execution-tier task as a tracked background job (M1). Capped at one
    running job per project; conversation calls are not blocked while it runs."""
    if jobs.running_for_project(cwd) >= 1:
        await message.channel.send(
            f"🛑 `{_where(cwd)}` 已有執行中的任務（`!jobs` 查看，`!cancel <id>` 取消）；"
            "一個專案一次只跑一個 exec job。")
        return
    job = jobs.create_job(bot_name, cwd, message.channel.id)
    status_msg = await message.channel.send(
        _render_job_status(job, _where(cwd), None), reference=message)
    jobs.set_msg(job, status_msg.id)
    asyncio.create_task(
        _drive_exec_job(job, message.channel, status_msg, bot_name, prompt, mode, cwd))


async def _drive_exec_job(job, channel, status_msg, bot_name: str, prompt: str,
                          mode: str, cwd: str) -> None:
    where = _where(cwd)
    trace: deque = deque(maxlen=config.EXEC_TRACE_LINES)
    done = asyncio.Event()

    async def _updater():
        # Throttled: edit the status message at most once per interval with the rolling
        # tool-use trace, until the job finishes.
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=config.EXEC_STATUS_EDIT_INTERVAL)
            except asyncio.TimeoutError:
                pass
            if trace:
                try:
                    await status_msg.edit(content=_render_job_status(job, where, list(trace)))
                except discord.HTTPException:
                    pass

    upd = asyncio.create_task(_updater())
    spf = memory.build_combined_system_prompt(channel.id, cwd, bot_name)
    try:
        reply, outcome = await runner.run_streaming_exec(
            bot_name, prompt, mode=mode, cwd=cwd, system_prompt_file=spf,
            on_trace=trace.append,
            on_proc=lambda proc: jobs.attach_proc(job, proc),
            timeout=config.EXEC_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — a job crash must not take down the bot
        done.set()
        await upd
        jobs.set_status(job, jobs.FAILED)
        log.exception("exec-job %s crashed", job.id)
        await _safe_edit(status_msg, f"❌ **[job `{job.id}`]** 內部錯誤：{e}")
        return
    done.set()
    await upd

    if job.status == jobs.CANCELLED:
        await _safe_edit(status_msg,
                         f"🛑 **[job `{job.id}`]** 已取消（部分變更可能已寫入 live checkout）")
        return
    if outcome == runner.EXEC_TIMEOUT:
        jobs.set_status(job, jobs.TIMEOUT)
        await _safe_edit(status_msg,
                         f"⏱️ **[job `{job.id}`]** 逾時（{config.EXEC_TIMEOUT}s）→ 已終止 process group")
        return
    if reply is None or outcome == runner.EXEC_FAILED:
        jobs.set_status(job, jobs.FAILED)
        await _safe_edit(status_msg, f"❌ **[job `{job.id}`]** 執行失敗（無結果）")
        return

    jobs.set_status(job, jobs.DONE)
    memory.record_bot_reply(channel.id, bot_name, reply, cwd=cwd)
    await _safe_edit(status_msg, f"✅ **[job `{job.id}` · `{where}`]** 完成")
    cwd_tag = "" if cwd == config.DEFAULT_CWD else f"[{where}] "
    for c in chunk_message(f"{cwd_tag}**[mode={mode} · job {job.id}]** {reply}"):
        await channel.send(c)
    await memory.maybe_token_flush(channel, bot_name, cwd)


async def _safe_edit(msg, content: str) -> None:
    try:
        await msg.edit(content=content[:1990])
    except discord.HTTPException:
        pass


# ── Command handlers ────────────────────────────────────────────────────
HELP_TEXT = """**Bridge 指令參考**
`!mode plan|edit|bypass|approve` — 設 channel 預設模式（bypass/approve 需 whitelist + opt-in tier）
`!once <mode>` — 單一訊息使用該模式（末尾加，不獨佔一行）
`!yolo` — bypass 跳過 plan-then-execute（單訊息）
`!discuss <topic>` — A↔B 強制輪流辯論
`!jobs` — 列出執行中的背景任務（id / bot / 專案 / 已跑多久）
`!cancel <id>` — 取消某個執行中的任務（終止整個 process group）
`!flush` — 立即整理對話到 summary 知識檔
`!reset` — 清掉當前 bot session id（保留 summary）
`!cd <專案名|路徑>` — 把此 channel 切到該專案工作目錄（限白名單 git 專案）
`!state` — 看當前狀態（cwd / mode / context tokens / A·B 帳號 5h·7d 用量）
`!help` — 顯示這份說明

**模式說明**
`plan` 只讀規劃；`edit` 可寫檔/執行（受 settings.json deny 規則約束）。
`approve` 逐指令核可：非白名單指令會丟 Discord 等你 ✅ 才跑（需 `ENABLE_APPROVER_TIER`）。
`bypass` 全自動 plan-then-execute 等你 ✅（需 `ENABLE_BYPASS_TIER`，預設關閉）。
要實際改專案 code：先 `!cd <專案>` 再 `!mode edit`（或 `approve` 走逐指令核可）
"""


async def cmd_cd(channel, args: str) -> str:
    if not args.strip():
        cur = sessions.get_channel_cwd(channel.id)
        names = "\n".join(f"  • {p.name}" for p in config.PROJECT_DIRS)
        root = " ←目前" if cur == config.DEFAULT_CWD else ""
        return (
            f"**目前 cwd**：`{cur}`\n"
            f"**可切換的專案**（`!cd <名稱>`）：\n{names}\n"
            f"  • `~` 回根目錄（`{config.DEFAULT_CWD}`）{root}"
        )
    if args.strip() in {"~", "/", "root", "home", config.DEFAULT_CWD}:
        resolved = config.DEFAULT_CWD
    else:
        resolved, msg = trust.resolve_project_cwd(args)
        if resolved is None:
            return msg
    # flush-before-switch: snapshot the OLD project's transcript BEFORE we mutate state.
    old_cwd = sessions.get_channel_cwd(channel.id)
    extra = ""
    if old_cwd != resolved and old_cwd != config.DEFAULT_CWD:
        transcript = memory.format_buffer_transcript(channel.id, cwd=old_cwd)
        if transcript and len(transcript) >= 200:
            asyncio.create_task(memory.do_flush(
                channel.id, cwd_override=old_cwd, transcript_override=transcript))
            extra = f"\n💾 已在背景把 `{Path(old_cwd).name}` 的進度寫入專案筆記"
    st = sessions.load_channel_state(channel.id)
    st["cwd"] = resolved
    sessions.save_channel_state(channel.id, st)
    state.messages_since_flush[channel.id] = 0
    return f"📂 cwd → `{resolved}`（此 channel 後續 @ 都在這裡工作）{extra}"


async def cmd_mode(channel, args: str, author_id: int) -> str:
    target = args.strip().lower()
    if target not in config.VALID_MODES:
        return f"❓ 用法：`!mode plan|edit|bypass|approve`（目前 valid: {sorted(config.VALID_MODES)}）"
    if target == "bypass":
        if not config.BYPASS_TIER_ENABLED:
            return ("🛡 `bypass` tier 未啟用（預設關閉）。需操作者設 `ENABLE_BYPASS_TIER=1` "
                    "才能開啟；在那之前請用 `edit`（可寫檔/執行，受 deny 規則約束）。")
        if author_id not in config.ALLOWED_USER_IDS:
            return "🛡 `bypass` 需要 whitelist 權限"
    if target == "approve":
        if not config.APPROVER_TIER_ENABLED:
            return ("🛡 `approve` tier 未啟用（預設關閉）。需操作者設 `ENABLE_APPROVER_TIER=1` "
                    "才能開啟——每條非白名單指令會丟 Discord 等你 ✅ 才執行。")
        if author_id not in config.ALLOWED_USER_IDS:
            return "🛡 `approve` 需要 whitelist 權限"
    st = sessions.load_channel_state(channel.id)
    st["mode"] = target
    sessions.save_channel_state(channel.id, st)
    return f"✅ channel 模式 → **{target}**"


async def cmd_reset(channel, bot_name: str) -> str:
    cwd = sessions.get_channel_cwd(channel.id)
    sessions.clear_session(bot_name, cwd)
    return f"♻️ {bot_name} 在 `{Path(cwd).name}` 的 session 清除（summary 保留）"


def read_cswap_usage() -> str:
    """Render both accounts' 5h/7d quota from the host-written cswap snapshot."""
    p = config.CSWAP_USAGE_FILE
    if not p.exists():
        return ("• 帳號用量: (無 cswap-usage.json — 需在 host 跑 "
                "`scripts/refresh-cswap-usage.py`，建議掛 cron)")
    try:
        d = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return "• 帳號用量: (cswap-usage.json 解析失敗)"
    age_min = int((time.time() - d.get("generated_at", 0)) // 60)
    label = {1: "A", 2: "B"}
    lines = [f"• 帳號用量（cswap · {age_min} 分前）:"]
    for acc in d.get("accounts", []):
        who = label.get(acc.get("slot"), f"slot{acc.get('slot')}")
        active = " ⚡" if acc.get("active") else ""
        lines.append(
            f"   {who}{active} {acc.get('email', '?')}: "
            f"5h {acc.get('h5_pct', '?')}%（reset {acc.get('h5_resets', '?')}）· "
            f"7d {acc.get('d7_pct', '?')}%（reset {acc.get('d7_resets', '?')}）"
        )
    return "\n".join(lines)


async def cmd_state(channel) -> str:
    st = sessions.load_channel_state(channel.id)
    cwd = sessions.get_channel_cwd(channel.id)
    summary = memory.latest_summary_path(channel.id, cwd)
    notes = memory.project_notes_path(cwd)
    has_notes = "✅" if (cwd != config.DEFAULT_CWD and notes.exists()) else "—"
    a_ctx = state.session_ctx_tokens.get(("A", cwd), 0)
    b_ctx = state.session_ctx_tokens.get(("B", cwd), 0)
    return (
        f"**Channel state**\n"
        f"• cwd: `{cwd}`\n"
        f"• mode: `{st.get('mode', config.DEFAULT_CHANNEL_MODE)}`\n"
        f"• buffered messages: {len(state.channel_msg_log[channel.id])}\n"
        f"• messages since last flush: {state.messages_since_flush[channel.id]}\n"
        f"• context（此 cwd）: A ~{a_ctx // 1000}k · B ~{b_ctx // 1000}k"
        f"（{config.FLUSH_TOKEN_THRESHOLD // 1000}k 存檔 / {config.RESET_TOKEN_THRESHOLD // 1000}k 重置 · 1M 視窗）\n"
        f"• latest summary（此 cwd）: `{summary.name if summary else '(none)'}`\n"
        f"• 專案筆記: {has_notes}\n"
        + read_cswap_usage()
    )


# ── Discord client factory ──────────────────────────────────────────────
def make_client(bot_name: str) -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.reactions = True
    # Egress containment (phase 1): on an internal (routeless) network the gateway
    # websocket AND REST must traverse the allow-list proxy. discord.py 2.4 honours a
    # single proxy= kwarg for both (verified in the step-0 spike). Unset → direct.
    client = discord.Client(intents=intents, proxy=config.EGRESS_PROXY_URL)

    @client.event
    async def on_ready():
        state.bot_user_ids[bot_name] = client.user.id
        log.info("[%s] logged in as %s (id=%d)", bot_name, client.user, client.user.id)
        if bot_name == "A":
            await asyncio.sleep(2)  # let Bot-B finish login too
            channel = client.get_channel(config.CHANNEL_ID)
            if channel:
                await channel.send(STARTUP_ANNOUNCEMENT)

    @client.event
    async def on_message(message: discord.Message):
        if message.channel.id != config.CHANNEL_ID:
            return
        if message.author.id == client.user.id:
            return

        # Only one bot writes to buffer + handles commands (Bot-A is primary)
        if bot_name == "A":
            memory.buffer_append(message)
            if state.messages_since_flush[message.channel.id] >= config.AUTO_FLUSH_THRESHOLD:
                state.messages_since_flush[message.channel.id] = 0
                asyncio.create_task(memory.do_flush(message.channel.id, manual=False))

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
                if message.author.id not in config.ALLOWED_USER_IDS:
                    return
                if name == "help":
                    await message.channel.send(HELP_TEXT)
                    return
                if name == "mode":
                    await message.channel.send(await cmd_mode(message.channel, args, message.author.id))
                    return
                if name == "reset":
                    target_bot = args.strip().upper() or "A"
                    if target_bot not in config.BOTS:
                        await message.channel.send("用法：`!reset A|B`")
                        return
                    await message.channel.send(await cmd_reset(message.channel, target_bot))
                    return
                if name == "cd":
                    await message.channel.send(await cmd_cd(message.channel, args))
                    return
                if name == "state":
                    await message.channel.send(await cmd_state(message.channel))
                    return
                if name == "flush":
                    await message.channel.send("⏳ flushing...")
                    result = await memory.do_flush(message.channel.id, manual=True)
                    await message.channel.send(result)
                    return
                if name == "discuss":
                    if not args.strip():
                        await message.channel.send("用法：`!discuss <主題>`")
                        return
                    asyncio.create_task(discuss.run_discuss(message.channel, args.strip()))
                    return
                if name == "jobs":
                    await message.channel.send(jobs.render_job_list())
                    return
                if name == "cancel":
                    jid = args.strip()
                    job = jobs.get_job(jid)
                    if job is None or job.status != jobs.RUNNING:
                        await message.channel.send(f"找不到執行中的 job `{jid}`（`!jobs` 查看）")
                        return
                    await jobs.cancel_job(job)
                    await message.channel.send(f"🛑 job `{jid}` 已取消")
                    return
                # Unknown command: don't reply (may be a typo)

        if not mentioned:
            return

        # ONLY our own A/B bots get the no-whitelist debate path.
        is_bot_msg = message.author.id in state.bot_user_ids.values()

        # Turn budget under turn_lock
        async with state.turn_lock:
            if is_bot_msg:
                if state.get_bot_turns() >= config.MAX_BOT_TURNS:
                    log.info("[%s] turn budget exhausted (%d)", bot_name, state.get_bot_turns())
                    return
            else:
                if message.author.id not in config.ALLOWED_USER_IDS:
                    return
                state.set_bot_turns(0)
            state.set_bot_turns(state.get_bot_turns() + 1)

        # Determine mode for this call: once override > channel default
        cleaned_content, once_mode = extract_once_override(message.content)
        cleaned_content, yolo = extract_yolo_flag(cleaned_content)
        if once_mode:
            effective_mode = once_mode
        else:
            effective_mode = sessions.load_channel_state(message.channel.id).get("mode", config.DEFAULT_CHANNEL_MODE)

        # Default-closed opt-in tiers: any path to bypass/approve is downgraded to the
        # safe default unless that tier is enabled AND the requester is whitelisted.
        if effective_mode in ("bypass", "approve") and not trust._tier_allowed(effective_mode, message.author.id):
            if once_mode in ("bypass", "approve"):
                await message.channel.send(
                    f"🛡 `!once {once_mode}` 不可用（該 tier 未啟用或你不在 whitelist）。"
                    "已改用安全的 `plan` 模式。"
                )
            effective_mode = config.DEFAULT_CHANNEL_MODE

        context = memory.build_context_prefix(message.channel.id, limit=15)
        other = "B" if bot_name == "A" else "A"
        other_id = state.bot_user_ids.get(other)
        mention_hint = ""
        if other_id:
            mention_hint = (
                f"\n\n[協作提示] 若你認為 Bot-{other} 的觀點能明顯加值"
                f"（跨領域、需要第二意見、或你不確定），可在回覆結尾 @他徵詢："
                f"<@{other_id}>。不需要時就獨立答完，不要為了熱鬧而 @。"
            )
        prompt = f"{context}[from {message.author.display_name}] {cleaned_content}{mention_hint}"

        cwd = sessions.get_channel_cwd(message.channel.id)

        # bypass mode → plan-then-execute (unless !yolo). A bot-origin mention is the
        # conversation layer and must never drive execution (defense-in-depth, D3).
        if effective_mode == "bypass" and not is_bot_msg:
            await run_plan_then_execute(message.channel, bot_name, prompt, skip_plan=yolo, cwd=cwd)
            await memory.maybe_token_flush(message.channel, bot_name, cwd)
            return

        # Standard call. Layer split (D3): a bot-origin mention → converse() (plan),
        # regardless of channel mode. Only a human-driven edit-tier request reaches the
        # execution layer — and in M1 that runs as a streaming, cancellable BACKGROUND
        # JOB (never blocking conversation), not a synchronous call.
        if runner.exec_layer_for(is_bot_msg, effective_mode) == "execute":
            await start_exec_job(message, bot_name, prompt, effective_mode, cwd)
            return

        spf = memory.build_combined_system_prompt(message.channel.id, cwd, bot_name)
        async with state.bot_locks[bot_name]:
            async with message.channel.typing():
                reply, _ok = await runner.converse(
                    bot_name, prompt, system_prompt_file=spf, cwd=cwd,
                )
        memory.record_bot_reply(message.channel.id, bot_name, reply, cwd=cwd)
        cwd_tag = "" if cwd == config.DEFAULT_CWD else f"[{Path(cwd).name}] "
        prefix = f"**[mode={effective_mode} · once]** " if once_mode else ""
        for c in chunk_message(cwd_tag + prefix + reply):
            await message.channel.send(c)
        await memory.maybe_token_flush(message.channel, bot_name, cwd)

    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.message_id not in state.pending_actions:
            return
        if payload.user_id not in config.ALLOWED_USER_IDS:
            return
        if payload.user_id in state.bot_user_ids.values():
            return  # ignore bot's own reactions
        fut = state.pending_actions.pop(payload.message_id, None)
        if fut is None or fut.done():
            return
        emoji = payload.emoji.name
        if emoji == "✅":
            fut.set_result("confirm")
        elif emoji == "❌":
            fut.set_result("cancel")

    return client


# ── Startup announcement ────────────────────────────────────────────────
STARTUP_ANNOUNCEMENT = """🚀 **Bridge v3 上線**

**🧠 記憶（四層 · 按專案切）**
• `!flush` 立即整理對話 → summary（+ 在專案內時一併更新專案筆記）
• `!reset A|B` 清掉 bot session（summary 保留）
• 每 {auto} 則訊息自動 flush；summary / 專案筆記都**按 cwd 分開**
• context 兩段式自動管理：400k 寫 summary 存檔、700k 濃縮+重置對話線（1M 視窗）
• `!cd <專案>` 切走時自動把舊專案進度寫入專案筆記
• 每次回應自動注入「該專案的 summary + 專案筆記」當 context
• `!state` 可看兩帳號 5h/7d 用量（cswap）

**🎭 多模式對話**
• `@A`、`@B` 單獨叫 — 自然回，被 @ 才接話
• `@A @B` 一起 — 並行雙視角
• `!discuss <主題>` — 強制 A↔B 輪流辯論

**⚙️ 背景執行任務**
• 執行層任務（edit/approve）改走背景 job：即時進度、可 `!cancel`、獨立逾時
• `!jobs` 看執行中的任務  •  `!cancel <id>` 取消（終止整個 process group）

**🔐 授權執行**
• `!mode plan|edit|bypass` 切 channel 模式
• `!once <mode>` 單訊息 override
• bypass 預設會先給 plan 等你 ✅ 才執行
• `!yolo` 跳過 plan 確認

**ℹ️ 其他**
• `!state` 看當前狀態  •  `!help` 完整指令參考

預設模式：**`plan`**（只讀規劃）。要寫檔請先 `!mode edit`。
""".format(auto=config.AUTO_FLUSH_THRESHOLD)

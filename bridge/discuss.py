"""A↔B sequential debate orchestration (conversation layer).

Imports converse (runner) and the memory helpers; never imports execute or the
private chokepoint.
"""
from __future__ import annotations

import discord

from bridge import config, memory, runner, sessions, state
from bridge.util import chunk_message


async def run_discuss(channel: discord.TextChannel, topic: str) -> None:
    """A↔B sequential debate over a SHARED rolling transcript.

    - Independent turn budget (does NOT touch the global bot-turn counter).
    - Each turn sees the FULL debate transcript plus recent channel context.
    - On completion, writes a summary into the shared knowledge base.
    """
    async with state.discuss_locks[channel.id]:
        cwd = sessions.get_channel_cwd(channel.id)
        seed = memory.build_context_prefix(channel.id, limit=12)
        transcript: list[str] = []

        for round_num in range(config.MAX_BOT_TURNS):
            bot_name = "A" if round_num % 2 == 0 else "B"
            round_label = (round_num // 2) + 1
            convo = "\n\n".join(transcript) if transcript else "（你是第一位發言）"
            prompt = (
                f"{seed}"
                f"=== 辯論主題 ===\n{topic}\n\n"
                f"=== 目前辯論進展（完整）===\n{convo}\n\n"
                f"=== 輪到你（{bot_name}）===\n"
                "看完整脈絡後接話：明確表態同意/反對/補充，給具體理由，2-4 句。"
                "不要客套、不要只說「對」。若你認為已收斂可在結尾寫「辯論結束」。"
            )
            # Behaviour-preserving: the original debate turns ran in DEFAULT_CWD with
            # the default-cwd summary (converse was called with no cwd kwarg); only the
            # transcript tag + final summary use the channel cwd.
            spf = memory.build_combined_system_prompt(channel.id, config.DEFAULT_CWD, bot_name)
            async with state.bot_locks[bot_name]:
                async with channel.typing():
                    reply, ok = await runner.converse(
                        bot_name, prompt, use_session=False,
                        system_prompt_file=spf,
                    )
            if not ok:
                await channel.send(f"⚠️ discuss 中斷：{reply}")
                return
            transcript.append(f"[{bot_name} 第{round_label}輪]\n{reply}")
            memory.record_bot_reply(channel.id, bot_name, f"(discuss) {reply}", cwd=cwd)
            header = f"**[discuss {round_num + 1}/{config.MAX_BOT_TURNS} · {bot_name}]**"
            for chunk in chunk_message(f"{header}\n{reply}"):
                await channel.send(chunk)
            if any(kw in reply for kw in ["辯論結束", "結束辯論"]):
                break

        await channel.send(f"_discuss 結束（{len(transcript)} 輪）· 正在整理結論…_")
        debate_text = "\n\n".join(transcript)
        flush_prompt = (
            "以下是一場 A↔B 辯論。請濃縮成 markdown：(1) 辯論主題 "
            "(2) 雙方核心論點 (3) 共識/結論 (4) 仍未解決的分歧。300 字內。\n\n"
            f"主題：{topic}\n\n{debate_text}"
        )
        summary, ok = await runner.converse("B", flush_prompt, use_session=False)
        if ok:
            path = memory.save_summary(channel.id, summary, cwd)
            await channel.send(f"📝 辯論結論已存：`{path.name}`")

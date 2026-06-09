# ai-discord-bridge

> 中文版本： [README.zh.md](README.zh.md) ｜ Design spec： [SPEC.md](SPEC.md) ｜ Threat model： [SECURITY.md](SECURITY.md)

Self-hosted dual-AI Discord companion — a working personal-scale reference implementation of a four-layer memory model and debate orchestration pattern for Claude Code.

> **Status**: Personal experiment / MVP. Single-channel, no test suite, no support SLA. Fork it, adapt it — don't file issues expecting maintenance.

> ⚠️ **Security**: this tool lets whitelisted Discord users run code as your host user inside the directories you mount (`bypass` mode = arbitrary execution). **Read [SECURITY.md](SECURITY.md) ([中文](SECURITY.zh.md)) before deploying** — the threat model and hardening checklist are not optional reading for this kind of project.

It's a **control plane over Claude Code**: two Discord bots (Bot-A, Bot-B) run in one Docker container; an `@`-mention becomes a `claude -p --resume <sid>` call with channel context, four-layer memory, and per-channel permission modes layered on top. Useful as a reference for **dual-agent orchestration, memory layering, and a Discord control plane** — not as a turnkey product. Each bot is bound to its own Claude Code config dir (`~/.claude/`, `~/.claude-b/`); auth/billing options are covered under [Auth modes](#auth-modes) below.

## Architecture Highlights

- **Four-layer memory**: per-session `.jsonl` → per-(channel, cwd) mid-term summary → per-cwd project notes → global long-term profiles (read-only in container)
- **Flush-before-compaction**: triggered on `!flush`, message threshold, and `!cd` project switch — preserves decisions before Claude's context window auto-compacts
- **Dual-agent debate**: `!discuss <topic>` — A and B take turns on a shared rolling transcript with an independent turn budget that doesn't starve normal @-mentions
- **Permission layers**: `plan` / `edit` / `bypass` modes per channel; `bypass` requires explicit whitelist; fail-closed auth + prompt-injection isolation + credential-read deny (see [SECURITY.md](SECURITY.md))

## Prerequisites

- Two Claude Code accounts (Pro or Max), logged in to `~/.claude/` and `~/.claude-b/` on the host
- Two Discord bot tokens (one per account)

<a id="auth-modes"></a>
### Auth modes

- **API-key mode** (`USE_API_KEY=true` + per-bot `ANTHROPIC_API_KEY_A`/`_B`) — **the intended path for public/forker use.** It bills the Developer Platform, which is the cleaner ToS footing for an automated bot. ⚠️ Its billing routing is **not yet verified against a live key** (see [SPEC.md](SPEC.md) §9) — verify with a **spend-capped** key before relying on it, and note the key lives in the subprocess env ([SECURITY.md](SECURITY.md) §6).
- **Subscription mode** (default, mounted `~/.claude{,-b}` credentials) — kept for the author's personal/local setup. Running an automated bot on subscription credentials is a grayer ToS area, so treat this as a *compatibility default, not a recommendation*. In this mode `claude -p` consumes **Agent SDK credits** (a pre-paid pool: Pro $20 / Max 5× $100 / Max 20× $200; hard-stops when exhausted). Set `MAX_BOT_TURNS` conservatively to control spend.

## Discord Setup

1. Create a server (or use an existing one) with a `#ai-chat` channel
2. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create **two** applications: `Claude-A` and `Claude-B`
3. For each application:
   - Bot tab → Add Bot → copy the **Token**
   - Privileged Gateway Intents → enable `MESSAGE CONTENT INTENT`
   - OAuth2 → URL Generator → scopes: `bot`, permissions: `Send Messages` + `Read Message History`
   - Use the generated URL to invite the bot to your server
4. Enable Developer Mode in Discord (Settings → Advanced)
5. Right-click `#ai-chat` → copy Channel ID
6. Right-click your user → copy User ID

## Configuration

```bash
cp .env.example .env
# Fill in:
#   DISCORD_BOT_A_TOKEN
#   DISCORD_BOT_B_TOKEN
#   DISCORD_CHANNEL_ID
#   ALLOWED_USER_IDS   (your Discord user ID)
```

Copy `docker-compose.example.yml` to `docker-compose.yml` and edit the volume mounts to point to your actual project directories.

## Start

```bash
docker compose up -d --build
docker compose logs -f
```

## Verify your deployment (smoke test)

Unit tests (`pip install -r requirements-dev.txt && pytest`) cover the security-critical logic — fail-closed auth, `!cd` path/escape guard, trust filtering, subprocess env scrubbing — and run in CI. They don't touch live Discord/Claude, so confirm end-to-end wiring by hand:

1. `docker compose config` — the compose file parses and mount paths resolve.
2. **Fail-closed auth**: start with `ALLOWED_USER_IDS` empty → the container must exit immediately (`refusing to start`). Set it back to your id.
3. **Bots online**: `docker compose logs` shows both A and B `logged in as ...`.
4. **From a whitelisted account** in the channel: `!help`, `!state`, `!mode plan`, `!cd <your-project>`, then `@Bot-A hello` → A replies.
5. **API-key mode** (if you enable it): set `USE_API_KEY=true` with the keys empty → the container must refuse to start.

## Usage

In `#ai-chat`:

| Input | Effect |
|-------|--------|
| `@Bot-A <message>` | Only A replies |
| `@Bot-A @Bot-B <message>` | Both reply |
| A mentions `@Bot-B` in reply | B responds (debate mode) |
| You send any message | Resets A↔B turn counter |

**Commands** (prefix with `!`, handled by Bot-A to avoid double-triggering):

| Command | Effect |
|---------|--------|
| `!cd /path/to/project` | Switch working directory; flushes previous project context first |
| `!flush` | Manual context flush — saves mid-term summary + project notes |
| `!discuss <topic>` | Structured A↔B debate with shared rolling transcript |
| `!mode plan\|edit\|bypass` | Set permission mode for this channel |
| `!reset a\|b` | Clear one bot's session (summary preserved) |
| `!state` | Show channel state, buffer stats, summary status |

> Full command table (with session semantics + permission columns) is in [SPEC.md](SPEC.md) §5.

A↔B turn counter hard-stops at `MAX_BOT_TURNS` (default 6).

## Why the Same Absolute Paths in Bind Mounts

`~/.claude/skills` is a symlink to `~/.claude-shared/skills/`, and `CLAUDE.md` uses `@/home/user/.claude-shared/CLAUDE.md` (absolute path import). The container must mount to the **same absolute paths** `/home/user/.claude{,-b,-shared}` — otherwise symlinks and `@import` directives break silently.

The `memory/` subdirectory is mounted **read-only** to prevent write races between the bot container and interactive host sessions.

## Known Limitations

1. **Single channel** — MVP hardcodes one channel ID. Multi-channel routing is on the backlog.
2. **OAuth refresh race** — bot and host may race on token refresh. Rare in practice; accepted for MVP.
3. **No attachments**, no thread/reply nesting, no slash commands — future backlog.
4. **Limited tests** — unit tests cover the security-critical pure logic (auth, path/trust guards, env scrub) and run in CI; there are no integration tests against live Discord/Claude (manual smoke test for that).

## No Support

This is a personal daily-use project, not a maintained library. PRs are welcome but I can't guarantee reviews or timely responses. If something breaks for you, the entire implementation is in `bot.py`.

## License

MIT — see [LICENSE](LICENSE).

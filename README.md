# ai-discord-bridge

> 中文版本： [README.zh.md](README.zh.md) ｜ Design spec： [SPEC.md](SPEC.md) ｜ Threat model： [SECURITY.md](SECURITY.md)

Self-hosted dual-AI Discord companion — a working personal-scale reference implementation of a four-layer memory model, debate orchestration, and a **review-gated execution loop** for Claude Code.

> **Status**: Personal experiment. Single-channel, no support SLA. Fork it, adapt it — don't file issues expecting maintenance. The security-critical logic is covered by a 230-test pytest suite running in CI.

> ⚠️ **Security**: this tool lets whitelisted Discord users run code as your host user inside the directories you mount. **Read [SECURITY.md](SECURITY.md) ([中文](SECURITY.zh.md)) before deploying** — the threat model and hardening checklist are not optional reading for this kind of project.

It's a **control plane over Claude Code**: two Discord bots (Bot-A, Bot-B); an `@`-mention becomes a `claude -p --resume <sid>` call with channel context, four-layer memory, and per-channel permission modes layered on top. Execution-mode tasks run as **background jobs in throwaway git worktrees** and post a diff you approve before anything touches your checkout. Useful as a reference for **dual-agent orchestration, memory layering, egress containment, and a Discord control plane** — not as a turnkey product. Each bot runs under a dedicated minimal config dir (`~/.claude-bot-{a,b}`) with a cron-synced read-only staged copy of your account's credential ([SECURITY.md](SECURITY.md) §9); auth/billing options are covered under [Auth modes](#auth-modes) below.

## Architecture Highlights

- **Four-layer memory**: per-session `.jsonl` → per-(channel, cwd) mid-term summary → per-cwd project notes → global long-term profiles (thin index only, read-only in container)
- **Flush-before-compaction**: triggered on `!flush`, message threshold, token thresholds, and `!cd` project switch — preserves decisions before Claude's context window auto-compacts
- **Dual-agent debate**: `!discuss <topic>` — A and B take turns on a shared rolling transcript with an independent turn budget that doesn't starve normal @-mentions
- **Review-gated exec loop**: execution-mode tasks become background jobs (`!jobs` / `!cancel`) streaming live progress; changes land in a throwaway git worktree, and the resulting diff waits for your ✅ to merge / ❌ to discard (`!merge` / `!discard` after a timeout). Optional per-project **post-task verification** and an optional **second-account advisory review** post above the gate.
- **Permission tiers**: `plan` / `edit` / `approve` / `bypass` per channel; `approve` is a per-command human-approval tier (MCP approver), `bypass` is structurally unreachable unless opted in; fail-closed auth + prompt-injection isolation + a canary-proven credential-read deny family (see [SECURITY.md](SECURITY.md))
- **Egress containment (two-container split)**: a `discord-frontend` (Discord egress only, holds bot tokens) and an `executor` (Anthropic egress only, holds Claude credentials) on routeless internal networks behind default-deny proxies — each secret lives where the other secret's egress can't reach. Fail-closed startup canaries prove each deny direction.

## Prerequisites

- Two Claude Code accounts (Pro or Max), logged in on the host
- Two Discord bot tokens (one per account)
- Dedicated minimal bot config dirs `~/.claude-bot-{a,b}` + the credential staging dir & sync cron — see [SECURITY.md](SECURITY.md) §9 for the one-time setup

<a id="auth-modes"></a>
### Auth modes

- **API-key mode** (`USE_API_KEY=true` + per-bot `ANTHROPIC_API_KEY_A`/`_B`) — **the intended path for public/forker use.** It bills the Developer Platform, which is the cleaner ToS footing for an automated bot. The key does **not** enter the subprocess env: it is materialized as a `0600` file behind an `apiKeyHelper` script (see [SECURITY.md](SECURITY.md) §6). ⚠️ The billing *routing* is **not yet verified against a live key** (see [SPEC.md](SPEC.md) §10) — verify with a **spend-capped** key before relying on it.
- **Subscription mode** (default, each account's `.credentials.json` reaching the container as a cron-synced read-only staged copy — [SECURITY.md](SECURITY.md) §9) — kept for the author's personal/local setup. Running an automated bot on subscription credentials is a grayer ToS area, so treat this as a *compatibility default, not a recommendation*. In this mode `claude -p` consumes **Agent SDK credits** (a pre-paid pool: Pro $20 / Max 5× $100 / Max 20× $200; hard-stops when exhausted). Set `MAX_BOT_TURNS` conservatively to control spend.

## Discord Setup

1. Create a server (or use an existing one) with a `#ai-chat` channel
2. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create **two** applications: `Claude-A` and `Claude-B`
3. For each application:
   - Bot tab → Add Bot → copy the **Token**
   - Privileged Gateway Intents → enable `MESSAGE CONTENT INTENT`
   - OAuth2 → URL Generator → scopes: `bot`, permissions: `Send Messages` + `Read Message History` + `Add Reactions` + `Attach Files`
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

Copy `docker-compose.example.yml` to `docker-compose.yml` and edit the project bind mounts + `PROJECT_DIRS` (both services) to point to your actual project directories. The example compose is the **two-container split** (frontend / executor / two egress proxies); read its header comments — several env vars are cross-process coupled and must match on both services.

## Start

```bash
docker compose up -d --build
docker compose logs -f
```

## Verify your deployment (smoke test)

Unit tests (`pip install -r requirements-dev.txt && pytest`, 230 tests) cover the security-critical logic — fail-closed auth, `!cd` path/escape guard, trust filtering, env scrubbing, the exec-loop job/worktree/verify machinery, egress canary logic — and run in CI. They don't touch live Discord/Claude, so confirm end-to-end wiring by hand:

1. `docker compose config` — the compose file parses and mount paths resolve.
2. **Fail-closed auth**: start with `ALLOWED_USER_IDS` empty → the container must exit immediately (`refusing to start`). Set it back to your id.
3. **Canaries green**: the logs show the egress canary (per container, split deploy) and the settings-deny canary passing before either bot serves.
4. **Bots online**: `docker compose logs` shows both A and B `logged in as ...`.
5. **From a whitelisted account** in the channel: `!help`, `!state`, `!mode plan`, `!cd <your-project>`, then `@Bot-A hello` → A replies.
6. **Exec round-trip**: `!mode edit`, ask a bot for a trivial file change → a background job streams progress, posts a diff, and your ✅ merges it (❌ discards).
7. **API-key mode** (if you enable it): set `USE_API_KEY=true` with the keys empty → the container must refuse to start.

## Usage

In `#ai-chat`:

| Input | Effect |
|-------|--------|
| `@Bot-A <message>` | Only A replies |
| `@Bot-A @Bot-B <message>` | Both reply |
| A mentions `@Bot-B` in reply | B responds (debate mode) |
| You send any message | Resets A↔B turn counter |

In `plan` mode (the default) a mention is a normal conversation call. In `edit` / `approve` / `bypass` mode it becomes a **background exec job**: work happens in a throwaway git worktree, progress streams into a status message, and the finished diff waits for your ✅/❌. Attachments on the triggering message are ingested as untrusted context.

**Commands** (prefix with `!`, handled by Bot-A to avoid double-triggering):

| Command | Effect |
|---------|--------|
| `!cd <project>` | Switch working directory (whitelisted git projects only); flushes previous project context first |
| `!mode plan\|edit\|bypass\|approve` | Set permission mode for this channel (`bypass`/`approve` need their opt-in tier) |
| `!jobs` | List background exec jobs (running / awaiting review) |
| `!cancel <id>` | Cancel a running job (kills the whole process group) |
| `!merge <id>` / `!discard <id>` | Merge or drop a parked awaiting-review diff |
| `!discuss <topic>` | Structured A↔B debate with shared rolling transcript |
| `!flush` | Manual context flush — saves mid-term summary + project notes |
| `!reset a\|b` | Clear one bot's session (summary preserved) |
| `!state` | Show channel state, cwd, context tokens, account usage |

> Full command table (with session semantics + permission columns) is in [SPEC.md](SPEC.md) §5; the exec loop is §6.

A↔B turn counter hard-stops at `MAX_BOT_TURNS` (default 6).

## Why the Same Absolute Paths in Bind Mounts

Config and state paths are anchored at `/home/user/...` inside the container, and exec-job worktrees link back to each project's `.git` by absolute path. Mount your project directories and the `~/.claude-shared` state subdirectories at the **same absolute paths** as on the host (as the example compose does) — otherwise the `!cd` whitelist, worktree merges, and the shared-volume IPC socket break silently.

The `memory/project_plan.md` index and the M4 `discord-verify/` config are mounted **read-only** by design — the latter is what makes the post-task verification signal un-forgeable by the agent it checks ([SECURITY.md](SECURITY.md) §4).

## Known Limitations

1. **Single channel** — one hardcoded channel ID; the turn counter is global. Multi-channel routing is on the backlog.
2. **OAuth refresh race** — bot and host may race on token refresh. Rare in practice; in the split deploy the container mounts the credential read-only, so the host keeps it fresh.
3. **Restart loses in-memory state** — pending plan confirmations, buffers, and turn counters reset; sessions, summaries, and parked awaiting-review jobs persist. A job that finished committing but hadn't posted its diff yet is garbage-collected on restart (known finding, accepted for now).
4. **No thread/reply nesting, no slash commands** — future backlog.
5. **Tests are unit-level** — the suite covers the security-critical and exec-loop logic in CI; there are no integration tests against live Discord/Claude (manual smoke test above for that).

## No Support

This is a personal daily-use project, not a maintained library. PRs are welcome but I can't guarantee reviews or timely responses. If something breaks for you, the implementation lives in `bridge/` (entrypoints: `bot.py`, `executor.py`).

## License

MIT — see [LICENSE](LICENSE).

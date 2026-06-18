# Preflight gate findings (tasks 0.1 / 0.2)

Empirically verified 2026-06-17 against the installed `claude` and the actual
container image. **These results materially change M1 — D1 and D2 do not hold as
designed.** Recorded here before any M1 code lands.

## Environment
- `claude` version: **2.1.179 (Claude Code)**
- Container image: `ai-discord-bridge-ai-discord-bridge:latest` (python:3.12-slim, runs as `1000:1000`)
- Host: bubblewrap present, unprivileged userns works.

## 0.1 — Behavior gate (OV2): allow-list semantics in headless `-p`

Probes run with an isolated `CLAUDE_CONFIG_DIR=~/.claude-b` (settings `{}`), so the
operator's permissive `~/.claude` allow-list does not contaminate the result.

| # | Setup | Asked to run | Outcome |
|---|-------|--------------|---------|
| D | `--permission-mode default`, no `--allowedTools` | `whoami` | **RAN** (no denials) |
| F | `--permission-mode default --allowedTools "Bash(echo:*)"` | `echo`, `whoami` | **BOTH RAN** (no denials) |
| A | `--permission-mode acceptEdits --allowedTools "Bash(echo:*)"` | `echo`, `whoami` | **BOTH RAN** (no denials) |
| E | `--settings <deny whoami>` , default mode | `whoami` | **BLOCKED** — appeared in `permission_denials` |
| G | `--permission-mode plan` | `echo` (read-only) | RAN (plan permits read-only bash; blocks edits/writes) |

### Verdict
- **Headless `claude -p` defaults to ALLOW for Bash** — with no interactive approver
  and no `--permission-prompt-tool`, tool calls just execute.
- **`--allowedTools` is ADDITIVE (pre-approve), NOT a restrictive boundary.** Adding
  `Bash(echo:*)` does not deny `whoami`. **D1's premise — "allow-listed runs,
  non-allow-listed does not execute" — is false in this version.** Building M1 on it
  would *silently allow* arbitrary commands (the exact leak we set out to close).
- **`permissions.deny` via `--settings` DOES enforce, deterministically** (Probe E).
  This is the one tool-layer mechanism that actually contains in headless.
- The only ways to get a true *restrictive* execution boundary in headless are:
  (a) a `--permission-prompt-tool` MCP approver that returns deny-by-default (this is
  M4 — it would become the *mandatory* execution gate, not optional), or
  (b) `--permission-mode plan` (no execution at all), or
  (c) rely on `permissions.deny` as the containment (a deny-list — D1 itself rejected
  this as "never exhaustive against arbitrary shell").

## 0.2 — Hard gate (OV5): can the OS sandbox start in the container?

Inside `ai-discord-bridge-ai-discord-bridge:latest`, as user `1000:1000`:
- `command -v bwrap` → **NO_BWRAP_IN_IMAGE** (Dockerfile never installs bubblewrap).
- `unshare --user --map-root-user echo` → **`unshare failed: Operation not permitted`**
  (unprivileged user namespaces blocked under Docker's default seccomp/caps for the
  non-root user).

### Verdict
- Claude Code's bubblewrap sandbox **cannot start** in the current container runtime,
  for two independent reasons (binary absent AND userns denied).
- With `sandbox.enabled: true, failIfUnavailable: true`, **every execution call would
  fail closed → the bot goes mute on every task.** This is the silent-degrade the gate
  exists to prevent.
- Fallback options (must be chosen before M1):
  - **(A) Restore the OS keystone:** add `bubblewrap` to the Dockerfile AND relax the
    container runtime (`--security-opt seccomp=unconfined`, possibly userns config) so
    bwrap can create namespaces. Cost: weaker container isolation, compose changes.
  - **(B) Accept "no OS layer":** set `sandbox.enabled: false`; rely on `permissions.deny`
    + plan-mode default + whitelist + (future) MCP approver. Cost: deny-by-name is
    bypassable (`/usr/bin/cu*rl`, `python -c`, `cat /proc/self/environ`); residual risk
    is explicit and must be documented in SECURITY.md.

## Net effect on M1
Both keystones M1 was architected on (restrictive allow-list, OS sandbox) are
unavailable as designed. The enforceable controls that remain are
**`permissions.deny`**, **plan-mode default (no exec)**, and the **MCP approver (M4)**.
M1 must be re-grounded on those. Operator decision required (see the two fallbacks
above) before implementing tasks 2.x / 3.x.

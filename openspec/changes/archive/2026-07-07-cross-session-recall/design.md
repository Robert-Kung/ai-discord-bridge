## Context

The four-layer memory model writes mid-term summaries to
`SUMMARIES_DIR/<channel>/<cwd-slug>/` (one timestamped markdown file per flush,
via `save_summary`). On each call, `build_combined_system_prompt` injects only
`latest_summary_path(...)` plus project notes; every older summary is write-only.
Session transcripts persist in full under the bot config dir
(`<config_dir>/projects/<cwd-slug>/<sid>.jsonl`) — which is **deny-listed**
(`Read(//home/user/.claude-bot-{a,b}/**)`), so the agent cannot read them; the
bot keeps a per-(bot,cwd) session pointer but no link from a summary to the
session it condensed.

Two facts drive the downsized design (review D4):
1. The conversation layer runs in **plan mode** — the agent has Read/Grep/Glob —
   and `discord-summaries/` is mounted in-container and not denied. Retrieval
   capability already exists; only *awareness* (the path + when to look) is missing.
2. Summaries are Chinese. Keyword recall (`rg -i` over OR'd whitespace tokens)
   performs poorly on unsegmented CJK, undermining the original pipeline's
   relevance premise.

## Goals / Non-Goals

**Goals:**
- The agent knows where the older summaries live and searches them on demand
  when the conversation references past decisions.
- Each written summary carries the session id it condensed (frontmatter),
  best-effort, for operator-side tracing.
- Zero new trust surface: only trust-filtered summary content is reachable.
- Zero new dependencies or pipelines.

**Non-Goals:**
- Bridge-side retrieval (`extract_recall_terms`/rg pipeline, caps machinery,
  ripgrep in the image) — rejected this revision; escalation path below.
- FTS5/SQLite/embeddings, provider abstraction, Honcho user modeling, skill
  evolution (all previously rejected vs. Hermes Agent; still rejected).
- Recall over raw session transcripts (deny-listed) or the global profile layer.
- Multi-channel routing (separate change).

## Decisions

- **Pointer injection, not bridge-side retrieval.** `build_combined_system_prompt`
  appends a short fixed section after latest-summary + project-notes, e.g.:
  `# 歷史摘要（跨 session）\n更早的頻道摘要在 <SUMMARIES_DIR/<channel>/<cwd-slug>/>（一檔一次 flush，檔名含時間戳；latest.md 已在上方）。當使用者提到更早的決定/討論而上方摘要沒有涵蓋時，先用 Grep/Read 搜尋該目錄再回答。`
  One file, one `--append-system-prompt-file`, as today. The agent's own LLM-driven
  search (it can grep for names, read candidate files, judge relevance) replaces
  keyword extraction — strictly better relevance on CJK, cost only when used.
  *Alternative considered:* the original rg pipeline — rejected: duplicates the
  agent's existing capability (DRY), adds a pure-Python CJK tokenization problem
  it can't solve well, and injects guessed-relevant text on every call whether
  needed or not.
- **Lineage in frontmatter.** `save_summary` accepts an optional
  `parent_session_id` and writes YAML frontmatter (`parent_session_id`, `cwd`,
  `ts`). The reset/flush callers (`flush_session_then_reset`, the reset branch of
  `maybe_token_flush`, `do_flush`) pass the session id known for that (bot, cwd)
  BEFORE it is cleared. Omitted (no placeholder) when unknown. Read paths
  (`latest_summary_path` consumers) tolerate and strip the frontmatter.
  Documented limitation: transcripts are deny-listed, so the pointer serves the
  operator (tracing which conversation a summary condensed), not the agent.
- **Escalation path (recorded, not implemented):** if observation shows the agent
  does not search when it should (user says "we decided X last week", agent
  answers without grepping), first strengthen the pointer wording; only if that
  fails, revisit bridge-side retrieval — and then with CJK-aware matching, not
  the whitespace tokenizer.

## Risks / Trade-offs

- **Agent may not search when it should** → pointer wording names the trigger
  condition explicitly; escalation path above; cheap to iterate (one string).
- **Extra tool-use iterations on recall-heavy questions** → bounded by the
  agent's own judgment; strictly cheaper than always-on injection for the
  common (no-recall-needed) case.
- **Frontmatter parsed as content by older readers** → minimal known key set;
  `latest_summary_path` read path strips it; body round-trips unchanged.
- **Trust regression** → summaries are produced by the trust-filtered write
  path; test asserts untrusted buffer content never reaches a summary and thus
  can never be recalled.

## Migration Plan

- Lands after `bridge-restructure` (touches `bridge/memory.py`).
- Backward compatible: existing summaries without frontmatter remain readable;
  lineage is best-effort going forward. No data migration, no image change.
- Rollback: revert; existing summaries untouched.

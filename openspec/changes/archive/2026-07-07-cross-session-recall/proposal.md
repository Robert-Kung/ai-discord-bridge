## Why

Today a bot only "remembers" the resumed Claude session plus the single latest mid-term summary injected per call. Once a session is reset (700k token boundary, `!reset`, or a `!cd` project switch), earlier decisions become unreachable: the summaries are written to disk but never read back except for the most recent one (verified: `save_summary` writes timestamped files + `latest.md`; `build_combined_system_prompt` reads only `latest.md`). Users hit this as "the bot forgot what we decided last week" even though the record exists on disk.

**Downsized at review (D4).** The original spec built a bespoke recall pipeline (`extract_recall_terms` + ripgrep over summaries, injected per call). Re-examination found the capability already exists: the conversation layer runs `claude -p` in **plan mode** (Read/Grep/Glob available), and `discord-summaries/` is bind-mounted into the container and absent from the deny list — **the agent can already search the whole summary tree on demand**. Additionally, summaries are Chinese; whitespace tokenization + `rg -i` keyword OR-matching is at its weakest on unsegmented CJK text, so the pipeline's core relevance assumption was weakest in exactly this deployment. The bespoke pipeline duplicated existing capability with a dubious quality premise.

## What Changes

- **Recall pointer injection (~3 lines)**: `build_combined_system_prompt` appends a short section giving the agent the exact on-disk path of the older summaries for this (channel, cwd) plus one instruction: when the user references past decisions/discussions not covered by the latest summary, search that directory (Grep/Read) before answering. LLM-driven, on-demand retrieval — no new code surface, no keyword extraction, token cost only when actually used.
- **Parent-session lineage**: when a summary is written at a session reset/flush, record the session id it condensed in the summary file's frontmatter, so a lossy summary always carries a pointer back to the full transcript it came from. Note: transcripts live under the deny-listed bot config dirs, so this pointer is **operator-side** tooling value (tracing/debugging), not agent-followable — documented as such.
- Recall respects the existing **trust boundary**: only content already admitted into summaries (whitelisted humans + our own A/B bots) is reachable — no new trust surface; the trust-boundary test ships regardless of retrieval mechanism.
- **Explicitly rejected (this revision)**: the bespoke `extract_recall_terms` + ripgrep recall pipeline, the ripgrep Dockerfile addition, snippet/token cap machinery — replaced by the pointer approach. Revisit only if observation shows the agent fails to search when it should (the escalation path is recorded in design.md).

## Capabilities

### New Capabilities
- `cross-session-recall`: the conversation call carries a pointer to the historical summary tree and an instruction to search it on demand; written summaries carry parent-session lineage metadata.

### Modified Capabilities
<!-- None: the four-layer memory model and trust layers are unchanged at the requirement level. -->

## Impact

- **Code** (`bridge/memory.py`, after `bridge-restructure`): `save_summary` gains optional frontmatter; reset/flush callers pass the session id; `build_combined_system_prompt` appends the pointer section. No new dependencies, **no Dockerfile change** (ripgrep no longer needed).
- **Sequencing**: after `bridge-restructure` (the same functions move to `bridge/memory.py`).
- **Out of scope (explicitly not doing)**: the rg/FTS5/SQLite/embedding recall pipeline (rejected above), provider abstraction, Honcho-style user modeling, autonomous skill creation/evolution, recall over raw session transcripts.

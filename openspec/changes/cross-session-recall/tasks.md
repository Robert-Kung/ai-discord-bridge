## 1. Lineage (parent session id in frontmatter) — DONE

- [x] 1.1 Extend `save_summary` to accept an optional `parent_session_id` and write YAML frontmatter (`parent_session_id`, `cwd`, `ts`) ahead of the summary body
- [x] 1.2 Pass the known (bot, cwd) session id into `save_summary` from the reset/flush callers (`flush_session_then_reset`, the reset branch of `maybe_token_flush`, `do_flush`) BEFORE the session pointer is cleared
- [x] 1.3 Make the summary read paths frontmatter-tolerant: `latest_summary_path` consumers strip frontmatter before use
- [x] 1.4 Unit tests: frontmatter written when a sid is known; omitted (no placeholder, no error) when unknown; body round-trips unchanged; injected latest-summary text carries no frontmatter

## 2. Recall pointer injection — DONE

- [x] 2.1 `build_combined_system_prompt` appends the fixed `# 歷史摘要（跨 session）` pointer section (exact per-(channel,cwd) summaries dir path + search-on-demand instruction) after latest-summary + project-notes
- [x] 2.2 Emit the section only when older summary files exist (beyond `latest.md`) — no empty pointer
- [x] 2.3 Unit tests: pointer present iff older summaries exist; path is the correct (channel, cwd-slug) dir; wording includes the trigger condition

## 3. Trust + docs — DONE

- [x] 3.1 Test: untrusted buffer content never reaches a summary and therefore can never be recalled (trust boundary preserved regardless of retrieval mechanism)
- [x] 3.2 Run full pytest suite; update README/SPEC memory section: cross-session recall = agent-searched summary tree via injected pointer + parent-sid lineage (operator-side); note the rejected rg pipeline + escalation path

> Note: lineage is stamped only in `flush_session_then_reset` (which condenses a specific bot session then clears it — the one place a parent session id is unambiguous). `do_flush` summarises the shared side-channel buffer, not a single session, so it correctly writes no parent pointer (spec: omitted when unknown).

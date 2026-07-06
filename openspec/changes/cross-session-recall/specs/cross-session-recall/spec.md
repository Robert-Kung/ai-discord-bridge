## ADDED Requirements

### Requirement: Recall relevant past summaries into a conversation call

On an `@`-mention conversation call, the system SHALL search the on-disk mid-term
summary files for the current channel — scoped to the active cwd — for passages
relevant to the incoming message, and inject the top matches into the call's
system prompt in addition to the latest summary and project notes.

#### Scenario: Past decision recalled on mention

- **WHEN** a whitelisted user `@`-mentions a bot with a message whose terms match
  content in an older (non-latest) summary file for that channel+cwd
- **THEN** the matching passage(s) are included in the system prompt passed to
  `claude -p` for that call, so the reply can reflect the earlier decision

#### Scenario: No relevant history

- **WHEN** the incoming message matches nothing in the channel+cwd summary tree
- **THEN** the call proceeds with only the existing latest-summary + project-notes
  context and no recall section is injected (no error, no empty heading)

### Requirement: Recall is bounded and search-only

Recall SHALL be capped by both a maximum number of injected snippets and a
maximum injected-token budget, and SHALL use a text search (ripgrep) over the
markdown summary files only. The system SHALL NOT introduce an FTS5/SQLite store,
an embeddings index, or any non-markdown summary storage.

#### Scenario: Many matches are capped

- **WHEN** the incoming message matches more passages than the configured snippet
  or token cap
- **THEN** only the highest-ranked matches up to the caps are injected, so recall
  never crowds out the live transcript or overflows the context window

#### Scenario: Search backend stays file-based

- **WHEN** recall runs
- **THEN** it reads the existing `SUMMARIES_DIR/<channel>/<cwd-slug>/` markdown
  files directly (via ripgrep) with no auxiliary index or database

### Requirement: Recall respects the existing trust boundary

Recall SHALL only surface content that was already admitted into summaries. It
SHALL NOT search any source that the trust layer excludes (third-party bots,
non-whitelisted humans), introducing no new content into the bot's context beyond
what the summary-write path already trusts.

#### Scenario: Untrusted content is not recallable

- **WHEN** the channel buffer contained messages from a non-whitelisted human or a
  third-party bot that were excluded from summaries by the trust filter
- **THEN** that content is absent from the summary files and therefore cannot be
  recalled into any call

### Requirement: Summaries record their parent session lineage

When the system writes a summary at a session reset or flush, it SHALL record, in
the summary file's frontmatter, the Claude session id that the summary condensed
(when one is known), so a lossy summary retains a pointer back to the full
transcript it was derived from.

#### Scenario: Reset writes parent session id

- **WHEN** a session is summarized and reset (e.g. at the reset token threshold or
  via `!reset`) and a current session id is known for that (bot, cwd)
- **THEN** the written summary file's frontmatter contains that session id as its
  parent/condensed-session pointer

#### Scenario: Flush without a known session id

- **WHEN** a summary is written but no session id is known for that (bot, cwd)
- **THEN** the summary is still written successfully, with the parent-session
  pointer omitted (no placeholder, no failure)

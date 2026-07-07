## ADDED Requirements

### Requirement: Conversation calls carry a pointer to the historical summary tree

The combined system prompt SHALL, on an `@`-mention conversation call whose
(channel, cwd) has summaries older than the latest one, include the exact on-disk path
of that channel+cwd's summary directory and an instruction to search it (with the
agent's own read/search tools) when the incoming conversation references past
decisions or discussions not covered by the injected latest summary. Retrieval is
performed by the agent on demand; the bridge SHALL NOT implement its own search
pipeline, index, or snippet injection.

#### Scenario: Pointer present when history exists

- **WHEN** a conversation call is built for a (channel, cwd) whose summary
  directory contains older summary files beyond `latest.md`
- **THEN** the combined system prompt includes the directory path and the
  search-on-demand instruction, after the latest summary and project notes

#### Scenario: No pointer without history

- **WHEN** the (channel, cwd) has no summary files older than the latest
- **THEN** no pointer section is injected (no empty heading, no error)

#### Scenario: No bridge-side retrieval infrastructure

- **WHEN** recall occurs
- **THEN** it is the agent reading the plain markdown summary files itself; the
  bridge adds no keyword extraction, no ripgrep invocation, no FTS5/SQLite store,
  and no embeddings index

### Requirement: Recall respects the existing trust boundary

Recall SHALL only be able to surface content that was already admitted into
summaries by the trust-filtered write path. Content the trust layer excludes
(third-party bots, non-whitelisted humans) SHALL never reach a summary file and
therefore can never be recalled.

#### Scenario: Untrusted content is not recallable

- **WHEN** the channel buffer contained messages from a non-whitelisted human or a
  third-party bot that were excluded from summaries by the trust filter
- **THEN** that content is absent from the summary files and therefore cannot be
  recalled into any call

### Requirement: Summaries record their parent session lineage

When the system writes a summary at a session reset or flush, it SHALL record, in
the summary file's frontmatter, the Claude session id that the summary condensed
(when one is known), so a lossy summary retains a pointer back to the full
transcript it was derived from. The transcript files live under deny-listed bot
config dirs, so this pointer serves operator-side tracing, not agent retrieval.
Summary read paths SHALL tolerate and strip the frontmatter.

#### Scenario: Reset writes parent session id

- **WHEN** a session is summarized and reset (e.g. at the reset token threshold or
  via `!reset`) and a current session id is known for that (bot, cwd)
- **THEN** the written summary file's frontmatter contains that session id as its
  parent/condensed-session pointer

#### Scenario: Flush without a known session id

- **WHEN** a summary is written but no session id is known for that (bot, cwd)
- **THEN** the summary is still written successfully, with the parent-session
  pointer omitted (no placeholder, no failure)

#### Scenario: Frontmatter never leaks into injected context

- **WHEN** a summary bearing frontmatter is injected as the latest summary
- **THEN** the injected text contains the summary body only, with the frontmatter
  stripped

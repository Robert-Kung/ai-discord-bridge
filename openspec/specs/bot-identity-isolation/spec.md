# bot-identity-isolation

## Purpose
Run the bots under a dedicated minimal config dir (no operator personal data / primary account) and expose only an explicit allow-list of shared paths to the execution container, so the operator's credentials and PII/infra trove are outside the execution blast radius.

## Requirements

### Requirement: Bots run under a dedicated minimal config dir
Each bot SHALL authenticate using a dedicated `CLAUDE_CONFIG_DIR` that does not contain the operator's personal `CLAUDE.md` content (email, infrastructure topology, cross-agent setup) and is not the operator's primary interactive account dir. The minimal `CLAUDE.md` SHALL NOT `@import` the shared `CLAUDE.md`, since the infra/topology content enters through that import.

#### Scenario: Personal config is not loaded into bot calls
- **WHEN** a bot runs `claude -p`
- **THEN** the loaded `CLAUDE.md` chain contains no operator personal data and does not include the shared `CLAUDE.md` import

#### Scenario: Bot dir is distinct from the operator primary dir
- **WHEN** the bot config dir is resolved
- **THEN** it is not the operator's primary interactive `~/.claude` dir

### Requirement: Execution container exposes only an explicit allow-list of shared paths
The execution container SHALL mount only: (a) the bot's operational state from `.claude-shared` — `discord-state/`, `discord-summaries/`, `discord-project-notes/`; (b) the plan landing zone `.claude-shared/plans/`; and (c) the single file `.claude-shared/memory/project_plan.md` (the thin summary index). It SHALL NOT mount the `.claude-shared/memory/` directory as a whole — which holds the operator PII / infra topology trove (`infrastructure.md`, `user_profile.md`, `agent_*.md`, per-project detail files) — nor the shared `CLAUDE.md`. Any memory file the bot needs SHALL be exposed by explicit single-file mount, never by mounting the directory, so a newly added memory file does not become reachable by default. Where the OS sandbox is active, the credential files and the unmounted memory paths SHALL additionally be in `filesystem.denyRead`.

#### Scenario: PII/infra trove is not reachable from the execution path
- **WHEN** the execution path attempts to read `.claude-shared/memory/infrastructure.md` (or `user_profile.md`, `agent_*.md`)
- **THEN** the file is not present in the container (the directory is not mounted) or is denied by the sandbox

#### Scenario: The thin index is reachable but the directory is not
- **WHEN** the execution path reads `.claude-shared/memory/project_plan.md`
- **THEN** that single file is available, while sibling files in `.claude-shared/memory/` are not present in the container

#### Scenario: A newly added memory file does not leak
- **WHEN** a new file is added to `.claude-shared/memory/` on the host
- **THEN** it is not reachable from the execution container unless explicitly single-file mounted

#### Scenario: Cross-surface plan continuity is preserved
- **WHEN** the bot persists a full plan from Discord
- **THEN** it is written under the mounted `.claude-shared/plans/` (or a `discord-*` dir) that the operator's interactive CLI can read

### Requirement: Example deployment template represents the credential risk honestly
`docker-compose.example.yml` SHALL document the credential mount as the highest-value secret in the deployment and SHALL NOT contain claims implying the mounted credentials are unreachable by execution (e.g. "even bypass mode can't touch it" adjacent to credential mounts).

#### Scenario: Misleading isolation claim is absent
- **WHEN** the example template is inspected near the credential mount lines
- **THEN** no comment claims execution cannot reach the mounted credentials

#### Scenario: Credential mount is flagged
- **WHEN** a forker reads the credential mount lines in the example template
- **THEN** an accompanying note identifies these as live credentials reachable by the execution path
</content>

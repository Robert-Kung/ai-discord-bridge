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
The execution container SHALL mount only: (a) the bot's operational state from `.claude-shared` — `discord-state/`, `discord-summaries/`, `discord-project-notes/`; (b) the plan landing zone `.claude-shared/plans/`; and (c) the thin summary index `project_plan.md`, exposed as a read-only staged copy presented at `.claude-shared/memory/project_plan.md` (the staging dir holds only that copy). It SHALL NOT mount the `.claude-shared/memory/` directory as a whole — which holds the operator PII / infra topology trove (`infrastructure.md`, `user_profile.md`, `agent_*.md`, per-project detail files) — nor the shared `CLAUDE.md`. Any memory file the bot needs SHALL be exposed by an explicit staged copy, never by mounting the real directory, so a newly added memory file does not become reachable by default (staged-copy dirs also avoid the single-file bind-mount inode staleness on atomic replacement). Where the OS sandbox is active, the credential files and the unmounted memory paths SHALL additionally be in `filesystem.denyRead`.

#### Scenario: PII/infra trove is not reachable from the execution path
- **WHEN** the execution path attempts to read `.claude-shared/memory/infrastructure.md` (or `user_profile.md`, `agent_*.md`)
- **THEN** the file is not present in the container (the directory is not mounted) or is denied by the sandbox

#### Scenario: The thin index is reachable but the directory is not
- **WHEN** the execution path reads `.claude-shared/memory/project_plan.md`
- **THEN** that single file is available, while sibling files in `.claude-shared/memory/` are not present in the container

#### Scenario: A newly added memory file does not leak
- **WHEN** a new file is added to `.claude-shared/memory/` on the host
- **THEN** it is not reachable from the execution container unless explicitly staged into a mounted copy

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

### Requirement: API-key mode supplies keys via apiKeyHelper, never the subprocess env
In API-key mode the per-bot key SHALL be provided to `claude` via an `apiKeyHelper` script in the bot's config dir, and SHALL NOT be injected into the subprocess environment. The key file backing the helper SHALL live outside every mounted project directory and SHALL be covered by the credential-read deny family.

#### Scenario: Subprocess env carries no API key
- **WHEN** a `claude -p` subprocess env is built in API-key mode
- **THEN** it contains no `ANTHROPIC_API_KEY*` variable

#### Scenario: Key file is not readable by the agent
- **WHEN** the agent attempts to read the apiKeyHelper key file
- **THEN** the attempt is denied by the deny family, and the file is outside the mounted project tree

#### Scenario: Subscription mode is unchanged
- **WHEN** the bridge runs in subscription mode
- **THEN** authentication continues via the read-only staged OAuth credential copy (name-resolved staging-dir mount; a single-file bind mount goes inode-stale when the CLI's write-tmp+rename refresh replaces the file), with egress containment as its stated mitigation

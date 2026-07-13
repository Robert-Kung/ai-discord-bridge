## ADDED Requirements

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
- **THEN** authentication continues via the single-file OAuth credential mount, with egress containment as its stated mitigation

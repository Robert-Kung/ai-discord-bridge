# registry-install-guardrails

## Purpose
Bound what package installation can do inside the executor: install-time code execution disabled for npm and minimized for pip through channels the agent cannot rewrite, dependency changes surfaced at the human diff gate, and the opt-in posture with its residual risks documented in both languages.

## Requirements

### Requirement: Install-time code execution is disabled for npm and minimized for pip
Every subprocess the bridge spawns that may run a package install — both the `claude -p` path and the verify-command path — SHALL be given an environment that disables npm lifecycle scripts (`npm_config_ignore_scripts=true`) and prefers pre-built Python wheels over source distributions (`PIP_PREFER_BINARY=1`). These SHALL be injected unconditionally, independent of whether the package-index opt-in is enabled, so behavior does not vary with deployment posture. The executor image SHALL additionally carry equivalent global npm and pip configuration. The pip guardrail is explicitly a **reduction, not an elimination**: source distributions still build when no wheel exists, and a wheel can still execute code at interpreter startup via a `.pth` file.

#### Scenario: Guardrail env is present on the agent spawn path
- **WHEN** the bridge builds the subprocess environment for a `claude -p` spawn
- **THEN** the environment contains `npm_config_ignore_scripts=true` and `PIP_PREFER_BINARY=1`

#### Scenario: Guardrail env is present on the verify path
- **WHEN** the bridge builds the environment for a project verify command
- **THEN** the same two guardrail values are present, because verify commands routinely run installs and are agent-influenced

#### Scenario: Guardrails do not depend on the opt-in
- **WHEN** the environment is built with package-index egress disabled
- **THEN** both guardrail variables are still present

#### Scenario: postinstall does not run
- **WHEN** a dependency declaring a `postinstall` script is installed inside a job worktree by either spawn path
- **THEN** the script does not execute

### Requirement: Agent-writable files cannot weaken the guardrails, and the limits are stated
The guardrails SHALL be enforced through channels the agent cannot rewrite between spawns: environment constructed per spawn, plus image-baked global configuration owned by a different uid. A configuration file inside a job worktree or any other agent-writable mount — notably a project-level `.npmrc` or `pip.conf` — SHALL NOT be able to re-enable install-time execution. The guardrails SHALL NOT be described as bounding a hostile agent: an agent holding a shell can remove the variables from its own child processes, pass overriding command-line flags, point the config path at `/dev/null`, or switch to a package manager that reads neither `npm_config_*` nor `PIP_*` (uv, pnpm, yarn Berry). The enforcing boundary in those cases remains the executor's routeless egress, and the documentation SHALL name these bypasses rather than implying they are closed.

#### Scenario: Worktree config cannot re-enable scripts
- **WHEN** a job worktree contains an `.npmrc` setting `ignore-scripts=false` and an install runs
- **THEN** the environment value wins and lifecycle scripts remain disabled

#### Scenario: Global config is not agent-writable
- **WHEN** the agent attempts to modify the image-baked global npm or pip configuration
- **THEN** the write fails, because the file is owned by a uid the executor process does not run as

#### Scenario: Named bypasses are documented, not claimed closed
- **WHEN** the security documentation describes these guardrails
- **THEN** it names environment removal, command-line override, config-path redirection, and alternative package managers as working bypasses, and identifies routeless egress as the actual boundary

### Requirement: Dependency changes are surfaced at the diff gate
Because the guardrails end at the container boundary while the job's output is a commit the operator merges, changes to dependency manifests and lockfiles SHALL be called out distinctly in the diff presented for approval, rather than appearing as ordinary diff lines. A job that adds a dependency SHALL produce and commit the corresponding lockfile.

#### Scenario: Manifest change is flagged for review
- **WHEN** a job's diff modifies a dependency manifest or lockfile
- **THEN** the approval message identifies that a dependency change is present, so the operator does not have to spot it inside an unreadable lockfile diff

#### Scenario: Post-merge execution risk is documented
- **WHEN** the security documentation describes the install guardrails
- **THEN** it states that they do not apply once the commit leaves the container, so an installed-on-host or CI run executes install-time code without them

### Requirement: The opt-in posture and its residual risks are documented in both languages
The security documentation (English and Chinese) SHALL state that package-index egress is off by default, how to enable it, that only read-only index hosts are eligible, why publish-capable hosts including `registry.npmjs.org` are excluded, and the residual risks that remain once enabled. The residual list SHALL name at minimum: source-distribution builds when no wheel exists; `.pth` execution at interpreter startup; the guardrail bypasses available to a shell-holding agent; SNI-routed tunnels to other tenants of the same CDN; the low-bandwidth side channel of selective package downloads; and post-merge execution on the operator's host or CI. It SHALL record the credential-free build container as the end state that removes these, and as the precondition for enabling npm.

#### Scenario: Documented posture matches shipped defaults
- **WHEN** a reader consults the security documentation about package installation
- **THEN** it states the default-off posture, the read-only-hosts-only rule, the npm exclusion and its reason, the enforced guardrails, the named residuals, and the credential-free build container as end state

#### Scenario: Existing publish-capable-host passage stays consistent
- **WHEN** the documentation's existing statement that no publish-capable host is allow-listed is read alongside the new opt-in section
- **THEN** the two are consistent, because the opt-in admits only read-only hosts and the upload endpoints remain denied

#### Scenario: Both language versions stay in sync
- **WHEN** the English security document describes the posture, residuals, and roadmap
- **THEN** the Chinese version describes the same items, itemized rather than summarized

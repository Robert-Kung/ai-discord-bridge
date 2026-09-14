# agent-trust-layers — delta for unattended-auto-mode

## ADDED Requirements

### Requirement: Auto tier is an opt-in execution posture above bypass
The `auto` channel mode SHALL be an execution-layer tier that carries MORE authority
than `bypass` (it resolves the merge decision without a human), and SHALL therefore be
gated by BOTH the `ENABLE_AUTO_MERGE` environment flag AND author membership in
`ALLOWED_USER_IDS` — the same flag-plus-whitelist posture as bypass/approve, never the
flag alone. The conversation layer SHALL never reach the auto tier (a bot-origin
mention cannot drive execution, unchanged). Enabling auto SHALL additionally require the
verify path and the cross-account evaluator to be available; absent either, `!mode auto`
SHALL be refused (fail-closed).

#### Scenario: auto requires flag and whitelist
- WHEN a user issues `!mode auto`
- THEN it is honoured only if `ENABLE_AUTO_MERGE` is set AND the user is in `ALLOWED_USER_IDS`, and is refused otherwise

#### Scenario: auto is re-validated per message
- WHEN a message dispatches under channel mode `auto`
- THEN the tier is re-checked at dispatch (the mode is in `VALID_MODES` and the dispatch-time downgrade list), so a revoked flag/whitelist takes effect immediately

#### Scenario: conversation layer cannot reach auto
- WHEN a bot-origin mention arrives on an auto-mode channel
- THEN it is handled by the conversation layer (plan only) and never resolves a merge

### Requirement: The auto tier's merge signal is not claimed unforgeable
The auto tier replaces the human merge decision with two signals — verify (agent-authored
tests) and the cross-account evaluator (the OTHER bot, same model family, reviewing the
same diff). The security posture SHALL document that these signals are CORRELATED, not
independent, and that a diff-content prompt injection may steer the evaluator into
genuinely emitting `VERDICT: approve`. "Two green signals" SHALL be documented to mean
"not obviously bad", never "safe"; the auto tier SHALL NOT be presented as sealing the
verdict channel against injection.

#### Scenario: security posture states the residual
- WHEN SECURITY.md describes the auto tier
- THEN it names the evaluator-injection residual and the verify/evaluator correlation, rather than asserting the verdict channel is unforgeable

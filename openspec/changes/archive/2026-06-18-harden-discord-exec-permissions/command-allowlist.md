# Command allow-list (task 0.3) — the M4 approver's auto-allow policy

These are the Bash commands the bot may run in Discord **without** a per-command Discord
approval. Anything not matching auto-allow goes to Discord for ✅/❌ (default-deny on
timeout). This list is the restrictive allow-list that gate 0.1 showed `--allowedTools`
cannot enforce at the flag layer — it lives in the M4 MCP approver instead.

Starter set (operator: "pytest etc." + the suggested defaults). Expand deliberately.

## Tests
- `Bash(pytest:*)`
- `Bash(python -m pytest:*)`
- `Bash(.venv/bin/python -m pytest:*)`
- `Bash(npm test:*)`
- `Bash(npm run test:*)`

## Read-only git (inspection)
- `Bash(git status:*)`
- `Bash(git diff:*)`
- `Bash(git log:*)`
- `Bash(git show:*)`
- `Bash(git branch:*)`

## Build / typecheck / lint
- `Bash(npm run build:*)`
- `Bash(npm run lint:*)`
- `Bash(npx tsc:*)`
- `Bash(npx eslint:*)`
- `Bash(python -m py_compile:*)`

## Notes
- Pure read-only commands (`ls`, `cat`, `grep`, `whoami`, …) are auto-classified safe by
  claude and pass without even reaching the approver (see m4-approver-preflight.md); the
  `permissions.deny` family still blocks credential reads / env / network among those.
- State-changing git (`git commit`, `git push`, `git checkout`, `git reset`) is
  deliberately NOT auto-allowed → routes to Discord approval.
- `curl`/`wget`/`env`/`printenv` are denied outright by `settings.json` and never reach
  the approver as "allowable".
- Matching is prefix-based on the command string; the approver should match each segment
  of a compound command (`&&`/`|`/`;`) independently, since a single allow must not
  green-light a piggy-backed non-listed segment.

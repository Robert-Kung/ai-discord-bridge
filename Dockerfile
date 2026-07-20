FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HOME=/home/user

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    # git: the exec loop is worktree-based — the frontend runs
    # worktree add/commit/diff/merge, and exec-tier jobs run inside a
    # `git worktree` checkout. Without it, startup GC and every exec job fail.
    && apt-get install -y --no-install-recommends nodejs git \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y --auto-remove curl gnupg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Keep this version in sync with requirements-dev.txt (tests import the same dep).
RUN pip install --no-cache-dir discord.py==2.4.0

# Install-time code-execution guardrails, belt to the per-spawn env injection in
# bridge/config.py:INSTALL_GUARDRAIL_ENV. Written root-owned 0644: the app runs as
# uid 1000, so the agent can read but never rewrite these. They are NOT a boundary
# against a hostile agent (PIP_CONFIG_FILE=/dev/null, CLI flags, and uv/pnpm/yarn
# all sidestep them) — see SECURITY.md. The cache dirs are uid-1000-writable because
# HOME is root-owned (docker creates it for the bind mounts), so pip/npm defaults
# under ~/.cache would EACCES.
RUN set -eux; \
    GLOBAL_NPMRC="$(npm config get globalconfig)"; \
    mkdir -p "$(dirname "$GLOBAL_NPMRC")"; \
    printf 'ignore-scripts=true\n' >"$GLOBAL_NPMRC"; \
    printf '[global]\nprefer-binary = true\n' >/etc/pip.conf; \
    chmod 0644 "$GLOBAL_NPMRC" /etc/pip.conf; \
    mkdir -p /home/user/.cache/pip /home/user/.cache/npm; \
    chown -R 1000:1000 /home/user/.cache

WORKDIR /app
COPY bot.py executor.py approver_policy.py mcp_approver.py approver-allowlist.json settings.json /app/
COPY bridge/ /app/bridge/

# Default entrypoint = single-container frontend+executor. The phase-2 split
# overrides `command:` to /app/executor.py for the executor service.
CMD ["python3", "/app/bot.py"]

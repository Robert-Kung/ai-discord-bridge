"""M0 — bot-identity isolation (spec: bot-identity-isolation).

Asserts the published deployment template (docker-compose.example.yml) and the
in-repo minimal bot CLAUDE.md honour the isolation cuts:
  • bots run under dedicated minimal config dirs, not the operator account dirs
  • the operator's whole ~/.claude(-b) account dirs are NOT mounted (Issue 1)
  • credentials arrive via the :ro staging-dir mount (bot-dir symlink resolves in-container)
  • only the thin project_plan.md index is mounted from memory/, never the dir
We parse the example template (committed; the real docker-compose.yml is gitignored)
with a tiny text parser so CI needs no PyYAML.
"""
from pathlib import Path

from bridge import config

REPO = Path(config.__file__).resolve().parent.parent
COMPOSE_EXAMPLE = REPO / "docker-compose.example.yml"
BOT_CLAUDE_MD = REPO / "bot-config" / "CLAUDE.md"


def _volumes(compose_path: Path) -> list[tuple[str, str, str]]:
    """Extract (src, dst, mode) from the `volumes:` block. Volume lines look like
    `      - <src>:<dst>[:<mode>]`; we ignore non-volume list items."""
    out: list[tuple[str, str, str]] = []
    in_vols = False
    for raw in compose_path.read_text().splitlines():
        stripped = raw.strip()
        if stripped.endswith("volumes:"):
            in_vols = True
            continue
        if in_vols:
            # leave the block when indentation returns to a sibling key
            if stripped and not stripped.startswith("- ") and not stripped.startswith("#") \
                    and raw[:1] != " ":
                break
            if not stripped.startswith("- "):
                continue
            spec = stripped[2:].strip()
            parts = spec.split(":")
            if len(parts) == 2:
                out.append((parts[0], parts[1], ""))
            elif len(parts) >= 3:
                out.append((parts[0], parts[1], parts[2]))
    return out


# ── 1.7 — dedicated minimal config dir, no operator PII / shared import ──────
def test_bot_config_dirs_are_not_operator_account_dirs():
    for n, d in config.BOT_CONFIG_DIRS.items():
        assert d not in ("/home/user/.claude", "/home/user/.claude-b"), \
            f"bot {n} must not run under the operator primary/secondary account dir"
        assert "claude-bot" in d


def test_bot_claude_md_has_no_shared_import_or_pii():
    text = BOT_CLAUDE_MD.read_text()
    # no @import of any shared CLAUDE.md (that is how operator infra/topology leaks in)
    assert "@/home/user/.claude-shared/CLAUDE.md" not in text
    assert "@import" not in text.lower()
    # no operator personal data
    assert "@" not in text or "mention" in text.lower()  # only the '@'-mention rule may use '@'
    for pii in ("bot-b@example.invalid", "bot-a@example.invalid", "cswap slot"):
        assert pii not in text


# ── 1.8 — only the thin index is reachable, not the memory/ trove ───────────
def test_only_project_plan_index_mounted_from_memory():
    vols = _volumes(COMPOSE_EXAMPLE)
    mem_sources = [s for (s, d, m) in vols if "/.claude-shared/memory" in s]
    # only the single project_plan.md file (once per app container in the split
    # deploy), never the memory/ directory
    assert mem_sources and set(mem_sources) == {
        "/home/user/.claude-shared/memory/project_plan.md"}, mem_sources
    # the directory itself is not mounted
    assert not any(s.rstrip("/") == "/home/user/.claude-shared/memory" for s, _, _ in vols)


def test_memory_siblings_and_shared_claude_md_not_mounted():
    vols = _volumes(COMPOSE_EXAMPLE)
    srcs = [s for s, _, _ in vols]
    for sibling in ("infrastructure.md", "user_profile.md", "agent_a.md", "agent_b.md"):
        assert not any(sibling in s for s in srcs), f"{sibling} must not be mounted"
    # the shared operator CLAUDE.md must not be mounted either
    assert not any(s == "/home/user/.claude-shared/CLAUDE.md" for s in srcs)


# ── 1.9 (Issue 1) — operator account dirs unmounted; creds single-file ──────
def test_wholesale_account_dirs_not_mounted():
    vols = _volumes(COMPOSE_EXAMPLE)
    for s, d, _ in vols:
        # neither the operator's ~/.claude nor ~/.claude-b appears as a directory mount
        assert s not in ("/home/user/.claude", "/home/user/.claude-b"), f"wholesale mount: {s}"
        assert d not in ("/home/user/.claude", "/home/user/.claude-b"), f"wholesale target: {d}"


def test_bot_dirs_and_staged_credentials_mounted():
    vols = _volumes(COMPOSE_EXAMPLE)
    targets = [d for _, d, _ in vols]
    # the dedicated bot dirs are mounted
    assert "/home/user/.claude-bot-a" in targets
    assert "/home/user/.claude-bot-b" in targets
    # credentials arrive via the READ-ONLY staging DIRECTORY (never a single-file
    # mount: the CLI's write-tmp+rename refresh mints a new inode and a file mount
    # pins the old one — the container 401s on a 4-day-stale token, live find
    # 2026-07-13; the bot-dir .credentials.json is a host-side symlink into it)
    match = [(s, d, m) for s, d, m in vols if d == "/home/user/.claude-bot-creds"]
    assert match, "missing the credential staging-dir mount"
    src, _, mode = match[0]
    assert src == "/home/user/.claude-bot-creds"
    assert mode == "ro", "staging dir must be read-only in-container"
    # and no mount may source from inside the operator account dirs
    for s, d, _ in vols:
        for acct in ("/home/user/.claude/", "/home/user/.claude-b/"):
            assert not s.startswith(acct), f"mount sources from the account dir: {s}"
            assert not d.startswith(acct), f"mount targets the account dir path: {d}"

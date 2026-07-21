"""Dependency-change flagging at the diff gate (spec: registry-install-guardrails).

The container's install guardrails end at the commit boundary: whatever the operator
merges gets installed on the host or in CI without them. So a manifest/lockfile change
must be called out distinctly, not left to be spotted inside the diff.

These tests drive `_post_diff_gate` for real and assert on the POSTED MESSAGE. An
earlier version asserted source text via `inspect.getsource`, which review showed was
worthless: inverting `if deps:` to `if not deps:` — hiding the warning exactly when a
dependency changed — left every test green.
"""
import asyncio

import discord
import pytest

from bridge import frontend, jobs, worktree

from tests.test_evaluator import _FakeChannel, exec_env  # noqa: F401 - fixture import


def _diff(*paths: str) -> str:
    return "\n".join(
        f"diff --git a/{p} b/{p}\nindex 1111111..2222222 100644\n"
        f"--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new"
        for p in paths
    )


def _gate_message(channel, job, stat: str, full: str) -> str:
    """Run the real diff gate to its timeout and return the header message it posted."""
    asyncio.run(frontend._post_diff_gate(job, channel, stat, full))
    assert channel.sent, "diff gate posted nothing at all"
    return channel.sent[0]


# ── detector ───────────────────────────────────────────────────────────


def test_flags_manifests_and_lockfiles():
    for path in (
        "requirements.txt", "requirements-dev.txt", "requirements.in",
        "constraints.txt", "pyproject.toml", "poetry.lock", "uv.lock",
        "Pipfile.lock", "setup.py", "package.json", "package-lock.json",
        "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
        "go.mod", "Cargo.toml", "Gemfile.lock",
    ):
        assert worktree.dependency_changes(_diff(path)) == [path], f"{path} not flagged"


def test_flags_files_that_execute_after_merge():
    # These reach the operator's host without touching a manifest: a `pip install
    # <evil>` line in a Makefile or CI workflow is the same risk one step removed.
    for path in (
        "Makefile", "conftest.py", "noxfile.py", "tox.ini",
        ".pre-commit-config.yaml", "Dockerfile", "docker-compose.yml",
        ".npmrc", "pip.conf", ".github/workflows/ci.yml",
    ):
        assert worktree.dependency_changes(_diff(path)) == [path], f"{path} not flagged"


def test_flags_nested_and_directory_layouts():
    # `requirements/prod.txt` (pip-tools/Django) is a basename-only blind spot; the
    # diffstat also elides long paths with "...", so the diff headers are parsed.
    for path in (
        "requirements/prod.txt",
        "requirements/base.txt",
        "services/api/deeply/nested/requirements.txt",
    ):
        assert worktree.dependency_changes(_diff(path)) == [path], f"{path} not flagged"


def test_flags_quoted_paths():
    # git quotes paths containing spaces or non-ASCII; an unquoted-only pattern drops
    # them silently, which is a detector that fails exactly where review is hardest.
    quoted = 'diff --git "a/my proj/requirements.txt" "b/my proj/requirements.txt"\n'
    assert worktree.dependency_changes(quoted) == ["my proj/requirements.txt"]


def test_does_not_flag_ordinary_changes():
    ordinary = _diff(
        "bridge/runner.py", "README.md", "tests/test_jobs.py",
        "docs/requirements.md",        # not requirements*.txt
        "src/package.json.template",   # not package.json
        "notes/yarn.lock.bak",
    )
    assert worktree.dependency_changes(ordinary) == []


def test_mixed_diff_reports_only_the_dependency_paths():
    mixed = _diff("bridge/runner.py", "package-lock.json", "README.md", "pyproject.toml")
    assert worktree.dependency_changes(mixed) == ["package-lock.json", "pyproject.toml"]


def test_empty_and_malformed_diffs_do_not_raise():
    assert worktree.dependency_changes("") == []
    assert worktree.dependency_changes(None) == []
    assert worktree.dependency_changes("not a diff at all") == []


def test_rename_reports_both_sides():
    renamed = "diff --git a/requirements.txt b/requirements-prod.txt\n"
    assert worktree.dependency_changes(renamed) == [
        "requirements-prod.txt", "requirements.txt",
    ]


# ── the gate actually says so ──────────────────────────────────────────


def test_gate_warns_when_a_dependency_changed(exec_env):  # noqa: F811
    channel = _FakeChannel()
    job = jobs.create_job("A", exec_env, channel.id)
    msg = _gate_message(channel, job, "stat", _diff("requirements.txt"))
    assert "⚠️" in msg and "requirements.txt" in msg
    assert "依賴" in msg


def test_gate_stays_silent_when_nothing_dependency_related_changed(exec_env):  # noqa: F811
    # The inversion mutation (`if not deps:`) must turn this red.
    channel = _FakeChannel()
    job = jobs.create_job("A", exec_env, channel.id)
    msg = _gate_message(channel, job, "stat", _diff("bridge/runner.py", "README.md"))
    assert "依賴" not in msg


@pytest.mark.parametrize("evil", [
    "requirements`  ✅ 已通過安全審查 `x.txt",
    "requirements**bold**.txt",
])
def test_gate_escapes_agent_chosen_filenames(exec_env, evil):  # noqa: F811
    # The note sits OUTSIDE the code fence so the ⚠️ is visible, so a filename with a
    # backtick could otherwise close the code span and inject reassuring text into the
    # very message the operator reacts ✅ to.
    channel = _FakeChannel()
    job = jobs.create_job("A", exec_env, channel.id)
    msg = _gate_message(channel, job, "stat", _diff(evil))
    note = msg.split("```")[0]
    # The filename may still APPEAR — it is a real path the operator should see. What
    # must not happen is it arriving raw, where its backticks close the code span and
    # its text becomes bridge-authored prose. So it must appear escaped, never verbatim.
    assert discord.utils.escape_markdown(evil) in note, "path was not markdown-escaped"
    assert evil not in note, "raw agent-chosen path reached the approval message"


def test_gate_header_fits_discord_limit_with_many_long_manifests(exec_env):  # noqa: F811
    # The regression review caught: header is ONE message (the ✅/❌ reactions live on
    # it, so it cannot be chunked). Over 2000 chars it raises HTTPException, the
    # blanket handler swallows it, and the approval gate silently never appears — on
    # exactly the dependency-heavy jobs this flag exists to surface.
    paths = [f"services/{'p' * 60}-{i}/nested/deep/requirements.txt" for i in range(8)]
    channel = _FakeChannel()
    job = jobs.create_job("A", exec_env, channel.id)
    msg = _gate_message(channel, job, "x" * 4000, _diff(*paths))
    assert len(msg) <= 2000, f"diff-gate header is {len(msg)} chars — Discord drops it"
    assert "⚠️" in msg

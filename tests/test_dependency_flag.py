"""Dependency-change flagging at the diff gate (spec: registry-install-guardrails).

The container's install guardrails end at the commit boundary: whatever the operator
merges gets installed on the host or in CI without them. So a manifest/lockfile change
must be called out distinctly, not left to be spotted inside the diff.
"""
import inspect

from bridge import frontend, worktree


def _diff(*paths: str) -> str:
    return "\n".join(
        f"diff --git a/{p} b/{p}\nindex 1111111..2222222 100644\n"
        f"--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new"
        for p in paths
    )


def test_flags_manifests_and_lockfiles():
    for path in (
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "setup.py",
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    ):
        assert worktree.dependency_changes(_diff(path)) == [path], f"{path} not flagged"


def test_flags_nested_manifests():
    # The diffstat elides long paths with "..."; parsing the diff headers must not.
    nested = "services/api/deeply/nested/requirements.txt"
    assert worktree.dependency_changes(_diff(nested)) == [nested]


def test_does_not_flag_ordinary_changes():
    ordinary = _diff(
        "bridge/runner.py",
        "README.md",
        "tests/test_jobs.py",
        "docs/requirements.md",       # not requirements*.txt
        "src/package.json.template",  # not package.json
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
    # a/ and b/ differ on a rename — a manifest moving in or out still matters
    renamed = "diff --git a/requirements.txt b/requirements-prod.txt\n"
    assert worktree.dependency_changes(renamed) == [
        "requirements-prod.txt",
        "requirements.txt",
    ]


def test_diff_gate_surfaces_the_flag_in_the_approval_message():
    # The detector is only useful if the gate posts it above the diff.
    src = inspect.getsource(frontend._post_diff_gate)
    assert "worktree.dependency_changes(full)" in src
    assert "dep_note" in src and "{dep_note}" in src

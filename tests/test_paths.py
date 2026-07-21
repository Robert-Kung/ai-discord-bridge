"""L1 — resolve_project_cwd: the !cd whitelist + git guard + traversal/symlink
escape protection (the boundary that keeps `bypass` inside operator-chosen dirs)."""
from pathlib import Path

import pytest

from bridge import config, trust


@pytest.fixture
def projects(tmp_path, monkeypatch):
    proj = tmp_path / "myproj"
    (proj / ".git").mkdir(parents=True)        # whitelisted, valid git project
    nogit = tmp_path / "nogit"
    nogit.mkdir()                              # whitelisted, but no .git
    monkeypatch.setattr(config, "PROJECT_DIRS", [proj.resolve(), nogit.resolve()])
    return proj, nogit, tmp_path


def test_valid_project_accepted(projects):
    proj, _, _ = projects
    resolved, msg = trust.resolve_project_cwd(str(proj))
    assert resolved == str(proj.resolve())
    assert msg == "ok"


def test_outside_whitelist_rejected(projects):
    _, _, tmp_path = projects
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    resolved, msg = trust.resolve_project_cwd(str(outside))
    assert resolved is None


def test_whitelisted_without_git_rejected(projects):
    _, nogit, _ = projects
    resolved, msg = trust.resolve_project_cwd(str(nogit))
    assert resolved is None
    assert ".git" in msg


def test_dotdot_traversal_escape_rejected(projects):
    proj, _, tmp_path = projects
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    resolved, _ = trust.resolve_project_cwd(str(proj / ".." / "outside"))
    assert resolved is None


def test_symlink_escape_rejected(projects):
    proj, _, tmp_path = projects
    secret = tmp_path / "secret"
    (secret / ".git").mkdir(parents=True)      # a VALID git repo outside the whitelist
    link = proj / "link"
    link.symlink_to(secret)                    # symlink inside whitelist → outside
    resolved, _ = trust.resolve_project_cwd(str(link))
    assert resolved is None                    # resolves to `secret`, not whitelisted


def test_prefix_sibling_not_treated_as_inside(tmp_path, monkeypatch):
    # classic startswith-style bug: /home/user/proj must NOT whitelist /home/user/proj-evil
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    evil = tmp_path / "proj-evil"
    (evil / ".git").mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_DIRS", [proj.resolve()])
    resolved, _ = trust.resolve_project_cwd(str(evil))
    assert resolved is None


def test_empty_input_rejected(projects):
    resolved, _ = trust.resolve_project_cwd("   ")
    assert resolved is None


# ── bare-name expansion (PROJECT_BASE_DIR) ────────────────────────────────────
# The relative-path branch was previously unexercised: every test above passes an
# absolute path, so the expansion base could have been anything and stayed green.

def test_bare_name_expands_under_project_base_dir(projects, monkeypatch):
    proj, _, tmp_path = projects
    monkeypatch.setattr(config, "PROJECT_BASE_DIR", tmp_path)
    resolved, msg = trust.resolve_project_cwd("myproj")
    assert resolved == str(proj.resolve())
    assert msg == "ok"


def test_bare_name_uses_base_dir_not_cwd(projects, monkeypatch, tmp_path_factory):
    # Mutation guard: if the expansion base were process cwd (or a hardcoded path)
    # rather than PROJECT_BASE_DIR, `myproj` would not resolve and this goes red.
    proj, _, tmp_path = projects
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    decoy = elsewhere / "myproj"
    (decoy / ".git").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(config, "PROJECT_BASE_DIR", tmp_path)
    resolved, _ = trust.resolve_project_cwd("myproj")
    assert resolved == str(proj.resolve())      # the base dir one, not the cwd decoy


def test_bare_name_still_subject_to_whitelist(projects, monkeypatch):
    # A wrong PROJECT_BASE_DIR must not widen anything: expansion happens BEFORE the
    # PROJECT_DIRS check, so an unlisted sibling under the base is still refused.
    _, _, tmp_path = projects
    sibling = tmp_path / "unlisted"
    (sibling / ".git").mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_BASE_DIR", tmp_path)
    resolved, _ = trust.resolve_project_cwd("unlisted")
    assert resolved is None


def test_bare_name_traversal_escape_rejected(projects, monkeypatch):
    _, _, tmp_path = projects
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_BASE_DIR", tmp_path / "myproj")
    resolved, _ = trust.resolve_project_cwd("../outside")
    assert resolved is None


def test_absolute_input_ignores_base_dir(projects, monkeypatch):
    proj, _, tmp_path = projects
    monkeypatch.setattr(config, "PROJECT_BASE_DIR", Path("/nonexistent-base"))
    resolved, msg = trust.resolve_project_cwd(str(proj))
    assert resolved == str(proj.resolve())      # absolute path unaffected by the base
    assert msg == "ok"


def test_project_base_dir_is_env_driven(set_env, tmp_state, tmp_path):
    # It lives in _CONFIG_GLOBALS, so load_config must own it like every other
    # env-derived global — an import-time-only read would ignore this env change.
    assert "PROJECT_BASE_DIR" in config._CONFIG_GLOBALS
    set_env(ALLOWED_USER_IDS="111", PROJECT_BASE_DIR=str(tmp_path))
    config.load_config()
    assert config.PROJECT_BASE_DIR == tmp_path


def test_project_base_dir_defaults_when_env_absent(set_env, tmp_state):
    set_env(ALLOWED_USER_IDS="111", PROJECT_BASE_DIR=None)
    config.load_config()
    assert config.PROJECT_BASE_DIR == Path("/home/user/projects")

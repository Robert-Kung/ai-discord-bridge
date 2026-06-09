"""L1 — resolve_project_cwd: the !cd whitelist + git guard + traversal/symlink
escape protection (the boundary that keeps `bypass` inside operator-chosen dirs)."""
import pytest

import bot


@pytest.fixture
def projects(tmp_path, monkeypatch):
    proj = tmp_path / "myproj"
    (proj / ".git").mkdir(parents=True)        # whitelisted, valid git project
    nogit = tmp_path / "nogit"
    nogit.mkdir()                              # whitelisted, but no .git
    monkeypatch.setattr(bot, "PROJECT_DIRS", [proj.resolve(), nogit.resolve()])
    return proj, nogit, tmp_path


def test_valid_project_accepted(projects):
    proj, _, _ = projects
    resolved, msg = bot.resolve_project_cwd(str(proj))
    assert resolved == str(proj.resolve())
    assert msg == "ok"


def test_outside_whitelist_rejected(projects):
    _, _, tmp_path = projects
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    resolved, msg = bot.resolve_project_cwd(str(outside))
    assert resolved is None


def test_whitelisted_without_git_rejected(projects):
    _, nogit, _ = projects
    resolved, msg = bot.resolve_project_cwd(str(nogit))
    assert resolved is None
    assert ".git" in msg


def test_dotdot_traversal_escape_rejected(projects):
    proj, _, tmp_path = projects
    # The escape target is ITSELF a valid git repo, so the .git guard can't be the
    # thing that rejects it — only resolve()+whitelist can. (Without this, the test
    # would still pass via the .git guard even if .resolve() were removed.)
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    resolved, _ = bot.resolve_project_cwd(str(proj / ".." / "outside"))
    assert resolved is None


def test_symlink_escape_rejected(projects):
    proj, _, tmp_path = projects
    secret = tmp_path / "secret"
    (secret / ".git").mkdir(parents=True)      # a VALID git repo outside the whitelist
    link = proj / "link"
    link.symlink_to(secret)                    # symlink inside whitelist → outside
    resolved, _ = bot.resolve_project_cwd(str(link))
    assert resolved is None                    # resolves to `secret`, not whitelisted


def test_prefix_sibling_not_treated_as_inside(tmp_path, monkeypatch):
    # classic startswith-style bug: /home/user/proj must NOT whitelist
    # /home/user/proj-evil. is_relative_to handles it; lock it down.
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    evil = tmp_path / "proj-evil"
    (evil / ".git").mkdir(parents=True)
    monkeypatch.setattr(bot, "PROJECT_DIRS", [proj.resolve()])
    resolved, _ = bot.resolve_project_cwd(str(evil))
    assert resolved is None


def test_empty_input_rejected(projects):
    resolved, _ = bot.resolve_project_cwd("   ")
    assert resolved is None

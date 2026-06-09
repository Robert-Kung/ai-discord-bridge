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
    proj, _, _ = projects
    # ../outside resolves out of the whitelisted dir → must be rejected
    resolved, _ = bot.resolve_project_cwd(str(proj / ".." / "outside"))
    assert resolved is None


def test_symlink_escape_rejected(projects):
    proj, _, tmp_path = projects
    secret = tmp_path / "secret"
    secret.mkdir()
    link = proj / "link"
    link.symlink_to(secret)                    # symlink inside whitelist → outside
    resolved, _ = bot.resolve_project_cwd(str(link))
    assert resolved is None                    # resolves to `secret`, not whitelisted


def test_empty_input_rejected(projects):
    resolved, _ = bot.resolve_project_cwd("   ")
    assert resolved is None

"""Install-time code-execution guardrails (spec: registry-install-guardrails).

Both spawn paths — the `claude -p` path and the verify path — must carry the
guardrail env unconditionally, independent of the package-index opt-in. The verify
path is the main one, not a hole-plug: verify commands routinely run installs.

Mutation-verified 2026-07-20: removing either `env.update(install_guardrail_env(...))`
call in bridge/runner.py turns that path's tests red. The image-level guarantees are
asserted against a REAL BUILD rather than Dockerfile text — review showed a text
assertion let `chmod 0644` → `0666` (which defeats the whole "not agent-writable"
claim) pass green.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from bridge import config, runner

HOST = {"PATH": "/usr/bin", "HOME": "/home/user", "DISCORD_BOT_A_TOKEN": "tok"}
CFG = {"config_dir": "/home/user/.claude", "api_key": None}

REPO = Path(config.__file__).resolve().parent.parent

EXPECTED = {
    # npm parses booleans: the literal "true" is required, "1" would NOT disable scripts.
    "npm_config_ignore_scripts": "true",
    "PIP_PREFER_BINARY": "1",
    # installs land in a per-job venv: a user-site .pth would execute at every
    # interpreter start, including this long-lived executor holding the credential
    "PIP_REQUIRE_VIRTUALENV": "1",
    # the pip cache is shared by every job in one container -> cross-job poisoning
    "PIP_NO_CACHE_DIR": "1",
}

docker_required = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode != 0,
    reason="docker unavailable",
)


def _env(kind: str, base=None):
    base = HOST if base is None else base
    if kind == "subprocess":
        return runner.build_subprocess_env(CFG, base_env=base)
    return runner.build_verify_env(base_env=base)


@pytest.mark.parametrize("kind", ["subprocess", "verify"])
def test_guardrails_present_on_both_spawn_paths(kind):
    env = _env(kind)
    for key, value in EXPECTED.items():
        assert env[key] == value, f"{kind} path missing guardrail {key}={value}"


@pytest.mark.parametrize("kind", ["subprocess", "verify"])
def test_guardrails_do_not_depend_on_the_opt_in(monkeypatch, kind):
    # The opt-in is a proxy build arg with no runtime representation; assert the
    # guardrails hold with egress plumbing entirely absent, i.e. the most
    # "index-disabled" configuration the process can observe.
    monkeypatch.setattr(config, "EGRESS_PROXY_URL", "")
    assert all(_env(kind)[k] == v for k, v in EXPECTED.items())


@pytest.mark.parametrize("kind", ["subprocess", "verify"])
def test_guardrail_values_survive_a_hostile_inherited_env(kind):
    hostile = dict(HOST, npm_config_ignore_scripts="false", PIP_PREFER_BINARY="0",
                   PIP_REQUIRE_VIRTUALENV="0", PIP_NO_CACHE_DIR="0")
    assert all(_env(kind, hostile)[k] == v for k, v in EXPECTED.items())


def test_guardrail_vars_are_not_stripped_by_the_deny_filter():
    assert not (set(config.install_guardrail_env(HOST)) & config._SUBPROCESS_ENV_DENY)


@pytest.mark.parametrize("kind", ["subprocess", "verify"])
def test_npm_cache_follows_HOME_rather_than_a_hardcoded_path(kind):
    # npm exits non-zero on an unwritable cache dir (pip merely degrades), so a path
    # hardcoded to the container's HOME would break `npm ci` for every bare run under
    # a different HOME — and vendored Node is the only Node path this change endorses.
    env = _env(kind, dict(HOST, HOME="/somewhere/else"))
    assert env["npm_config_cache"] == "/somewhere/else/.cache/npm"


def test_no_npm_cache_override_when_HOME_is_absent():
    # Better to leave npm's own default than to point it at "/.cache/npm".
    assert "npm_config_cache" not in config.install_guardrail_env({"PATH": "/usr/bin"})


# ── image-level guarantees, asserted against a real build ──────────────


@docker_required
def test_image_config_is_effective_and_not_writable_by_the_app_uid():
    """Build the executor image and check the belt from inside it, as uid 1000.

    Covers what a Dockerfile-text assertion cannot: that npm/pip actually READ the
    baked config, and that the app uid genuinely cannot rewrite it.
    """
    subprocess.run(["docker", "build", "-q", "-t", "guardrail-image-test", str(REPO)],
                   check=True, capture_output=True, timeout=900)
    script = (
        'printf "npm=%s\\n" "$(npm config get ignore-scripts)"; '
        'printf "pip=%s\\n" "$(pip config list)"; '
        'if echo x >>"$(npm config get globalconfig)" 2>/dev/null; '
        'then echo "npmrc=WRITABLE"; else echo "npmrc=readonly"; fi; '
        'if echo x >>/etc/pip.conf 2>/dev/null; '
        'then echo "pipconf=WRITABLE"; else echo "pipconf=readonly"; fi; '
        'if touch /home/user/probe 2>/dev/null; '
        'then echo "home=WRITABLE"; else echo "home=readonly"; fi'
    )
    out = subprocess.run(
        ["docker", "run", "--rm", "--user", "1000:1000", "-e", "HOME=/home/user",
         "guardrail-image-test", "sh", "-c", script],
        check=True, capture_output=True, timeout=180,
    ).stdout.decode()
    assert "npm=true" in out, "global npmrc does not disable lifecycle scripts"
    assert "prefer-binary" in out, "global pip.conf not in effect"
    assert "npmrc=readonly" in out, "app uid can rewrite the global npmrc — belt is void"
    assert "pipconf=readonly" in out, "app uid can rewrite /etc/pip.conf — belt is void"
    # A writable HOME would restore a user site, where a .pth executes at every
    # interpreter start — including this long-lived credential-holding process.
    assert "home=readonly" in out, "HOME became writable: user-site .pth persistence"


@docker_required
def test_bare_pip_install_fails_loudly_instead_of_EACCES():
    """The venv requirement must produce an actionable error, not a permissions crash.

    Live smoke found a bare `pip install` dying on EACCES creating ~/.local — the
    headline use case failing opaquely. PIP_REQUIRE_VIRTUALENV turns that into a
    message that names the fix, and closes user-site persistence at the same time.
    """
    env_args = []
    for key, value in config.install_guardrail_env({"HOME": "/home/user"}).items():
        env_args += ["-e", f"{key}={value}"]
    out = subprocess.run(
        ["docker", "run", "--rm", "--user", "1000:1000", "-e", "HOME=/home/user",
         *env_args, "guardrail-image-test", "sh", "-c",
         "pip install tabulate 2>&1 | head -3"],
        check=True, capture_output=True, timeout=180,
    ).stdout.decode()
    assert "virtualenv" in out.lower(), f"expected a venv-required message, got: {out}"
    assert "Permission denied" not in out, "still failing on EACCES, not guided"

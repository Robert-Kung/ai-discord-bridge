"""Install-time code-execution guardrails (spec: registry-install-guardrails).

Both spawn paths — the `claude -p` path and the verify path — must carry the
guardrail env unconditionally, independent of the package-index opt-in. The verify
path is the main one, not a hole-plug: verify commands routinely run installs.

Mutation-verify (2026-07-20): removing either `env.update(INSTALL_GUARDRAIL_ENV)`
call in bridge/runner.py turns the corresponding path test red.
"""
import re
from pathlib import Path

import pytest

from bridge import config, runner

HOST = {"PATH": "/usr/bin", "HOME": "/home/user", "DISCORD_BOT_A_TOKEN": "tok"}
CFG = {"config_dir": "/home/user/.claude", "api_key": None}

REPO = Path(config.__file__).resolve().parent.parent

# npm parses booleans: the literal "true" is required, "1" would NOT disable scripts.
EXPECTED = {"npm_config_ignore_scripts": "true", "PIP_PREFER_BINARY": "1"}


@pytest.mark.parametrize("build", ["subprocess", "verify"])
def test_guardrails_present_on_both_spawn_paths(build):
    env = (
        runner.build_subprocess_env(CFG, base_env=HOST)
        if build == "subprocess"
        else runner.build_verify_env(base_env=HOST)
    )
    for key, value in EXPECTED.items():
        assert env[key] == value, f"{build} path missing guardrail {key}={value}"


@pytest.mark.parametrize("build", ["subprocess", "verify"])
def test_guardrails_do_not_depend_on_the_opt_in(monkeypatch, build):
    # The opt-in is a proxy build arg with no runtime representation; assert the
    # guardrails hold with egress plumbing entirely absent, i.e. the most
    # "index-disabled" configuration the process can observe.
    monkeypatch.setattr(config, "EGRESS_PROXY_URL", "")
    env = (
        runner.build_subprocess_env(CFG, base_env=HOST)
        if build == "subprocess"
        else runner.build_verify_env(base_env=HOST)
    )
    assert all(env[k] == v for k, v in EXPECTED.items())


@pytest.mark.parametrize("build", ["subprocess", "verify"])
def test_guardrail_values_survive_a_hostile_inherited_env(build):
    # The agent influences the parent env in some deployments; injection must win
    # over inheritance, not merge with it.
    hostile = dict(HOST, npm_config_ignore_scripts="false", PIP_PREFER_BINARY="0")
    env = (
        runner.build_subprocess_env(CFG, base_env=hostile)
        if build == "subprocess"
        else runner.build_verify_env(base_env=hostile)
    )
    assert all(env[k] == v for k, v in EXPECTED.items())


def test_guardrail_vars_are_not_stripped_by_the_deny_filter():
    assert not (set(config.INSTALL_GUARDRAIL_ENV) & config._SUBPROCESS_ENV_DENY)


def test_cache_dirs_are_redirected_off_the_root_owned_home():
    # The executor runs as uid 1000 with a root-owned HOME, so the pip/npm defaults
    # under ~/.cache EACCES. Verified against the built image 2026-07-20.
    assert config.INSTALL_GUARDRAIL_ENV["PIP_CACHE_DIR"].startswith("/home/user/.cache")
    assert config.INSTALL_GUARDRAIL_ENV["npm_config_cache"].startswith("/home/user/.cache")


def test_image_bakes_global_config_owned_by_another_uid():
    # Belt to the env injection. Written as root while the app runs as 1000:1000,
    # so the agent can read but not rewrite it.
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "ignore-scripts=true" in dockerfile
    assert "prefer-binary = true" in dockerfile
    assert re.search(r"chown -R 1000:1000 /home/user/\.cache", dockerfile)
    assert "USER" not in re.sub(r"#.*", "", dockerfile), \
        "image must stay root-built so the baked config is not agent-writable"

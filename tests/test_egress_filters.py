"""Proxy allow-list composition (spec: egress-containment).

The opt-in package-index filter must be additive-only and default-off, and no
filter shipped in the repo may ever name a publish-capable host. The build-time
assertions are exercised against a real `docker build`; those tests skip when
docker is unavailable rather than silently passing.
"""
import re
import shutil
import subprocess

import pytest

from bridge import config
from pathlib import Path

REPO = Path(config.__file__).resolve().parent.parent
PROXY = REPO / "egress-proxy"

# Same host serves publishes -> reachable means writable. Never allow-list these.
PUBLISH_CAPABLE = ("registry.npmjs.org", "upload.pypi.org")

docker_required = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(
        ["docker", "info"], capture_output=True, timeout=60
    ).returncode
    != 0,
    reason="docker unavailable",
)


def _regex_lines(path: Path) -> list[str]:
    """Filter lines tinyproxy actually matches on — comments and blanks are inert
    (verified against tinyproxy 1.11.2: a blank line does NOT match every host)."""
    return [
        ln.strip()
        for ln in path.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _regex_lines_text(text: str) -> list[str]:
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _build_filter(filter_name: str, extra: str = "") -> str:
    """Build the proxy image and return the composed /etc/tinyproxy/filter."""
    tag = f"filtertest-{filter_name}-{extra or 'none'}".replace(".", "-")
    cmd = ["docker", "build", "-q", "-t", tag, "--build-arg", f"FILTER={filter_name}"]
    if extra:
        cmd += ["--build-arg", f"EXTRA_FILTER={extra}"]
    subprocess.run(cmd + [str(PROXY)], check=True, capture_output=True, timeout=600)
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "cat", tag, "/etc/tinyproxy/filter"],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return out.stdout.decode()


def test_no_shipped_filter_names_a_publish_capable_host():
    # Guards against a later "just add npm too" edit: the exclusion is the whole
    # basis for README/SECURITY's no-publish-capable-host claim.
    # Match on active regex lines only: filter.pypi names these hosts in a comment
    # precisely to record *why* they are excluded, and a comment matches nothing.
    for path in PROXY.glob("filter*"):
        for line in _regex_lines(path):
            for host in PUBLISH_CAPABLE:
                assert not re.fullmatch(line, host, re.IGNORECASE), \
                    f"{path.name} allow-lists publish-capable host {host} via {line!r}"


def test_base_executor_filter_has_no_index_hosts():
    lines = _regex_lines(PROXY / "filter.anthropic")
    assert not any("pypi" in ln or "pythonhosted" in ln for ln in lines)


def test_pypi_filter_is_exactly_the_two_read_only_hosts():
    assert _regex_lines(PROXY / "filter.pypi") == [
        r"^pypi\.org$",
        r"^files\.pythonhosted\.org$",
    ]


@docker_required
def test_default_build_is_byte_identical_to_base_filter():
    # Default-off must mean *no* change to the shipped allow-list, not merely
    # "no pypi lines".
    composed = _build_filter("filter.anthropic")
    assert composed == (PROXY / "filter.anthropic").read_text()


@docker_required
def test_opt_in_appends_without_dropping_any_existing_host():
    composed = _regex_lines_text(_build_filter("filter.anthropic", "filter.pypi"))
    base = _regex_lines(PROXY / "filter.anthropic")
    assert set(base) <= set(composed), "opt-in dropped a previously allow-listed host"
    assert r"^pypi\.org$" in composed
    assert r"^files\.pythonhosted\.org$" in composed


@docker_required
@pytest.mark.parametrize("base", ["filter.discord", "filter"])
def test_opt_in_on_non_executor_filter_fails_the_build(base):
    # The frontend holds the Discord token and the combined filter is the
    # single-container posture; neither may gain index egress. A doc warning is
    # not a guardrail — the build must refuse.
    with pytest.raises(subprocess.CalledProcessError):
        _build_filter(base, "filter.pypi")

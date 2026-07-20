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


# Half-anchored variants of the shipped lines. tinyproxy would tunnel upload.pypi.org
# for every one of these; a guard test using re.fullmatch calls them all safe.
# Dropping the LEADING anchor lets a subdomain in (upload.pypi.org); dropping the
# TRAILING one lets an attacker-registered suffix in (pypi.org.evil.example).
_ANCHOR_MUTANTS = [
    (r"pypi\.org$", "upload.pypi.org"),
    (r"pypi\.org", "upload.pypi.org"),
    (r".*pypi\.org.*", "upload.pypi.org"),
    (r"^pypi\.org", "pypi.org.evil.example"),
]


def _tinyproxy_would_allow(pattern: str, host: str) -> bool:
    """tinyproxy matches with POSIX regexec, which is UNANCHORED — a pattern matching
    any SUBSTRING of the CONNECT host allows it. re.fullmatch models a different engine
    and would score `pypi\.org$` as safe while the proxy tunnels upload.pypi.org."""
    return re.search(pattern, host, re.IGNORECASE) is not None


def test_no_shipped_filter_allows_a_publish_capable_host():
    # Guards against a later "just add npm too" edit: the exclusion is the whole
    # basis for README/SECURITY's no-publish-capable-host claim.
    # Match on active regex lines only: filter.pypi names these hosts in a comment
    # precisely to record *why* they are excluded, and a comment matches nothing.
    for path in PROXY.glob("filter*"):
        for line in _regex_lines(path):
            for host in PUBLISH_CAPABLE:
                assert not _tinyproxy_would_allow(line, host), \
                    f"{path.name} allow-lists publish-capable host {host} via {line!r}"


@pytest.mark.parametrize("mutant,host", _ANCHOR_MUTANTS)
def test_the_guard_itself_catches_a_dropped_anchor(mutant, host):
    # Meta-test: the guard above exists to stop a future one-character edit. Prove it
    # actually would — under re.fullmatch these all scored as "safe".
    assert _tinyproxy_would_allow(mutant, host), \
        f"guard would not catch {mutant!r} vs {host} — wrong regex engine"
    assert not _tinyproxy_would_allow(r"^pypi\.org$", host), \
        "the shipped double-anchored form must still reject it"


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


@docker_required
@pytest.mark.parametrize("extra", ["filter.discord", "filter", "../../etc/hostname"])
def test_build_rejects_any_extra_filter_but_the_index_one(extra):
    # Guarding only FILTER left the mirror-image footgun open: filter.anthropic +
    # EXTRA_FILTER=filter.discord built a proxy granting the CREDENTIAL container
    # Discord egress — the exact inversion the split exists to prevent. Traversal
    # (../../etc/hostname) would splice arbitrary file contents in as match patterns.
    with pytest.raises(subprocess.CalledProcessError):
        _build_filter("filter.anthropic", extra)


@docker_required
def test_composed_filter_denies_unlisted_hosts_in_a_live_proxy():
    """End-to-end against a running tinyproxy, not just file contents.

    The composition inserts a blank line between the base and appended filters. If a
    blank line ever compiled as an empty regex it would match EVERY host, silently
    turning default-deny into allow-all — a file-contents test cannot see that, and
    `FROM alpine:3.20` floats the tinyproxy version under us.
    """
    tag = "filtertest-live"
    subprocess.run(
        ["docker", "build", "-q", "-t", tag, "--build-arg", "FILTER=filter.anthropic",
         "--build-arg", "EXTRA_FILTER=filter.pypi", str(PROXY)],
        check=True, capture_output=True, timeout=600,
    )
    cid = subprocess.run(["docker", "run", "-d", "--rm", tag],
                         check=True, capture_output=True, timeout=120).stdout.decode().strip()
    try:
        probe = (
            "import socket,sys\n"
            "for h in ['pypi.org','api.anthropic.com','upload.pypi.org',"
            "'registry.npmjs.org','example.com']:\n"
            "    s=socket.create_connection(('127.0.0.1',8888),timeout=8)\n"
            "    s.sendall(('CONNECT %s:443 HTTP/1.1\\r\\nHost: %s:443\\r\\n\\r\\n'%(h,h)).encode())\n"
            "    print(h, s.recv(32).decode('utf-8','replace').split()[1]); s.close()\n"
        )
        # python3 is not in the proxy image; run the probe from a sibling container.
        out = subprocess.run(
            ["docker", "run", "--rm", f"--network=container:{cid}", "python:3.12-slim",
             "python3", "-c", probe],
            check=True, capture_output=True, timeout=180,
        ).stdout.decode()
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=60)
    status = dict(line.split() for line in out.strip().splitlines())
    assert status["pypi.org"] == "200", "opt-in host should tunnel"
    assert status["api.anthropic.com"] == "200", "base allow-list regressed"
    for denied in ("upload.pypi.org", "registry.npmjs.org", "example.com"):
        assert status[denied] == "403", f"{denied} was NOT denied (allow-all filter?)"

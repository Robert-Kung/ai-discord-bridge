"""Canary behaviour under the package-index opt-in (spec: egress-containment).

Two asymmetric requirements:
  - the FRONTEND must treat index hosts as forbidden, so an EXTRA_FILTER applied to
    the wrong proxy is caught fail-closed instead of silently widening the container
    that holds the Discord token;
  - the EXECUTOR must NOT gain an index-reachability gate — an index outage must not
    take it down — while its probe 1/2 default-deny proof stays mandatory.
"""
import inspect

import bot
import executor
from bridge import config, egress

D = egress.PROXY_DENIED
T = egress.PROXY_TUNNELED
I = egress.PROXY_INCONCLUSIVE

REFUSE = (egress.EGRESS_OPEN, egress.EGRESS_OPEN_PROXY, egress.EGRESS_PEER_REACHABLE)


def test_frontend_forbidden_set_covers_index_hosts_and_anthropic():
    for host in config.PACKAGE_INDEX_HOSTS:
        assert host in config.FRONTEND_FORBIDDEN_HOSTS
    assert config.ANTHROPIC_API_HOST in config.FRONTEND_FORBIDDEN_HOSTS, \
        "existing 5.3 deny direction regressed"


def test_frontend_canary_actually_probes_that_set(monkeypatch):
    # Behavioral, not a source-string check: review showed commenting out the real
    # canary call left the old source assertion green, i.e. the fail-closed startup
    # gate could be dead while the test still passed.
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", "/tmp/exec.sock")
    required, forbidden = bot.startup_canary_hosts()
    assert required == "discord.com"
    for host in config.PACKAGE_INDEX_HOSTS + (config.ANTHROPIC_API_HOST,):
        assert host in forbidden


def test_single_container_still_probes_index_hosts(monkeypatch):
    # design.md called this "unsupported by construction", but the build assertion only
    # covers the image; an operator hand-editing egress-proxy/filter would otherwise
    # get index egress in the container holding BOTH secrets, undetected.
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", "")
    required, forbidden = bot.startup_canary_hosts()
    assert required is None  # phase-1 posture: Anthropic is the implicit required host
    for host in config.PACKAGE_INDEX_HOSTS:
        assert host in forbidden


def test_frontend_refuses_to_serve_when_an_index_host_is_reachable():
    # Contained in every other respect; only the index host tunnels.
    forbidden = tuple(
        T if h in config.PACKAGE_INDEX_HOSTS else D
        for h in config.FRONTEND_FORBIDDEN_HOSTS
    )
    assert egress.classify_egress(False, D, T, forbidden) == egress.EGRESS_PEER_REACHABLE
    assert egress.classify_egress(False, D, T, forbidden) in REFUSE


def test_executor_keeps_its_default_deny_proof_under_the_opt_in():
    # Probes 1 and 2 stay mandatory regardless of the opt-in: a live direct route, a
    # tunneling proxy, or an unprovable deny all still refuse to reach OK, even with
    # every forbidden host correctly denied.
    denied = tuple(D for _ in config.EXECUTOR_FORBIDDEN_HOSTS)
    assert egress.classify_egress(True, D, T, denied) == egress.EGRESS_OPEN
    assert egress.classify_egress(False, T, T, denied) == egress.EGRESS_OPEN_PROXY
    assert egress.classify_egress(False, I, T, denied) == egress.EGRESS_INCONCLUSIVE


def test_executor_has_no_index_reachability_gate():
    # An index outage must surface when an install runs, not as a startup failure, so
    # the READ hosts must never be probed as required-or-forbidden on this side.
    for host in config.PACKAGE_INDEX_HOSTS:
        assert host not in config.EXECUTOR_FORBIDDEN_HOSTS
    assert "PACKAGE_INDEX_HOSTS" not in inspect.getsource(executor)


def test_executor_proves_publish_hosts_stay_denied():
    # The opt-in's entire basis is that upload endpoints remain unreachable, so the
    # executor asserts it at startup rather than trusting the filter file. This is not
    # an outage gate: a forbidden probe expects 403, which tinyproxy answers itself
    # without ever contacting the host.
    for host in config.PUBLISH_CAPABLE_HOSTS:
        assert host in config.EXECUTOR_FORBIDDEN_HOSTS
    assert "upload.pypi.org" in config.PUBLISH_CAPABLE_HOSTS
    assert "registry.npmjs.org" in config.PUBLISH_CAPABLE_HOSTS


def test_executor_canary_wiring_passes_the_forbidden_set():
    assert "forbidden_hosts=config.EXECUTOR_FORBIDDEN_HOSTS" in inspect.getsource(executor)


def test_executor_refuses_to_serve_if_a_publish_host_is_reachable():
    forbidden = tuple(
        T if h in config.PUBLISH_CAPABLE_HOSTS else D
        for h in config.EXECUTOR_FORBIDDEN_HOSTS
    )
    assert egress.classify_egress(False, D, T, forbidden) == egress.EGRESS_PEER_REACHABLE


def test_executor_startup_is_unaffected_by_index_state():
    # The executor's canary inputs never include a READ index host, so no index outage
    # can appear in them: a correct posture classifies OK either way.
    denied = tuple(D for _ in config.EXECUTOR_FORBIDDEN_HOSTS)
    assert egress.classify_egress(False, D, T, denied) == egress.EGRESS_OK

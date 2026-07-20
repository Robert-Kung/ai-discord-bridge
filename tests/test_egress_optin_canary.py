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


def test_frontend_canary_actually_probes_that_set():
    # The constant is only worth anything if the split-deploy gate passes it.
    src = inspect.getsource(bot)
    assert "forbidden_hosts=config.FRONTEND_FORBIDDEN_HOSTS" in src


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
    denied = tuple(D for _ in config.DISCORD_HOSTS)
    assert egress.classify_egress(True, D, T, denied) == egress.EGRESS_OPEN
    assert egress.classify_egress(False, T, T, denied) == egress.EGRESS_OPEN_PROXY
    assert egress.classify_egress(False, I, T, denied) == egress.EGRESS_INCONCLUSIVE


def test_executor_has_no_index_reachability_gate():
    # An index outage must surface when an install runs, not as a startup failure.
    assert "PACKAGE_INDEX_HOSTS" not in inspect.getsource(executor)
    assert "FRONTEND_FORBIDDEN_HOSTS" not in inspect.getsource(executor)


def test_executor_startup_is_unaffected_by_index_state():
    # The executor's canary inputs are the control host + Discord hosts only, so no
    # index status can appear in them: a correct posture classifies OK either way.
    denied = tuple(D for _ in config.DISCORD_HOSTS)
    assert egress.classify_egress(False, D, T, denied) == egress.EGRESS_OK

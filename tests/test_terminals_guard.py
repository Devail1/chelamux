"""The terminal wall is on by default but loopback-guarded: the dashboard only
serves its writable shells on a loopback bind unless CHELA_TERMINALS_EXPOSE is
set. These tests lock in the host classification and the serve decision."""
import pytest

from chela import config


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "", "  127.0.0.1  ", "LOCALHOST"])
def test_loopback_hosts(host):
    assert config.is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "100.64.1.2", "192.168.1.5", "example.com"])
def test_non_loopback_hosts(host):
    assert config.is_loopback_host(host) is False


def _serves(host, enabled, expose):
    """Mirror of the guard in dashboard.app.main()."""
    return enabled and (config.is_loopback_host(host) or expose)


@pytest.mark.parametrize(
    "host,enabled,expose,expected",
    [
        ("127.0.0.1", True, False, True),    # loopback: served
        ("localhost", True, False, True),
        ("0.0.0.0", True, False, False),     # public bind, no opt-in: refused
        ("100.64.1.2", True, False, False),  # tailnet IP, no opt-in: refused
        ("0.0.0.0", True, True, True),       # public bind + explicit expose: served
        ("127.0.0.1", False, False, False),  # wall turned off entirely
    ],
)
def test_wall_serve_decision(host, enabled, expose, expected):
    assert _serves(host, enabled, expose) is expected

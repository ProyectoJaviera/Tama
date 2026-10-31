import socket

from tama.net import Connectivity, tcp_probe
from tests.fakes import FakeClock


def test_connectivity_is_cached_for_ttl():
    clock, calls = FakeClock(), []

    def probe():
        calls.append(1)
        return True

    connectivity = Connectivity(probe, ttl=30, clock=clock)
    assert connectivity.online() and connectivity.online()
    assert len(calls) == 1

    clock.now += 30
    connectivity.online()
    assert len(calls) == 2


def test_invalidate_forces_new_probe():
    results = iter([True, False])
    connectivity = Connectivity(
        lambda: next(results), ttl=30, clock=FakeClock(), misses_to_offline=1
    )
    assert connectivity.online() is True
    connectivity.invalidate()
    assert connectivity.online() is False


def test_tcp_probe_false_when_nothing_listens():
    # Un puerto local recién cerrado: la conexión se rechaza al instante, sin red.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert tcp_probe("127.0.0.1", port, timeout=0.5) is False


def test_tcp_probe_true_when_port_is_open():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        assert tcp_probe("127.0.0.1", server.getsockname()[1], timeout=0.5) is True


def test_one_failed_probe_does_not_mean_offline():
    results = iter([True, False, False])
    connectivity = Connectivity(lambda: next(results), ttl=0)
    assert connectivity.online() is True
    assert connectivity.online() is True  # una falla: se duda
    assert connectivity.online() is False  # dos seguidas: sin red

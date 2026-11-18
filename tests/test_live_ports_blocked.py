"""The suite cannot reach the live CODEC servers on this Mac (docs/MODEL-MEMORY-GUARD-DESIGN.md, option A)."""
import os
import socket

import pytest

from conftest import LIVE_PORTS


pytestmark = pytest.mark.skipif(os.environ.get("CODEC_TESTS_LIVE") == "1",
                                reason="live servers allowed for this run")


@pytest.mark.parametrize("port", sorted(LIVE_PORTS))
def test_a_live_port_is_refused_before_leaving_the_process(port):
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=1)


def test_the_model_server_is_refused_through_requests():
    import requests
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.post("http://localhost:8083/v1/chat/completions", json={"model": ""}, timeout=2)


def test_other_loopback_ports_still_connect():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        port = srv.getsockname()[1]
        assert port not in LIVE_PORTS
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    finally:
        srv.close()

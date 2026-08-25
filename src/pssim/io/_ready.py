"""Waiting for a server to be ready to answer.

Here rather than in a test file because all three integration harnesses need it
and a fourth copy of the same loop would eventually differ from the other three.
It is only ever pointed at `pssim mock-server`.
"""

from __future__ import annotations

import socket
import time

DEFAULT_TIMEOUT_S = 20.0


def wait_for_endpoint(endpoint: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> bool:
    """Block until a server answers on the endpoint's port, or the time runs out.

    A fixed sleep is what this replaces: it is either longer than the wait (every
    test pays for it) or shorter than it on a slow machine (the test fails for a
    reason that has nothing to do with what it checks).
    """
    host, port = _address_of(endpoint)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _address_of(endpoint: str) -> tuple[str, int]:
    """`opc.tcp://host:port/path/` -> `(host, port)`."""
    authority = endpoint.split("//", 1)[-1].split("/", 1)[0]
    host, _, port = authority.rpartition(":")
    return host or "127.0.0.1", int(port or 4840)

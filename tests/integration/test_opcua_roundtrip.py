"""Integration tests against the mock OPC UA server.

Never against a real machine. Run with: ``uv run pytest -m integration``
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from pssim.config.binding import JointBinding
from pssim.io.base import SourceStatus
from pssim.io.mock_server import DEFAULT_AXES, MockAxis, run_mock_server
from pssim.io.opcua_source import OpcUaConfig, OpcUaSource

pytestmark = pytest.mark.integration

ENDPOINT = "opc.tcp://127.0.0.1:48400/pssim-test/"
NAMESPACE_INDEX = 2


class MockServerThread:
    """The mock server in its own thread, so the test can connect a client meanwhile."""

    def __init__(self, duration_s: float = 15.0) -> None:
        self._duration_s = duration_s
        self._thread = threading.Thread(target=self._run, name="mock-server", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                ENDPOINT, DEFAULT_AXES, update_interval_s=0.05, duration_s=self._duration_s
            )
        )

    def __enter__(self) -> MockServerThread:
        self._thread.start()
        time.sleep(1.5)  # the server needs time to open the endpoint
        return self

    def __exit__(self, *_: object) -> None:
        self._thread.join(timeout=self._duration_s + 5.0)


def wait_until(predicate: object, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.1)
    return False


class TestMockAxis:
    """Generating the values is a pure function — tested without the server."""

    def test_at_time_zero_it_is_mid_stroke(self) -> None:
        axis = MockAxis(name="X", amplitude=100.0, center=50.0)

        assert axis.value_at(0.0) == pytest.approx(50.0)

    def test_at_a_quarter_period_it_is_at_the_maximum(self) -> None:
        axis = MockAxis(name="X", amplitude=100.0, center=0.0, period_s=8.0)

        assert axis.value_at(2.0) == pytest.approx(100.0)

    def test_the_motion_is_periodic(self) -> None:
        axis = MockAxis(name="X", amplitude=100.0, period_s=8.0)

        assert axis.value_at(0.0) == pytest.approx(axis.value_at(8.0), abs=1e-9)


class TestSubscription:
    def test_data_arrives_from_the_mock_server(self) -> None:
        bindings = (
            JointBinding(
                joint_name="axis_x",
                node_id=f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos",
                scale=1e-3,
            ),
        )
        source = OpcUaSource(OpcUaConfig(endpoint=ENDPOINT, bindings=bindings))

        with MockServerThread():
            source.start()
            received = wait_until(lambda: len(source.store) > 0)
            status = source.status
            source.stop()

        assert received, "not a single sample arrived within 10 s"
        assert status is SourceStatus.CONNECTED

    def test_values_are_converted_into_metres(self) -> None:
        # The mock sends mm (amplitude 1200, centre 1250) → 0.05 to 2.45 in metres.
        bindings = (
            JointBinding(
                joint_name="axis_x",
                node_id=f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos",
                scale=1e-3,
            ),
        )
        source = OpcUaSource(OpcUaConfig(endpoint=ENDPOINT, bindings=bindings))

        with MockServerThread():
            source.start()
            wait_until(lambda: len(source.store) > 0)
            latest = source.store.latest_time()
            value = source.store.sample("axis_x", at_time_s=latest) if latest else None
            source.stop()

        assert value is not None
        assert 0.0 <= value <= 2.6


class TestResilience:
    def test_without_a_server_it_keeps_reconnecting(self) -> None:
        bindings = (JointBinding(joint_name="axis_x", node_id="ns=2;s=Nic"),)
        source = OpcUaSource(
            OpcUaConfig(endpoint="opc.tcp://127.0.0.1:1/nikde/", bindings=bindings)
        )

        source.start()
        time.sleep(2.0)
        status = source.status
        source.stop()

        assert status in (SourceStatus.CONNECTING, SourceStatus.DISCONNECTED)

    def test_stop_is_idempotent(self) -> None:
        bindings = (JointBinding(joint_name="axis_x", node_id="ns=2;s=Nic"),)
        source = OpcUaSource(
            OpcUaConfig(endpoint="opc.tcp://127.0.0.1:1/nikde/", bindings=bindings)
        )

        source.start()
        source.stop()
        source.stop()

        assert source.status is SourceStatus.DISCONNECTED

"""Integration tests against the mock OPC UA server.

Never against a real machine. Run with: ``uv run pytest -m integration``
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time

import pytest

from pssim.config.binding import BindingDirection, JointBinding, VariableBinding
from pssim.domain.errors import DataSourceError
from pssim.io._ready import wait_for_endpoint
from pssim.io.base import SourceStatus
from pssim.io.mock_server import (
    DEFAULT_AXES,
    DEFAULT_OUTPUTS,
    MockAxis,
    run_mock_server,
)
from pssim.io.opcua_browser import browse_variables
from pssim.io.opcua_source import OpcUaConfig, OpcUaSource

pytestmark = pytest.mark.integration

FIRST_PORT = 48400
_ports = itertools.count(FIRST_PORT)
NAMESPACE_INDEX = 2


class MockServerThread:
    """The mock server in its own thread, so the test can connect a client meanwhile."""

    def __init__(self, duration_s: float = 30.0) -> None:
        # Its own port per instance. One shared port worked only while a fixed
        # sleep separated the tests; a bind now lands before the previous
        # server's socket has been released.
        self.endpoint = f"opc.tcp://127.0.0.1:{next(_ports)}/pssim-test/"
        self._duration_s = duration_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mock-server", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                self.endpoint,
                DEFAULT_AXES,
                update_interval_s=0.05,
                duration_s=self._duration_s,
                stop_event=self._stop,
            )
        )

    def __enter__(self) -> MockServerThread:
        self._thread.start()
        # Asked rather than slept for: a fixed wait is either longer than
        # the server needs or shorter than it on a slow machine.
        assert wait_for_endpoint(self.endpoint), f"no server on {self.endpoint}"
        return self

    def __exit__(self, *_: object) -> None:
        # Stopped when the test is done rather than left to run out its span:
        # servers that overlap are what made unrelated tests fail, and waiting
        # out a fixed lifetime is what made the suite take twelve minutes.
        self._stop.set()
        self._thread.join(timeout=10.0)


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
        with MockServerThread() as server:
            source = OpcUaSource(OpcUaConfig(endpoint=server.endpoint, bindings=bindings))
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
        with MockServerThread() as server:
            source = OpcUaSource(OpcUaConfig(endpoint=server.endpoint, bindings=bindings))
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


async def read_node(endpoint: str, node_id: str) -> float:
    """One value straight from the server, for checking what a write landed as."""
    from asyncua import Client

    async with Client(url=endpoint) as client:
        return float(await client.get_node(node_id).read_value())


def value_of(endpoint: str, node_id: str) -> float:
    return asyncio.run(read_node(endpoint, node_id))


OUTPUT_NODE = f"ns={NAMESPACE_INDEX};s=Sim.Sensor1"


class TestWriting:
    """The write path. Exercised here and nowhere else — the mock server is the
    only server this project may ever write to (`.claude/rules/io-opcua.md`).
    """

    def test_writing_is_off_by_default(self) -> None:
        with MockServerThread() as server:
            source = OpcUaSource(
                OpcUaConfig(
                    endpoint=server.endpoint,
                    bindings=(
                        VariableBinding(
                            variable="gate",
                            node_id=OUTPUT_NODE,
                            direction=BindingDirection.WRITE,
                        ),
                    ),
                )
            )
            source.start()
            try:
                assert wait_until(lambda: source.status is SourceStatus.CONNECTED)
                source.store.queue_write("gate", 42.0)
                time.sleep(1.0)

                # Nothing carried it: with the switch off the pump is never made.
                assert value_of(server.endpoint, OUTPUT_NODE) == pytest.approx(0.0)
                assert source.store.pending_writes() == {"gate": 42.0}
            finally:
                source.stop()

    def test_an_allowed_write_reaches_the_server(self) -> None:
        with MockServerThread() as server:
            source = OpcUaSource(
                OpcUaConfig(
                    endpoint=server.endpoint,
                    allow_writing=True,
                    bindings=(
                        VariableBinding(
                            variable="gate",
                            node_id=OUTPUT_NODE,
                            direction=BindingDirection.WRITE,
                        ),
                    ),
                )
            )
            source.start()
            try:
                assert wait_until(lambda: source.status is SourceStatus.CONNECTED)
                source.store.queue_write("gate", 7.0)

                assert wait_until(
                    lambda: value_of(server.endpoint, OUTPUT_NODE) == pytest.approx(7.0)
                )
            finally:
                source.stop()

    def test_the_conversion_is_applied_on_the_way_out(self) -> None:
        # The scene holds metres; the PLC is handed millimetres (R8).
        with MockServerThread() as server:
            source = OpcUaSource(
                OpcUaConfig(
                    endpoint=server.endpoint,
                    allow_writing=True,
                    bindings=(
                        VariableBinding(
                            variable="distance",
                            node_id=OUTPUT_NODE,
                            scale=0.001,
                            direction=BindingDirection.WRITE,
                        ),
                    ),
                )
            )
            source.start()
            try:
                assert wait_until(lambda: source.status is SourceStatus.CONNECTED)
                source.store.queue_write("distance", 1.25)

                assert wait_until(
                    lambda: value_of(server.endpoint, OUTPUT_NODE) == pytest.approx(1250.0)
                )
            finally:
                source.stop()

    def test_a_refused_write_does_not_kill_the_session(self) -> None:
        # An axis node is read-only, and the server answers BadUserAccessDenied.
        # The pump has to survive that: an exception there ends the session.
        with MockServerThread() as server:
            source = OpcUaSource(
                OpcUaConfig(
                    endpoint=server.endpoint,
                    allow_writing=True,
                    bindings=(
                        JointBinding(
                            joint_name="axis_x",
                            node_id=f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos",
                            scale=0.001,
                        ),
                        VariableBinding(
                            variable="nope",
                            node_id=f"ns={NAMESPACE_INDEX};s=Axes.Z.ActPos",
                            direction=BindingDirection.WRITE,
                        ),
                    ),
                )
            )
            source.start()
            try:
                assert wait_until(lambda: source.status is SourceStatus.CONNECTED)
                source.store.queue_write("nope", 1.0)
                time.sleep(1.0)

                assert source.status is SourceStatus.CONNECTED
                assert wait_until(lambda: "axis_x" in source.store.signal_names)
            finally:
                source.stop()


class TestBrowsing:
    """Picking a tag from what the server actually has, rather than typing one."""

    def test_it_finds_the_axes(self) -> None:
        with MockServerThread() as server:
            found = {node.node_id for node in browse_variables(server.endpoint)}

        assert f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos" in found

    def test_it_skips_the_standard_namespace(self) -> None:
        # Otherwise the three nodes somebody wants are buried under a hundred
        # server diagnostics they do not.
        with MockServerThread() as server:
            found = browse_variables(server.endpoint)

        assert all(not node.node_id.startswith("i=") for node in found)
        assert len(found) == len(DEFAULT_AXES) + len(DEFAULT_OUTPUTS)

    def test_it_reports_the_browse_path(self) -> None:
        with MockServerThread() as server:
            paths = {node.browse_path for node in browse_variables(server.endpoint)}

        assert "Axes / Axes.X.ActPos" in paths

    def test_it_reports_the_data_type(self) -> None:
        with MockServerThread() as server:
            found = {node.node_id: node for node in browse_variables(server.endpoint)}

        assert found[f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos"].data_type == "Double"

    def test_an_axis_is_not_writable(self) -> None:
        with MockServerThread() as server:
            found = {node.node_id: node for node in browse_variables(server.endpoint)}

        assert found[f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos"].is_writable is False

    def test_an_output_is_writable(self) -> None:
        # Which is what a sensor's variable needs to be bound to.
        with MockServerThread() as server:
            found = {node.node_id: node for node in browse_variables(server.endpoint)}

        assert found[OUTPUT_NODE].is_writable is True

    def test_a_server_that_is_not_there_is_a_typed_error(self) -> None:
        # A browse is a question with an answer, unlike a subscription dropping,
        # which is a normal state to retry (R12).
        with pytest.raises(DataSourceError):
            browse_variables("opc.tcp://127.0.0.1:1/nothing/", timeout_s=2.0)

    def test_an_empty_endpoint_is_refused(self) -> None:
        with pytest.raises(DataSourceError):
            browse_variables("")

    def test_the_timeout_covers_the_connection(self) -> None:
        # A host that accepts a TCP connection and then says nothing is exactly
        # what a browse has to survive; asyncua's own connect timeout is not
        # this one, so the deadline has to wrap the connect as well as the walk.
        started = time.monotonic()

        with pytest.raises(DataSourceError):
            browse_variables("opc.tcp://10.255.255.1:4840/", timeout_s=1.5)

        assert time.monotonic() - started < 4.0

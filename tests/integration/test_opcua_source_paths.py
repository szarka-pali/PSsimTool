"""Reading one field of a struct, or one element of an array, over a live
subscription.

The decisive test in here is `test_the_struct_path_agrees_with_the_plain_node`:
the mock's `Struct.AxisState.Position` tracks the same axes as
`Axes.X.ActPos`, so a path into the struct and the plain node must produce the
same number. That pins the extraction rather than merely exercising it — a wrong
field, a stale decode or a double conversion all show up as a mismatch.

Against `pssim mock-server` and nothing else. Run with ``uv run pytest -m integration``.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from pssim.config.binding import JointBinding
from pssim.io._ready import wait_for_endpoint
from pssim.io.base import SourceStatus
from pssim.io.mock_server import run_mock_server
from pssim.io.opcua_source import OpcUaConfig, OpcUaSource

pytestmark = pytest.mark.integration

FIRST_PORT = 48490
_ports = itertools.count(FIRST_PORT)

NAMESPACE_INDEX = 2
STATE_NODE = f"ns={NAMESPACE_INDEX};s=Struct.AxisState"
ARRAY_NODE = f"ns={NAMESPACE_INDEX};s=Struct.Positions"
AXIS_NODE = f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos"


class PathMockServer:
    def __init__(self, duration_s: float = 60.0) -> None:
        self.endpoint = f"opc.tcp://127.0.0.1:{next(_ports)}/pssim-paths/"
        self._duration_s = duration_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mock-paths", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                self.endpoint,
                update_interval_s=0.05,
                duration_s=self._duration_s,
                stop_event=self._stop,
            )
        )

    def __enter__(self) -> PathMockServer:
        self._thread.start()
        assert wait_for_endpoint(self.endpoint), f"no server on {self.endpoint}"
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)


def wait_until(predicate: object, timeout_s: float = 15.0) -> bool:
    """Poll until true. The condition is a subscription on another thread, so
    there is nothing to await."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.05)
    return False


@contextmanager
def running(endpoint: str, *bindings: JointBinding) -> Iterator[OpcUaSource]:
    """A started source, stopped whatever happens.

    A context manager rather than a bare generator: `for source in ...` makes
    every `lambda` below capture a loop variable, which ruff refuses and which
    would be a real trap if the loop ever ran twice.
    """
    source = OpcUaSource(OpcUaConfig(endpoint=endpoint, bindings=bindings))
    source.start()
    try:
        yield source
    finally:
        source.stop()


class TestReadingAField:
    def test_a_nested_field_arrives(self) -> None:
        binding = JointBinding(joint_name="tilt", node_id=STATE_NODE, path="Position.X")
        with PathMockServer() as server, running(server.endpoint, binding) as source:
            assert wait_until(lambda: "tilt" in source.store.signal_names)

    def test_and_it_is_a_number(self) -> None:
        binding = JointBinding(joint_name="tilt", node_id=STATE_NODE, path="Position.X")
        with PathMockServer() as server, running(server.endpoint, binding) as source:
            assert wait_until(lambda: len(source.store) > 0)
            latest = source.store.latest_time()
            assert latest is not None
            value = source.store.sample("tilt", at_time_s=latest)

        assert value is not None

    def test_a_boolean_field_arrives_too(self) -> None:
        # `NUMERIC_TYPES` has always promised `Boolean`, and the chooser offers
        # it. A PLC's `Enabled` flag is an ordinary thing to show.
        binding = JointBinding(joint_name="enabled", node_id=STATE_NODE, path="Enabled")
        with PathMockServer() as server, running(server.endpoint, binding) as source:
            assert wait_until(lambda: "enabled" in source.store.signal_names)

    def test_an_array_element_arrives(self) -> None:
        binding = JointBinding(joint_name="limit", node_id=STATE_NODE, path="Limits[1]")
        with PathMockServer() as server, running(server.endpoint, binding) as source:
            assert wait_until(lambda: "limit" in source.store.signal_names)

    def test_an_element_of_a_bare_array_arrives(self) -> None:
        binding = JointBinding(joint_name="first", node_id=ARRAY_NODE, path="[0]")
        with PathMockServer() as server, running(server.endpoint, binding) as source:
            assert wait_until(lambda: "first" in source.store.signal_names)


class TestTheValueIsRight:
    def test_the_struct_path_agrees_with_the_plain_node(self) -> None:
        # The mock's `Position` tracks the same axes as the scalar nodes, so
        # these two must be the same number. A wrong field, a stale decode or a
        # double conversion all show up here.
        through_struct = JointBinding(
            joint_name="via_struct", node_id=STATE_NODE, path="Position.X"
        )
        plain = JointBinding(joint_name="via_node", node_id=AXIS_NODE)

        with (
            PathMockServer() as server,
            running(server.endpoint, through_struct, plain) as source,
        ):
            assert wait_until(lambda: {"via_struct", "via_node"} <= set(source.store.signal_names))
            latest = source.store.latest_time()
            assert latest is not None
            sampled = source.store.sample_all(at_time_s=latest)

        # Interpolated at one instant, so the two agree to the width of one
        # publishing interval of movement rather than exactly.
        assert sampled["via_struct"] == pytest.approx(sampled["via_node"], abs=60.0)

    def test_a_fixed_field_is_exact(self) -> None:
        # `Limits[1]` never changes, so there is no interval of movement to
        # allow for: this one is the value or it is wrong.
        binding = JointBinding(joint_name="limit", node_id=STATE_NODE, path="Limits[1]")
        with PathMockServer() as server, running(server.endpoint, binding) as source:
            assert wait_until(lambda: len(source.store) > 0)
            latest = source.store.latest_time()
            assert latest is not None
            value = source.store.sample("limit", at_time_s=latest)

        assert value == pytest.approx(2450.0)

    def test_the_conversion_still_happens_after_the_path(self) -> None:
        # A path resolves, and *then* the scaling applies (R8). Both, in order.
        binding = JointBinding(
            joint_name="limit_m", node_id=STATE_NODE, path="Limits[1]", scale=0.001
        )
        with PathMockServer() as server, running(server.endpoint, binding) as source:
            assert wait_until(lambda: len(source.store) > 0)
            latest = source.store.latest_time()
            assert latest is not None
            value = source.store.sample("limit_m", at_time_s=latest)

        assert value == pytest.approx(2.45)


class TestOneNotificationFeedsSeveralSignals:
    """`Position.X` and `Position.Y` are two signals reading two places in one
    notification. A dict keyed by node id would have kept only the last."""

    def test_both_fields_of_one_node_arrive(self) -> None:
        with (
            PathMockServer() as server,
            running(
                server.endpoint,
                JointBinding(joint_name="x", node_id=STATE_NODE, path="Position.X"),
                JointBinding(joint_name="y", node_id=STATE_NODE, path="Position.Y"),
            ) as source,
        ):
            assert wait_until(lambda: {"x", "y"} <= set(source.store.signal_names))

    def test_three_of_them(self) -> None:
        with (
            PathMockServer() as server,
            running(
                server.endpoint,
                JointBinding(joint_name="x", node_id=STATE_NODE, path="Position.X"),
                JointBinding(joint_name="y", node_id=STATE_NODE, path="Position.Y"),
                JointBinding(joint_name="z", node_id=STATE_NODE, path="Position.Z"),
            ) as source,
        ):
            assert wait_until(lambda: {"x", "y", "z"} <= set(source.store.signal_names))

    def test_the_same_node_is_subscribed_once(self) -> None:
        # Three signals, one monitored item: the diagnostics say how many.
        with (
            PathMockServer() as server,
            running(
                server.endpoint,
                JointBinding(joint_name="x", node_id=STATE_NODE, path="Position.X"),
                JointBinding(joint_name="y", node_id=STATE_NODE, path="Position.Y"),
                JointBinding(joint_name="z", node_id=STATE_NODE, path="Position.Z"),
            ) as source,
        ):
            assert wait_until(lambda: source.status is SourceStatus.CONNECTED)
            subscribed = [
                entry for entry in source.diagnostics.entries if entry.step.value == "subscribe"
            ]

        assert subscribed
        assert subscribed[-1].detail.startswith("1 signal")


class TestAPathThatDoesNotFit:
    """One signal's problem, not the subscription's."""

    def test_a_missing_field_does_not_stop_the_others(self) -> None:
        with (
            PathMockServer() as server,
            running(
                server.endpoint,
                JointBinding(joint_name="nope", node_id=STATE_NODE, path="Velocity"),
                JointBinding(joint_name="good", node_id=STATE_NODE, path="Position.X"),
            ) as source,
        ):
            assert wait_until(lambda: "good" in source.store.signal_names)
            names = set(source.store.signal_names)
            status = source.status

        assert "nope" not in names
        assert status is SourceStatus.CONNECTED

    def test_a_non_numeric_field_is_refused(self) -> None:
        # Refused rather than scaled: `Name` is a string.
        with (
            PathMockServer() as server,
            running(
                server.endpoint,
                JointBinding(joint_name="name", node_id=STATE_NODE, path="Name"),
                JointBinding(joint_name="good", node_id=STATE_NODE, path="Position.X"),
            ) as source,
        ):
            assert wait_until(lambda: "good" in source.store.signal_names)
            names = set(source.store.signal_names)

        assert "name" not in names

    def test_a_whole_struct_is_refused(self) -> None:
        # An empty path on a struct node: the value is an object, not a number.
        with (
            PathMockServer() as server,
            running(
                server.endpoint,
                JointBinding(joint_name="whole", node_id=STATE_NODE),
                JointBinding(joint_name="good", node_id=AXIS_NODE),
            ) as source,
        ):
            assert wait_until(lambda: "good" in source.store.signal_names)
            names = set(source.store.signal_names)
            status = source.status

        assert "whole" not in names
        assert status is SourceStatus.CONNECTED

    def test_an_index_past_the_end_is_refused(self) -> None:
        with (
            PathMockServer() as server,
            running(
                server.endpoint,
                JointBinding(joint_name="far", node_id=STATE_NODE, path="Limits[9]"),
                JointBinding(joint_name="good", node_id=STATE_NODE, path="Position.X"),
            ) as source,
        ):
            assert wait_until(lambda: "good" in source.store.signal_names)
            names = set(source.store.signal_names)

        assert "far" not in names


class TestAPlainSetupPaysNothing:
    def test_a_scalar_only_source_still_connects(self) -> None:
        # `load_data_type_definitions` is a round trip and a code generation, and
        # every setup that existed before this reads plain scalars.
        with (
            PathMockServer() as server,
            running(
                server.endpoint, JointBinding(joint_name="axis_x", node_id=AXIS_NODE)
            ) as source,
        ):
            assert wait_until(lambda: "axis_x" in source.store.signal_names)

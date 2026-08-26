"""The mock server's structured nodes.

A mock made only of scalars is what let a client ship that stops browsing at a
variable: a real PLC's address space is mostly structures, and there was nothing
here to notice that against. So the mock now has a struct, a struct nested inside
it, an array inside that, a bare array, and one field that is deliberately not a
number.

The property that makes the rest testable: **`Struct.AxisState.Position` tracks
the same axes as the scalar nodes.** Binding a path into the struct and binding
the plain axis node must give the same number, and that is the test that pins the
extraction rather than merely exercising it.

Against `pssim mock-server` and nothing else. Run with ``uv run pytest -m integration``.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from typing import Any

import pytest

from pssim.io._ready import wait_for_endpoint
from pssim.io.mock_server import (
    AXIS_STATE_TYPE_NAME,
    DEFAULT_AXES,
    POINT_TYPE_NAME,
    run_mock_server,
)

pytestmark = pytest.mark.integration

FIRST_PORT = 48470
_ports = itertools.count(FIRST_PORT)

NAMESPACE_INDEX = 2
STATE_NODE = f"ns={NAMESPACE_INDEX};s=Struct.AxisState"
POINT_NODE = f"ns={NAMESPACE_INDEX};s=Struct.Point"
ARRAY_NODE = f"ns={NAMESPACE_INDEX};s=Struct.Positions"
AXIS_NODE = f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos"


class StructMockServer:
    """The mock in its own thread, on its own port."""

    def __init__(self, duration_s: float = 40.0) -> None:
        self.endpoint = f"opc.tcp://127.0.0.1:{next(_ports)}/pssim-structs/"
        self._duration_s = duration_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mock-structs", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                self.endpoint,
                update_interval_s=0.05,
                duration_s=self._duration_s,
                stop_event=self._stop,
            )
        )

    def __enter__(self) -> StructMockServer:
        self._thread.start()
        assert wait_for_endpoint(self.endpoint), f"no server on {self.endpoint}"
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)


@pytest.fixture(scope="module")
def server() -> Any:
    """One server for the module: every test here only reads."""
    with StructMockServer(duration_s=120.0) as running:
        yield running


async def _read(endpoint: str, node_ids: tuple[str, ...]) -> tuple[Any, ...]:
    """Values, with the type definitions loaded so a struct arrives decoded."""
    from asyncua import Client

    async with Client(url=endpoint) as client:
        # Without this the value is a raw `ExtensionObject` of undecoded bytes.
        await client.load_data_type_definitions()
        return tuple([await client.get_node(node_id).read_value() for node_id in node_ids])


def read(endpoint: str, *node_ids: str) -> tuple[Any, ...]:
    return asyncio.run(_read(endpoint, node_ids))


class TestTheStructIsThere:
    def test_the_state_node_decodes_to_a_class(self, server: Any) -> None:
        (state,) = read(server.endpoint, STATE_NODE)

        assert type(state).__name__ == AXIS_STATE_TYPE_NAME

    def test_it_holds_a_nested_struct(self, server: Any) -> None:
        # The case a one-level-deep implementation gets wrong and nobody notices
        # until a real PLC.
        (state,) = read(server.endpoint, STATE_NODE)

        assert type(state.Position).__name__ == POINT_TYPE_NAME

    def test_the_nested_struct_has_three_numbers(self, server: Any) -> None:
        (state,) = read(server.endpoint, STATE_NODE)

        assert isinstance(state.Position.X, float)

    def test_it_holds_an_array(self, server: Any) -> None:
        (state,) = read(server.endpoint, STATE_NODE)

        assert list(state.Limits) == [0.0, 2450.0]

    def test_one_field_is_deliberately_not_a_number(self, server: Any) -> None:
        # Selecting it has to be refused rather than scaled.
        (state,) = read(server.endpoint, STATE_NODE)

        assert isinstance(state.Name, str)

    def test_a_struct_can_also_stand_on_its_own(self, server: Any) -> None:
        (point,) = read(server.endpoint, POINT_NODE)

        assert type(point).__name__ == POINT_TYPE_NAME

    def test_the_bare_array_is_a_list(self, server: Any) -> None:
        (array,) = read(server.endpoint, ARRAY_NODE)

        assert len(array) == len(DEFAULT_AXES)


class TestTheStructFollowsTheAxes:
    """What makes the extraction testable rather than merely exercised."""

    def test_the_nested_x_is_the_x_axis(self, server: Any) -> None:
        # Read in one session, so the two are the same instant.
        state, axis = read(server.endpoint, STATE_NODE, AXIS_NODE)

        assert pytest.approx(axis, abs=1.0) == state.Position.X

    def test_the_array_holds_the_same_axes(self, server: Any) -> None:
        array, axis = read(server.endpoint, ARRAY_NODE, AXIS_NODE)

        assert array[0] == pytest.approx(axis, abs=1.0)

    def test_the_values_move(self, server: Any) -> None:
        first, _ = read(server.endpoint, STATE_NODE, AXIS_NODE)
        # A second read, a couple of update intervals later. No sleep: opening a
        # session is itself slower than the 50 ms the server updates at.
        second, _ = read(server.endpoint, STATE_NODE, AXIS_NODE)

        assert first.Position.X != second.Position.X


class TestStructsCanBeTurnedOff:
    def test_a_server_without_them_has_none(self) -> None:
        # The flag exists so a test about something else is not paying for them.
        endpoint = f"opc.tcp://127.0.0.1:{next(_ports)}/pssim-plain/"
        stop = threading.Event()
        thread = threading.Thread(
            target=lambda: asyncio.run(
                run_mock_server(
                    endpoint,
                    update_interval_s=0.1,
                    duration_s=30.0,
                    stop_event=stop,
                    with_structs=False,
                )
            ),
            daemon=True,
        )
        thread.start()
        try:
            assert wait_for_endpoint(endpoint)
            from pssim.io.opcua_browser import browse_variables

            found = {node.node_id for node in browse_variables(endpoint)}
        finally:
            stop.set()
            thread.join(timeout=10.0)

        assert STATE_NODE not in found

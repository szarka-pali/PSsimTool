"""Browsing into a struct's fields and an array's elements.

The reported symptom: the address-space browse stopped at a variable, so a
structure was a dead end and no field inside it could be picked. It stopped there
by a decision written into `_describe` — `has_children=kind is NodeKind.OBJECT`.

What makes this awkward rather than obvious is that **a field is not a node**: a
server has one node for `Struct.AxisState` and nothing at all for `Position.X`.
The fields come from the node's `DataType`, the elements from its value, and both
carry the parent's node id plus a path.

Against `pssim mock-server` and nothing else. Run with ``uv run pytest -m integration``.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections.abc import Iterator

import pytest

from pssim.io._ready import wait_for_endpoint
from pssim.io.mock_server import run_mock_server
from pssim.io.opcua_browse_session import (
    OBJECTS_NODE_ID,
    BrowseNode,
    BrowseResult,
    OpcUaBrowseSession,
)

pytestmark = pytest.mark.integration

FIRST_PORT = 48480
_ports = itertools.count(FIRST_PORT)

NAMESPACE_INDEX = 2
STATE_NODE = f"ns={NAMESPACE_INDEX};s=Struct.AxisState"
POINT_NODE = f"ns={NAMESPACE_INDEX};s=Struct.Point"
ARRAY_NODE = f"ns={NAMESPACE_INDEX};s=Struct.Positions"
AXIS_NODE = f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos"


class BrowseMockServer:
    def __init__(self, duration_s: float = 120.0) -> None:
        self.endpoint = f"opc.tcp://127.0.0.1:{next(_ports)}/pssim-browse-structs/"
        self._duration_s = duration_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mock-browse", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                self.endpoint,
                update_interval_s=0.1,
                duration_s=self._duration_s,
                stop_event=self._stop,
            )
        )

    def __enter__(self) -> BrowseMockServer:
        self._thread.start()
        assert wait_for_endpoint(self.endpoint), f"no server on {self.endpoint}"
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)


@pytest.fixture(scope="module")
def session() -> Iterator[OpcUaBrowseSession]:
    """One server and one session for the module: every test here only reads."""
    with BrowseMockServer() as server, OpcUaBrowseSession(server.endpoint) as open_session:
        yield open_session


def row(result: BrowseResult, label: str) -> BrowseNode:
    found = [node for node in result.nodes if node.label == label]
    assert found, f"no row {label!r} in {[node.label for node in result.nodes]}"
    return found[0]


def rows(session: OpcUaBrowseSession, node_id: str, path: str = "") -> BrowseResult:
    return session.children_of(node_id, path=path)


class TestAStructOffersAnExpander:
    def test_a_struct_variable_can_be_opened(self, session: OpcUaBrowseSession) -> None:
        # The whole complaint: this was `False` and the browse ended here.
        folder = rows(session, OBJECTS_NODE_ID)
        struct_folder = rows(session, row(folder, "Struct").node_id)

        assert row(struct_folder, "Struct.AxisState").has_children is True

    def test_an_array_variable_can_be_opened(self, session: OpcUaBrowseSession) -> None:
        struct_folder = rows(session, row(rows(session, OBJECTS_NODE_ID), "Struct").node_id)

        assert row(struct_folder, "Struct.Positions").has_children is True

    def test_a_plain_scalar_still_cannot(self, session: OpcUaBrowseSession) -> None:
        # An expander on every leaf makes a tree of leaves look like a tree of
        # folders, which is why this was suppressed in the first place.
        axes = rows(session, row(rows(session, OBJECTS_NODE_ID), "Axes").node_id)

        assert row(axes, "Axes.X.ActPos").has_children is False

    def test_an_array_says_so_in_its_type(self, session: OpcUaBrowseSession) -> None:
        struct_folder = rows(session, row(rows(session, OBJECTS_NODE_ID), "Struct").node_id)

        assert row(struct_folder, "Struct.Positions").data_type == "Double[]"


class TestAStructIsNotBindable:
    """Both halves matter: openable, and not something to hand to a joint."""

    def test_a_struct_is_a_container(self, session: OpcUaBrowseSession) -> None:
        struct_folder = rows(session, row(rows(session, OBJECTS_NODE_ID), "Struct").node_id)

        assert row(struct_folder, "Struct.AxisState").is_container is True

    def test_and_so_is_not_numeric(self, session: OpcUaBrowseSession) -> None:
        struct_folder = rows(session, row(rows(session, OBJECTS_NODE_ID), "Struct").node_id)

        assert row(struct_folder, "Struct.AxisState").is_numeric is False

    def test_an_array_of_numbers_is_not_numeric_either(self, session: OpcUaBrowseSession) -> None:
        # `Double[4]` reports `Double`; binding it would hand a joint a list.
        struct_folder = rows(session, row(rows(session, OBJECTS_NODE_ID), "Struct").node_id)

        assert row(struct_folder, "Struct.Positions").is_numeric is False

    def test_a_plain_scalar_is(self, session: OpcUaBrowseSession) -> None:
        axes = rows(session, row(rows(session, OBJECTS_NODE_ID), "Axes").node_id)

        assert row(axes, "Axes.X.ActPos").is_numeric is True


class TestTheFieldsOfAStruct:
    def test_every_field_is_listed(self, session: OpcUaBrowseSession) -> None:
        fields = rows(session, STATE_NODE)

        assert [node.label for node in fields.nodes] == [
            "Position",
            "Enabled",
            "Name",
            "Limits",
        ]

    def test_a_field_carries_the_parent_s_node_id(self, session: OpcUaBrowseSession) -> None:
        # A field is not a node. There is nothing else it could carry.
        assert row(rows(session, STATE_NODE), "Enabled").node_id == STATE_NODE

    def test_a_field_carries_its_path(self, session: OpcUaBrowseSession) -> None:
        assert row(rows(session, STATE_NODE), "Enabled").path == "Enabled"

    def test_a_field_knows_it_is_a_field(self, session: OpcUaBrowseSession) -> None:
        assert row(rows(session, STATE_NODE), "Enabled").is_field is True

    def test_a_node_knows_it_is_not(self, session: OpcUaBrowseSession) -> None:
        axes = rows(session, row(rows(session, OBJECTS_NODE_ID), "Axes").node_id)

        assert row(axes, "Axes.X.ActPos").is_field is False

    def test_a_numeric_field_is_bindable(self, session: OpcUaBrowseSession) -> None:
        assert row(rows(session, STATE_NODE), "Enabled").is_numeric is True

    def test_a_string_field_is_not(self, session: OpcUaBrowseSession) -> None:
        # Refused rather than scaled.
        assert row(rows(session, STATE_NODE), "Name").is_numeric is False

    def test_a_nested_struct_field_is_a_container(self, session: OpcUaBrowseSession) -> None:
        assert row(rows(session, STATE_NODE), "Position").is_container is True

    def test_it_is_named_rather_than_numbered(self, session: OpcUaBrowseSession) -> None:
        # A Type column reading `ns=2;i=3` is no help in picking a field.
        assert row(rows(session, STATE_NODE), "Position").data_type == "PSsimPoint3D"

    def test_an_array_field_says_so(self, session: OpcUaBrowseSession) -> None:
        assert row(rows(session, STATE_NODE), "Limits").data_type == "Double[]"

    def test_a_field_is_never_writable_on_its_own(self, session: OpcUaBrowseSession) -> None:
        # A write goes back as the whole struct or not at all, and this project
        # writes one node at a time (R19).
        assert row(rows(session, STATE_NODE), "Enabled").is_writable is False


class TestNestedStructs:
    """The case a one-level-deep implementation gets wrong, and nobody notices
    until a real PLC."""

    def test_a_nested_struct_opens(self, session: OpcUaBrowseSession) -> None:
        inner = rows(session, STATE_NODE, path="Position")

        assert [node.label for node in inner.nodes] == ["X", "Y", "Z"]

    def test_the_path_accumulates(self, session: OpcUaBrowseSession) -> None:
        inner = rows(session, STATE_NODE, path="Position")

        assert row(inner, "X").path == "Position.X"

    def test_the_node_id_does_not_change(self, session: OpcUaBrowseSession) -> None:
        inner = rows(session, STATE_NODE, path="Position")

        assert row(inner, "X").node_id == STATE_NODE

    def test_a_leaf_of_a_nested_struct_is_bindable(self, session: OpcUaBrowseSession) -> None:
        # What the request was for: pick the one member matching the variable.
        inner = rows(session, STATE_NODE, path="Position")

        assert row(inner, "X").is_numeric is True

    def test_a_struct_standing_on_its_own_opens_too(self, session: OpcUaBrowseSession) -> None:
        assert [node.label for node in rows(session, POINT_NODE).nodes] == ["X", "Y", "Z"]


class TestArrayElements:
    def test_an_array_field_lists_its_elements(self, session: OpcUaBrowseSession) -> None:
        elements = rows(session, STATE_NODE, path="Limits")

        assert [node.label for node in elements.nodes] == ["[0]", "[1]"]

    def test_an_element_carries_a_subscript_path(self, session: OpcUaBrowseSession) -> None:
        elements = rows(session, STATE_NODE, path="Limits")

        assert row(elements, "[1]").path == "Limits[1]"

    def test_an_element_is_bindable(self, session: OpcUaBrowseSession) -> None:
        elements = rows(session, STATE_NODE, path="Limits")

        assert row(elements, "[1]").is_numeric is True

    def test_a_bare_array_lists_its_elements(self, session: OpcUaBrowseSession) -> None:
        elements = rows(session, ARRAY_NODE)

        assert len(elements.nodes) == 3

    def test_its_path_has_no_field_name(self, session: OpcUaBrowseSession) -> None:
        # The node is itself the array; there is no field to lead with.
        assert row(rows(session, ARRAY_NODE), "[2]").path == "[2]"

    def test_an_element_reports_the_element_type(self, session: OpcUaBrowseSession) -> None:
        # `Double`, not `Double[]` — one element is not an array.
        assert row(rows(session, ARRAY_NODE), "[0]").data_type == "Double"


class TestWhatCannotBeOpened:
    def test_a_scalar_asked_anyway_answers_nothing(self, session: OpcUaBrowseSession) -> None:
        # The tree will not ask, but a stale expander or a hand-typed path might.
        assert len(rows(session, AXIS_NODE).nodes) == 0

    def test_a_path_that_is_not_there_answers_nothing(self, session: OpcUaBrowseSession) -> None:
        # Empty rather than an exception: a browse must not fail the dialog over
        # a field the server has since renamed.
        assert len(rows(session, STATE_NODE, path="Nonexistent").nodes) == 0

    def test_a_leaf_field_answers_nothing(self, session: OpcUaBrowseSession) -> None:
        assert len(rows(session, STATE_NODE, path="Position.X").nodes) == 0

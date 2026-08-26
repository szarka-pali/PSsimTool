"""Picking a struct field or an array element in the tag chooser.

No server: the tree has a public entry point for "here is what came back", so
what is exercised is what the dialog does with an answer — which is where the
decisions are.

The one that was easy to get wrong: a struct row must stay **openable** while not
being selectable as a tag. Dimmed, not disabled — a disabled row's expander is a
Qt behaviour this feature would then depend on, and the struct row is the one
thing here that has to be opened.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.io.opcua_browse_session import BrowseNode, BrowseResult, NodeKind  # noqa: E402
from pssim.ui.opcua_browse_tree import (  # noqa: E402
    COLUMN_NAME,
    COLUMN_NODE_ID,
    COLUMN_TYPE,
    OpcUaBrowseTree,
)
from pssim.ui.opcua_dialog import AssignTagDialog  # noqa: E402
from pssim.ui.settings import ConnectionSettings, VariableTag  # noqa: E402

pytestmark = pytest.mark.ui

STATE_NODE = "ns=2;s=Struct.AxisState"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def struct(node_id: str = STATE_NODE, name: str = "Struct.AxisState") -> BrowseNode:
    return BrowseNode(
        node_id=node_id,
        browse_name=name,
        display_name=name,
        kind=NodeKind.VARIABLE,
        data_type="ExtensionObject",
        has_children=True,
        is_container=True,
    )


def array(node_id: str = "ns=2;s=Struct.Positions") -> BrowseNode:
    return BrowseNode(
        node_id=node_id,
        browse_name="Struct.Positions",
        display_name="Struct.Positions",
        kind=NodeKind.VARIABLE,
        data_type="Double[]",
        has_children=True,
        is_container=True,
    )


def field(
    name: str = "X",
    path: str = "Position.X",
    data_type: str = "Double",
    has_children: bool = False,
    is_container: bool = False,
) -> BrowseNode:
    return BrowseNode(
        node_id=STATE_NODE,
        browse_name=name,
        display_name=name,
        kind=NodeKind.VARIABLE,
        data_type=data_type,
        has_children=has_children,
        path=path,
        is_container=is_container,
    )


def scalar(node_id: str = "ns=2;s=Axes.X.ActPos") -> BrowseNode:
    return BrowseNode(
        node_id=node_id,
        browse_name="Axes.X.ActPos",
        display_name="Axes.X.ActPos",
        kind=NodeKind.VARIABLE,
        data_type="Double",
    )


@pytest.fixture
def tree(qt_app: QApplication) -> OpcUaBrowseTree:
    return OpcUaBrowseTree()


@pytest.fixture
def chooser(qt_app: QApplication) -> AssignTagDialog:
    return AssignTagDialog("tilt", "opc.tcp://plc:4840/")


class TestAContainerStaysOpenable:
    def test_a_struct_gets_an_expander(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(struct(),)))

        assert tree.topLevelItem(0).childCount() == 1

    def test_and_is_not_disabled(self, tree: OpcUaBrowseTree) -> None:
        # Dimmed instead. A disabled row's expander is a Qt behaviour this
        # feature would then rest on, and this is the row that must be opened.
        tree._on_children("i=85", None, BrowseResult(nodes=(struct(),)))

        assert tree.topLevelItem(0).isDisabled() is False

    def test_but_it_is_dimmed(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(struct(), scalar())))

        dimmed = tree.topLevelItem(0).foreground(COLUMN_NAME).color()
        plain = tree.topLevelItem(1).foreground(COLUMN_NAME).color()
        assert dimmed != plain

    def test_an_array_too(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(array(),)))

        assert tree.topLevelItem(0).isDisabled() is False
        assert tree.topLevelItem(0).childCount() == 1

    def test_a_plain_scalar_is_neither(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(scalar(),)))

        assert tree.topLevelItem(0).childCount() == 0
        assert tree.topLevelItem(0).isDisabled() is False


class TestWhatAFieldRowShows:
    def test_the_name_is_the_field_s(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children(STATE_NODE, None, BrowseResult(nodes=(field(),)))

        assert tree.topLevelItem(0).text(COLUMN_NAME) == "X"

    def test_the_node_id_is_the_node_holding_it(self, tree: OpcUaBrowseTree) -> None:
        # A field is not a node. There is nothing else it could show.
        tree._on_children(STATE_NODE, None, BrowseResult(nodes=(field(),)))

        assert tree.topLevelItem(0).text(COLUMN_NODE_ID) == STATE_NODE

    def test_the_type_is_the_field_s(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children(STATE_NODE, None, BrowseResult(nodes=(field(),)))

        assert tree.topLevelItem(0).text(COLUMN_TYPE) == "Double"

    def test_the_path_is_in_the_tooltip(self, tree: OpcUaBrowseTree) -> None:
        # Every row down a struct's subtree shows the same node id, so the path
        # is what says which place this row actually is.
        tree._on_children(STATE_NODE, None, BrowseResult(nodes=(field(),)))

        assert "Position.X" in tree.topLevelItem(0).toolTip(COLUMN_NAME)

    def test_a_node_row_s_tooltip_has_no_arrow(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(scalar(),)))

        assert "→" not in tree.topLevelItem(0).toolTip(COLUMN_NAME)

    def test_the_selected_field_reads_back_with_its_path(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children(STATE_NODE, None, BrowseResult(nodes=(field(),)))

        tree.setCurrentItem(tree.topLevelItem(0))

        selected = tree.selected_node
        assert selected is not None
        assert selected.path == "Position.X"


class TestTheChooserTakesBothHalves:
    def test_picking_a_field_fills_the_node_id(self, chooser: AssignTagDialog) -> None:
        chooser._on_node_selected(field())

        assert chooser.node_edit.text() == STATE_NODE

    def test_and_the_path(self, chooser: AssignTagDialog) -> None:
        chooser._on_node_selected(field())

        assert chooser.path_edit.text() == "Position.X"

    def test_the_tag_carries_the_path(self, chooser: AssignTagDialog) -> None:
        chooser._on_node_selected(field())

        tag = chooser.tag
        assert tag is not None
        assert tag.path == "Position.X"

    def test_an_array_element_too(self, chooser: AssignTagDialog) -> None:
        chooser._on_node_selected(field(name="[1]", path="Limits[1]"))

        tag = chooser.tag
        assert tag is not None
        assert tag.path == "Limits[1]"

    def test_picking_a_plain_node_clears_a_stale_path(self, chooser: AssignTagDialog) -> None:
        # Otherwise the tag keeps pointing inside a value the new node has not
        # got, and the signal silently stops arriving.
        chooser._on_node_selected(field())

        chooser._on_node_selected(scalar())

        assert chooser.path_edit.text() == ""

    def test_a_struct_is_not_picked(self, chooser: AssignTagDialog) -> None:
        # Selecting it is navigation: it is the row you open to reach the field.
        chooser.node_edit.setText("ns=2;s=Kept")

        chooser._on_node_selected(struct())

        assert chooser.node_edit.text() == "ns=2;s=Kept"

    def test_an_array_is_not_picked_either(self, chooser: AssignTagDialog) -> None:
        chooser.node_edit.setText("ns=2;s=Kept")

        chooser._on_node_selected(array())

        assert chooser.node_edit.text() == "ns=2;s=Kept"

    def test_a_nested_struct_field_is_not_picked(self, chooser: AssignTagDialog) -> None:
        chooser.node_edit.setText("ns=2;s=Kept")

        chooser._on_node_selected(field("Position", "Position", "PSsimPoint3D", True, True))

        assert chooser.node_edit.text() == "ns=2;s=Kept"

    def test_an_existing_path_is_shown(self, qt_app: QApplication) -> None:
        current = VariableTag(node_id=STATE_NODE, path="Position.Z")

        dialog = AssignTagDialog("tilt", "opc.tcp://plc:4840/", current)

        assert dialog.path_edit.text() == "Position.Z"

    def test_a_typed_path_reaches_the_tag(self, chooser: AssignTagDialog) -> None:
        # The field stays editable: a server that cannot be reached right now
        # should not stop the assignment.
        chooser.node_edit.setText(STATE_NODE)
        chooser.path_edit.setText("Position.Y")

        tag = chooser.tag
        assert tag is not None
        assert tag.path == "Position.Y"

    def test_no_path_is_no_path(self, chooser: AssignTagDialog) -> None:
        chooser.node_edit.setText("ns=2;s=Axes.X.ActPos")

        tag = chooser.tag
        assert tag is not None
        assert tag.path == ""


class TestTheTagIsStored:
    def test_a_path_round_trips(self) -> None:
        tag = VariableTag(node_id=STATE_NODE, path="Position.X", decimals=1)

        assert VariableTag.from_dict(tag.to_dict()) == tag

    def test_no_path_is_not_written(self) -> None:
        # A settings file from a scene with no structures looks exactly as it did
        # before paths existed.
        assert "path" not in VariableTag(node_id="ns=2;s=X").to_dict()

    def test_a_tag_stored_before_paths_existed_still_loads(self) -> None:
        restored = VariableTag.from_dict({"node_id": "ns=2;s=X", "scale": 1.0})

        assert restored == VariableTag(node_id="ns=2;s=X")

    def test_an_unusable_path_is_dropped_not_raised(self) -> None:
        # A settings file is outside data (R18), and a malformed path would
        # otherwise be reported once per notification on the source's thread.
        restored = VariableTag.from_dict({"node_id": "ns=2;s=X", "path": "Position..X"})

        assert restored is not None
        assert restored.path == ""

    def test_a_usable_path_survives(self) -> None:
        restored = VariableTag.from_dict({"node_id": "ns=2;s=X", "path": "Limits[1]"})

        assert restored is not None
        assert restored.path == "Limits[1]"

    def test_it_reaches_the_binding(self) -> None:
        from pssim.config.binding import BindingDirection
        from pssim.ui.variable_registry import VariableEntry

        entry = VariableEntry(
            name="tilt",
            direction=BindingDirection.READ,
            owner="axis tilt",
            tag=VariableTag(node_id=STATE_NODE, path="Position.X"),
        )

        binding = entry.binding()
        assert binding is not None
        assert binding.path == "Position.X"

    def test_the_whole_connection_round_trips_with_one(self) -> None:
        settings = ConnectionSettings().with_tag(
            "tilt", VariableTag(node_id=STATE_NODE, path="Position.X")
        )

        assert ConnectionSettings.from_dict(settings.to_dict()) == settings

"""The tree fills from what was read before, and Refresh is explicit.

No server: the tree's `_on_children` is the entry point a worker's answer arrives
through, so a test can supply one and then check that the *next* look at the same
place asks nothing.

That "asks nothing" is the whole claim, and the way to pin it is a session stub
that counts. A tree with no session cannot request anything, so a cache hit and a
missing session look identical from outside — the stub is what tells them apart.

The asymmetry in these tests is the point rather than an accident: a **cache hit
fills the tree with no event loop at all**, so those tests assert straight after
the call. A miss goes to a worker thread, so those have to pump until the answer
arrives. If a cache hit ever needed pumping, it would mean it had stopped being
a cache hit.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.io.opcua_browse_session import (  # noqa: E402
    OBJECTS_NODE_ID,
    BrowseNode,
    BrowseResult,
    NodeKind,
)
from pssim.ui.browse_cache import BrowseCache  # noqa: E402
from pssim.ui.opcua_browse_tree import COLUMN_NAME, OpcUaBrowseTree  # noqa: E402

pytestmark = pytest.mark.ui

FOLDER = "ns=2;i=1"
STRUCT = "ns=2;s=Struct.AxisState"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


class _CountingSession:
    """A session that answers instantly and counts what it was asked.

    Not an `OpcUaBrowseSession`: the tree only ever calls `children_of` on it,
    and a real one would need a server. What is being tested is whether the tree
    asks at all.
    """

    def __init__(self, answers: dict[tuple[str, str], BrowseResult]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str]] = []

    def children_of(self, node_id: str, path: str = "", **_: object) -> BrowseResult:
        self.asked.append((node_id, path))
        return self.answers.get((node_id, path), BrowseResult())


def until(app: QApplication, predicate: object, timeout_s: float = 5.0) -> bool:
    """Pump the event loop until something is true.

    A worker thread answers by signal, and the signal is delivered by the event
    loop — so nothing arrives in a test that never runs one. Pumped rather than
    slept on: `.claude/rules/testing.md` rules out `sleep()` for
    synchronisation, and this returns the moment the condition holds.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():  # type: ignore[operator]
            return True
    return False


def folder(node_id: str = FOLDER, name: str = "Axes") -> BrowseNode:
    return BrowseNode(
        node_id=node_id,
        browse_name=name,
        display_name=name,
        kind=NodeKind.OBJECT,
        has_children=True,
    )


def variable(name: str = "X", node_id: str = "ns=2;s=X") -> BrowseNode:
    return BrowseNode(
        node_id=node_id,
        browse_name=name,
        display_name=name,
        kind=NodeKind.VARIABLE,
        data_type="Double",
    )


@pytest.fixture
def cache() -> BrowseCache:
    return BrowseCache()


@pytest.fixture
def tree(qt_app: QApplication, cache: BrowseCache) -> OpcUaBrowseTree:
    built = OpcUaBrowseTree()
    built.use_cache(cache)
    return built


class TestAnAnswerIsRemembered:
    def test_it_reaches_the_cache(self, tree: OpcUaBrowseTree, cache: BrowseCache) -> None:
        tree._on_children(OBJECTS_NODE_ID, None, BrowseResult(nodes=(folder(),)))

        assert cache.get(OBJECTS_NODE_ID) is not None

    def test_a_folder_s_own_answer_too(self, tree: OpcUaBrowseTree, cache: BrowseCache) -> None:
        tree._on_children(OBJECTS_NODE_ID, None, BrowseResult(nodes=(folder(),)))
        parent = tree.topLevelItem(0)

        tree._on_children(FOLDER, parent, BrowseResult(nodes=(variable(),)))

        assert cache.get(FOLDER) is not None

    def test_a_field_is_filed_under_its_path(
        self, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        # A struct's every field shares one node id (R21), so the path is what
        # distinguishes the places.
        struct = BrowseNode(
            node_id=STRUCT,
            browse_name="AxisState",
            display_name="AxisState",
            kind=NodeKind.VARIABLE,
            data_type="ExtensionObject",
            has_children=True,
            is_container=True,
        )
        tree._on_children(OBJECTS_NODE_ID, None, BrowseResult(nodes=(struct,)))
        parent = tree.topLevelItem(0)
        field = BrowseNode(
            node_id=STRUCT,
            browse_name="Position",
            display_name="Position",
            kind=NodeKind.VARIABLE,
            data_type="Double",
            path="Position",
        )

        tree._on_children(STRUCT, parent, BrowseResult(nodes=(field,)))

        assert cache.get(STRUCT, "") is not None


class TestItIsNotAskedTwice:
    def test_a_cached_root_costs_no_request(
        self, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        session = _CountingSession({})

        tree.set_session(session)  # type: ignore[arg-type]

        assert session.asked == []

    def test_and_it_is_shown(self, tree: OpcUaBrowseTree, cache: BrowseCache) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))

        tree.set_session(_CountingSession({}))  # type: ignore[arg-type]

        assert tree.topLevelItemCount() == 1

    def test_an_uncached_root_is_asked_for(
        self, qt_app: QApplication, tree: OpcUaBrowseTree
    ) -> None:
        session = _CountingSession({(OBJECTS_NODE_ID, ""): BrowseResult(nodes=(folder(),))})

        tree.set_session(session)  # type: ignore[arg-type]

        assert until(qt_app, lambda: session.asked == [(OBJECTS_NODE_ID, "")])

    def test_a_cached_folder_opens_without_asking(
        self, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        # The point of the whole thing: reopening the dialog puts the tree back
        # where it was rather than a request per level.
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        cache.put(FOLDER, "", BrowseResult(nodes=(variable(),)))
        session = _CountingSession({})

        tree.set_session(session)  # type: ignore[arg-type]

        assert session.asked == []
        assert tree.topLevelItem(0).childCount() == 1

    def test_a_cached_folder_is_expanded_again(
        self, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        cache.put(FOLDER, "", BrowseResult(nodes=(variable(),)))

        tree.set_session(_CountingSession({}))  # type: ignore[arg-type]

        assert tree.topLevelItem(0).isExpanded() is True

    def test_an_unvisited_folder_stays_shut(
        self, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))

        tree.set_session(_CountingSession({}))  # type: ignore[arg-type]

        assert tree.topLevelItem(0).isExpanded() is False


class TestRefreshingOneNode:
    def test_it_forgets_that_node(self, tree: OpcUaBrowseTree, cache: BrowseCache) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        cache.put(FOLDER, "", BrowseResult(nodes=(variable(),)))
        tree.set_session(_CountingSession({}))  # type: ignore[arg-type]

        tree.refresh_node(folder())

        assert cache.get(FOLDER) is None

    def test_and_asks_the_server_again(
        self, qt_app: QApplication, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        cache.put(FOLDER, "", BrowseResult(nodes=(variable(),)))
        session = _CountingSession({(FOLDER, ""): BrowseResult(nodes=(variable("Y"),))})
        tree.set_session(session)  # type: ignore[arg-type]

        tree.refresh_node(folder())

        assert until(qt_app, lambda: (FOLDER, "") in session.asked)

    def test_it_keeps_the_rest(self, tree: OpcUaBrowseTree, cache: BrowseCache) -> None:
        # "This node" rather than "everything": the root's answer is still good.
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        cache.put(FOLDER, "", BrowseResult(nodes=(variable(),)))
        tree.set_session(_CountingSession({}))  # type: ignore[arg-type]

        tree.refresh_node(folder())

        assert cache.get(OBJECTS_NODE_ID) is not None

    def test_the_new_answer_is_shown(
        self, qt_app: QApplication, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        cache.put(FOLDER, "", BrowseResult(nodes=(variable("X"),)))
        session = _CountingSession(
            {(FOLDER, ""): BrowseResult(nodes=(variable("Y"), variable("Z", "ns=2;s=Z")))}
        )
        tree.set_session(session)  # type: ignore[arg-type]

        tree.refresh_node(folder())

        assert until(qt_app, lambda: tree.topLevelItem(0).childCount() == 2)

    def test_with_nothing_selected_it_refreshes_everything(
        self, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        # There is no node to be specific about, and doing nothing at all would
        # look like a broken button.
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        tree.set_session(_CountingSession({}))  # type: ignore[arg-type]

        tree.refresh_node()

        assert len(cache) == 0


class TestRefreshingEverything:
    def test_the_cache_is_emptied(self, tree: OpcUaBrowseTree, cache: BrowseCache) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        cache.put(FOLDER, "", BrowseResult(nodes=(variable(),)))
        tree.set_session(_CountingSession({}))  # type: ignore[arg-type]

        tree.refresh_all()

        assert len(cache) == 0

    def test_the_server_is_walked_again_from_the_top(
        self, qt_app: QApplication, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        session = _CountingSession({(OBJECTS_NODE_ID, ""): BrowseResult(nodes=(folder(),))})
        tree.set_session(session)  # type: ignore[arg-type]
        session.asked.clear()

        tree.refresh_all()

        assert until(qt_app, lambda: session.asked == [(OBJECTS_NODE_ID, "")])

    def test_a_node_added_on_the_plc_then_appears(
        self, qt_app: QApplication, tree: OpcUaBrowseTree, cache: BrowseCache
    ) -> None:
        # Which is what this button is for: an answer given earlier cannot hold
        # a tag created since.
        cache.put(OBJECTS_NODE_ID, "", BrowseResult(nodes=(folder(),)))
        session = _CountingSession(
            {(OBJECTS_NODE_ID, ""): BrowseResult(nodes=(folder(), folder("ns=2;i=9", "NewDevice")))}
        )
        tree.set_session(session)  # type: ignore[arg-type]

        tree.refresh_all()

        def labels() -> list[str]:
            return [
                tree.topLevelItem(index).text(COLUMN_NAME)
                for index in range(tree.topLevelItemCount())
            ]

        assert until(qt_app, lambda: "NewDevice" in labels())


class TestWithoutACache:
    """The tree has to work with none — a test builds one bare, and so does any
    caller that has not been given one."""

    def test_it_still_asks_and_shows(self, qt_app: QApplication) -> None:
        bare = OpcUaBrowseTree()
        session = _CountingSession({(OBJECTS_NODE_ID, ""): BrowseResult(nodes=(folder(),))})

        bare.set_session(session)  # type: ignore[arg-type]

        assert until(qt_app, lambda: bare.topLevelItemCount() == 1)

    def test_refreshing_everything_is_quiet(self, qt_app: QApplication) -> None:
        bare = OpcUaBrowseTree()
        bare.set_session(_CountingSession({}))  # type: ignore[arg-type]

        bare.refresh_all()

        assert bare.topLevelItemCount() == 0

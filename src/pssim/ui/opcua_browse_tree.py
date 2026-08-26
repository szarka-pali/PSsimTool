"""The server's address space as a tree, read a folder at a time.

Every expansion is one question to the server, asked on a worker thread and
answered by signal. Nothing is read until a folder is opened, which is what makes
this usable against a PLC holding thousands of nodes — and what
`io/opcua_browser.browse_variables` cannot do, because it reads everything.

The widget owns no connection. It renders whatever `OpcUaBrowseSession` it is
given and reports what was picked; the dialog decides what that means. Same
arrangement as the other trees here (R6).
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from pssim.io.opcua_browse_session import (
    OBJECTS_NODE_ID,
    BrowseNode,
    BrowseResult,
    NodeKind,
    OpcUaBrowseSession,
)
from pssim.observability import get_logger
from pssim.ui.browse_cache import BrowseCache

logger = get_logger(__name__)

COLUMN_NAME: Final = 0
COLUMN_NODE_ID: Final = 1
COLUMN_TYPE: Final = 2
COLUMN_ACCESS: Final = 3

#: Item data: the node this row stands for, and whether its children have been
#: fetched. Qt wants ints above `UserRole`; nothing is read back from the text.
NODE_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1
LOADED_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 2

#: The row a folder shows while its contents are on their way. Replaced by the
#: answer; without it the expander would not appear at all, and a folder with no
#: visible arrow reads as an empty one.
PLACEHOLDER_TEXT: Final = "…"


class OpcUaBrowseTree(QTreeWidget):
    """The address space. Lazily expanded, never blocking the thread that draws."""

    node_selected = Signal(object)
    """Carries the selected `BrowseNode`, or `None`."""

    node_activated = Signal(object)
    """A double-click — carries the `BrowseNode`. The quickest way to say
    "that one", which for a variable is what a chooser is open for."""

    failed = Signal(str)
    """One expansion could not be read. Reported rather than raised: a folder a
    server will not open is a fact about that folder, not a reason to close the
    dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._session: OpcUaBrowseSession | None = None
        self._cache: BrowseCache | None = None
        # Keyed by node **and** path: a struct's fields all share one node id,
        # so keying by node alone would drop the second field expanded and
        # fill the wrong row with the first one's answer.
        self._workers: dict[tuple[str, str], _ChildrenThread] = {}
        self._numeric_only = False

        self.setHeaderLabels(
            [self.tr("Name"), self.tr("Node id"), self.tr("Type"), self.tr("Access")]
        )
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        self.setMinimumSize(560, 260)

        header = self.header()
        header.setStretchLastSection(False)
        for column in range(self.columnCount()):
            header.setSectionResizeMode(column, header.ResizeMode.Interactive)
        for column, width in enumerate((230, 210, 80, 70)):
            self.setColumnWidth(column, width)

        self.itemExpanded.connect(self._on_expanded)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemDoubleClicked.connect(self._on_double_clicked)

    def require_numeric(self) -> None:
        """Grey out every variable that cannot drive a joint.

        For the tag chooser, not for the address-space viewer: in the connection
        dialog the tree only shows what the server holds, and nothing there is
        being picked, so a `String` node greyed out would be a judgement on a
        node nobody asked about.
        """
        self._numeric_only = True
        self.refresh()

    def use_cache(self, cache: BrowseCache | None) -> None:
        """Read from, and write to, answers the server already gave.

        Handed in rather than owned: the window keeps it, so it outlives this
        dialog and reopening one shows the tree as it was.
        """
        self._cache = cache

    # -- the session --------------------------------------------------------

    @property
    def session(self) -> OpcUaBrowseSession | None:
        return self._session

    def set_session(self, session: OpcUaBrowseSession | None) -> None:
        """Show a different server, or nothing.

        Any expansion still in flight is abandoned first: its answer describes a
        server this tree is no longer looking at.
        """
        self._abandon_workers()
        self._session = session
        self.clear()
        if session is not None:
            self._request(OBJECTS_NODE_ID, parent=None)

    def refresh(self) -> None:
        """Show the tree again, from whatever is cached. Asks the server only for
        what it has no answer to."""
        self.set_session(self._session)

    def refresh_all(self) -> None:
        """Forget the server and walk it again from `Objects`.

        What to reach for when the address space itself has changed - a tag added
        on the PLC this morning is not in an answer given yesterday.
        """
        if self._cache is not None:
            self._cache.clear()
        self.set_session(self._session)

    def refresh_node(self, node: BrowseNode | None = None) -> None:
        """Read one place again, keeping the rest.

        The selected row unless told otherwise. Its own children are re-read;
        anything deeper stays cached until it is refreshed in turn, which is what
        "refresh this node" means as opposed to "refresh everything".
        """
        target = node or self.selected_node
        if target is None:
            self.refresh_all()
            return
        if self._cache is not None:
            self._cache.forget(target.node_id, target.path)

        item = self._item_for(target)
        if item is None:
            return
        item.takeChildren()
        item.setData(COLUMN_NAME, LOADED_ROLE, False)
        item.addChild(QTreeWidgetItem([PLACEHOLDER_TEXT, "", "", ""]))
        self._request(target.node_id, parent=item, path=target.path)

    def _item_for(self, node: BrowseNode) -> QTreeWidgetItem | None:
        """The row standing for one place, wherever it sits.

        Searched rather than remembered: a row is thrown away and rebuilt on
        every fill, so a held reference would be to an item Qt has deleted.
        """
        for item in self.findItems("", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive):
            held = item.data(COLUMN_NAME, NODE_ROLE)
            if (
                isinstance(held, BrowseNode)
                and held.node_id == node.node_id
                and held.path == node.path
            ):
                return item
        return None

    # -- reading ------------------------------------------------------------

    @property
    def selected_node(self) -> BrowseNode | None:
        items = self.selectedItems()
        if not items:
            return None
        node = items[0].data(COLUMN_NAME, NODE_ROLE)
        return node if isinstance(node, BrowseNode) else None

    # -- expansion ----------------------------------------------------------

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        """A folder was opened: ask the server what is in it, once."""
        if item.data(COLUMN_NAME, LOADED_ROLE):
            return
        node = item.data(COLUMN_NAME, NODE_ROLE)
        if isinstance(node, BrowseNode):
            self._request(node.node_id, parent=item, path=node.path)

    def _request(self, node_id: str, parent: QTreeWidgetItem | None, path: str = "") -> None:
        """Fill one place from the cache, or ask the server for it.

        A second request for the same place while the first is in flight is
        dropped — double-clicking an expander should not ask twice.
        """
        session = self._session
        key = (node_id, path)
        if session is None or key in self._workers:
            return

        if self._cache is not None:
            remembered = self._cache.get(node_id, path)
            if remembered is not None:
                # No thread and no request: the answer is already here, so the
                # folder opens at once and offline.
                self._fill(parent, remembered)
                return

        worker = _ChildrenThread(session, node_id, parent, path, self)
        worker.succeeded.connect(self._on_children)
        worker.failed.connect(self._on_failure)
        worker.finished.connect(lambda: self._workers.pop(key, None))
        self._workers[key] = worker
        worker.start()

    def _on_children(self, node_id: str, parent: object, result: object) -> None:
        """Fill in one folder, and remember what it held.

        Which item to fill is the `parent` the worker was started with; the node
        id is what the answer gets filed under.
        """
        if not isinstance(result, BrowseResult):
            return
        holder = parent if isinstance(parent, QTreeWidgetItem) else None
        if self._cache is not None:
            self._cache.put(node_id, _path_of(holder), result)
        self._fill(holder, result)

    def _on_failure(self, node_id: str, message: str) -> None:
        logger.warning("could not browse a node", node=node_id, error=message)
        self.failed.emit(message)

    def _fill(self, parent: QTreeWidgetItem | None, result: BrowseResult) -> None:
        """Replace a folder's placeholder with what the server said is in it."""
        if parent is None:
            self.clear()
        else:
            parent.takeChildren()
            parent.setData(COLUMN_NAME, LOADED_ROLE, True)

        for node in result.nodes:
            item = _make_item(node, self._numeric_only)
            if parent is None:
                self.addTopLevelItem(item)
            else:
                parent.addChild(item)

        # Anything already known about a child opens with it, so reopening the
        # dialog puts the tree back where it was rather than at the root.
        if self._cache is not None:
            for index in range(self.topLevelItemCount() if parent is None else parent.childCount()):
                child = self.topLevelItem(index) if parent is None else parent.child(index)
                held = child.data(COLUMN_NAME, NODE_ROLE) if child is not None else None
                if (
                    child is not None
                    and isinstance(held, BrowseNode)
                    and self._cache.get(held.node_id, held.path) is not None
                ):
                    child.setExpanded(True)

        if result.is_truncated:
            # Said rather than silently shown: a folder cut off at the limit that
            # looked complete would be a lie about the server.
            truncated = QTreeWidgetItem([self.tr("… more, not shown"), "", "", ""])
            truncated.setDisabled(True)
            if parent is None:
                self.addTopLevelItem(truncated)
            else:
                parent.addChild(truncated)

    def _abandon_workers(self) -> None:
        """Let go of every expansion in flight.

        Not joined: a worker is blocked on a server that may be gone, and waiting
        for it is what would freeze the window. Its signals are disconnected, so
        a late answer reaches nothing.
        """
        for worker in self._workers.values():
            worker.abandon()
        self._workers.clear()

    # -- events -------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        self.node_selected.emit(self.selected_node)

    def _on_double_clicked(self, _item: QTreeWidgetItem, _column: int) -> None:
        node = self.selected_node
        if node is not None:
            self.node_activated.emit(node)


class _ChildrenThread(QThread):
    """One question to the server, off the thread that draws.

    `children_of` blocks until the server answers. On a LAN that is milliseconds
    and on a bad link it is not, and a window that stops repainting meanwhile
    looks broken.
    """

    succeeded = Signal(str, object, object)
    failed = Signal(str, str)

    def __init__(
        self,
        session: OpcUaBrowseSession,
        node_id: str,
        parent_item: QTreeWidgetItem | None,
        path: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._node_id = node_id
        self._parent_item = parent_item
        self._path = path

    def abandon(self) -> None:
        """Stop anyone hearing the answer. The thread itself finishes on its own
        once the server replies or the request times out."""
        self.succeeded.disconnect()
        self.failed.disconnect()

    def run(self) -> None:
        """Never raises out of the thread: that would take the window with it."""
        try:
            result = self._session.children_of(self._node_id, path=self._path)
        except Exception as exc:
            self.failed.emit(self._node_id, str(exc))
            return
        self.succeeded.emit(self._node_id, self._parent_item, result)


def _make_item(node: BrowseNode, numeric_only: bool) -> QTreeWidgetItem:
    """One row. The node travels in item data, never read back from the text."""
    item = QTreeWidgetItem(
        [
            node.label,
            node.node_id,
            node.data_type,
            _access_text(node),
        ]
    )
    item.setData(COLUMN_NAME, NODE_ROLE, node)
    item.setData(COLUMN_NAME, LOADED_ROLE, False)
    item.setToolTip(COLUMN_NAME, _tooltip(node))

    if node.has_children:
        # A placeholder, so the expander exists before anything is known about
        # what is inside. Without it a folder reads as empty until it is opened,
        # which is the one thing it cannot be.
        item.addChild(QTreeWidgetItem([PLACEHOLDER_TEXT, "", "", ""]))
        if node.is_container:
            # **Dimmed, not disabled.** A struct or an array is the one thing
            # here that must stay openable — it is where the field somebody
            # wants lives — while still reading as something that cannot itself
            # be bound. Disabling it would rest on Qt letting a click reach the
            # expander of a disabled row, which is not a thing to build the
            # central interaction of a feature on. The same choice R15 made for
            # a hidden model's row, for the same reason.
            _dim(item)
    elif node.kind is NodeKind.OTHER:
        # A method or a type: shown so the tree matches the server, greyed
        # because there is nothing this application can do with it.
        item.setDisabled(True)
    elif numeric_only and not node.is_numeric:
        # Greyed rather than hidden: it is obvious then that the tag was found
        # and rejected, not that it is missing.
        item.setDisabled(True)
    return item


def _path_of(item: QTreeWidgetItem | None) -> str:
    """The path of the place a row stands for, or `""` for the root."""
    if item is None:
        return ""
    held = item.data(COLUMN_NAME, NODE_ROLE)
    return held.path if isinstance(held, BrowseNode) else ""


def _tooltip(node: BrowseNode) -> str:
    """The browse name, and for a field where inside the value it is.

    A field's Node id column shows the node holding it — correct, and identical
    all the way down a struct's subtree, so the path is what says which place
    this row actually is.
    """
    if node.path:
        return f"{node.browse_name}\n{node.node_id} → {node.path}"
    return node.browse_name


def _dim(item: QTreeWidgetItem) -> None:
    """Paint a row in the palette's disabled colour without disabling it.

    From the palette rather than a fixed grey: a hardcoded one cannot be legible
    on a light theme and a dark one both (R17).
    """
    color = QApplication.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    for column in range(item.columnCount()):
        item.setForeground(column, color)


def _access_text(node: BrowseNode) -> str:
    """`R` or `RW` for a variable, nothing for anything else."""
    if not node.is_variable:
        return ""
    return "RW" if node.is_writable else "R"

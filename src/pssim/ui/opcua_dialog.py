"""Two dialogs: where the server is, and which node a variable reads from.

Kept apart because they answer different questions and are opened at different
moments. The connection is set once and rarely revisited; a tag is assigned once
per variable, and while doing that the only thing worth seeing is the server's
own list of nodes.

Browsing runs in a worker thread. `io.opcua_browser.browse_variables` blocks
until the server answers, and a window that stops repainting while it does looks
broken — the same reason a STEP import has `ui/loader.StepImportThread`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pssim.io.opcua_browser import OpcUaNode, browse_variables
from pssim.observability import get_logger
from pssim.ui.settings import ConnectionSettings, VariableTag

logger = get_logger(__name__)

#: The publishing interval a server may be asked for. Below ~10 ms no PLC keeps
#: up and the subscription only queues; above a second the motion is a slideshow.
MIN_INTERVAL_MS: Final = 10
MAX_INTERVAL_MS: Final = 5_000

#: A scale of exactly zero has no inverse and cannot be written back
#: (`config.binding.to_plc`), so the spin box does not offer it.
SCALE_LIMIT: Final = 1_000_000.0
SCALE_DECIMALS: Final = 6

OFFSET_LIMIT: Final = 1_000_000.0

_BROWSE_COLUMNS: Final = 4


class ConnectionDialog(QDialog):
    """Where the server is, how often it should publish, and whether this
    application may write to it.

    The writing switch is here rather than buried in a preferences tree because
    it is the one setting in this window with consequences outside it.
    """

    def __init__(self, settings: ConnectionSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("OPC UA Connection"))
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.endpoint_edit = QLineEdit(settings.endpoint, self)
        self.endpoint_edit.setToolTip(
            self.tr("For example opc.tcp://192.168.0.10:4840/ - or the mock server on localhost")
        )
        self.endpoint_edit.setMinimumWidth(320)
        form.addRow(self.tr("Endpoint:"), self.endpoint_edit)

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setRange(MIN_INTERVAL_MS, MAX_INTERVAL_MS)
        self.interval_spin.setSuffix(" ms")
        self.interval_spin.setValue(settings.publishing_interval_ms)
        self.interval_spin.setToolTip(
            self.tr("How often to ask the server to publish. It may grant a different one.")
        )
        form.addRow(self.tr("Publish every:"), self.interval_spin)
        layout.addLayout(form)

        self.writing_check = QCheckBox(self.tr("Allow writing to the server"), self)
        self.writing_check.setChecked(settings.allow_writing)
        self.writing_check.setToolTip(
            self.tr(
                "Off by default. With it off nothing this application produces can leave "
                "it, whatever a sensor is bound to."
            )
        )
        layout.addWidget(self.writing_check)

        warning = QLabel(
            self.tr(
                "Writing affects the machine on the other end. Leave it off unless you mean it."
            ),
            self,
        )
        warning.setWordWrap(True)
        warning.setEnabled(False)
        layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._settings = settings

    @property
    def settings(self) -> ConnectionSettings:
        """What the fields now say. The tag mapping is carried through untouched —
        this dialog is about the connection, not about the assignments."""
        return replace(
            self._settings,
            endpoint=self.endpoint_edit.text().strip(),
            publishing_interval_ms=self.interval_spin.value(),
            allow_writing=self.writing_check.isChecked(),
        )


class _BrowseThread(QThread):
    """One browse, off the thread that draws."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, endpoint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._endpoint = endpoint

    def run(self) -> None:
        """Never raises out of the thread: an exception here would take the
        window with it. The message goes to the dialog instead."""
        try:
            self.succeeded.emit(browse_variables(self._endpoint))
        except Exception as exc:
            logger.warning("browse failed", endpoint=self._endpoint, error=str(exc))
            self.failed.emit(str(exc))


class AssignTagDialog(QDialog):
    """Pick the node one variable reads from, and its unit conversion.

    The node id can be typed — a server that cannot be reached right now should
    not stop the assignment — but the list is the point: a NodeId read off
    someone else's screen is how the wrong tag gets bound.
    """

    def __init__(
        self,
        variable: str,
        endpoint: str,
        current: VariableTag | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Assign a Tag to {0}").format(variable))
        self.setModal(True)
        self._endpoint = endpoint
        self._thread: _BrowseThread | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_browse_group())
        layout.addLayout(self._build_tag_form(current))

        self.status_label = QLabel(self.tr("Not browsed yet"), self)
        self.status_label.setWordWrap(True)
        self.status_label.setEnabled(False)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_browse_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("What the server offers"), self)
        inner = QVBoxLayout(group)

        row = QHBoxLayout()
        self.browse_button = QPushButton(self.tr("&Browse"), group)
        self.browse_button.clicked.connect(self.start_browse)
        row.addWidget(self.browse_button)
        row.addStretch(1)
        inner.addLayout(row)

        self.node_tree = QTreeWidget(group)
        self.node_tree.setHeaderLabels(
            [self.tr("Where"), self.tr("Node id"), self.tr("Type"), self.tr("Way")]
        )
        self.node_tree.setRootIsDecorated(False)
        self.node_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.node_tree.setMinimumSize(520, 220)
        header = self.node_tree.header()
        header.setStretchLastSection(False)
        for column in range(_BROWSE_COLUMNS):
            header.setSectionResizeMode(column, header.ResizeMode.Interactive)
        for column, width in enumerate((190, 200, 70, 60)):
            self.node_tree.setColumnWidth(column, width)
        self.node_tree.itemSelectionChanged.connect(self._on_node_selected)
        inner.addWidget(self.node_tree)
        return group

    def _build_tag_form(self, current: VariableTag | None) -> QFormLayout:
        form = QFormLayout()

        self.node_edit = QLineEdit(current.node_id if current is not None else "", self)
        self.node_edit.setToolTip(self.tr("The NodeId in text form, e.g. ns=2;s=Axes.X.ActPos"))
        form.addRow(self.tr("Node id:"), self.node_edit)

        # The conversion belongs with the tag, not with the joint: the same axis
        # read from a different PLC may arrive in different units (R8).
        self.scale_spin = QDoubleSpinBox(self)
        self.scale_spin.setRange(-SCALE_LIMIT, SCALE_LIMIT)
        self.scale_spin.setDecimals(SCALE_DECIMALS)
        self.scale_spin.setValue(current.scale if current is not None else 1.0)
        self.scale_spin.setToolTip(
            self.tr("Multiplied by the raw value. 0.001 turns millimetres into metres.")
        )
        form.addRow(self.tr("Scale:"), self.scale_spin)

        self.offset_spin = QDoubleSpinBox(self)
        self.offset_spin.setRange(-OFFSET_LIMIT, OFFSET_LIMIT)
        self.offset_spin.setDecimals(SCALE_DECIMALS)
        self.offset_spin.setValue(current.offset if current is not None else 0.0)
        self.offset_spin.setToolTip(self.tr("Added after the scale, in metres or radians."))
        form.addRow(self.tr("Offset:"), self.offset_spin)
        return form

    # -- browsing -----------------------------------------------------------

    def start_browse(self) -> None:
        """Ask the server what it has. Does not block the window."""
        if self._thread is not None:
            return
        self.browse_button.setEnabled(False)
        self.status_label.setText(self.tr("Browsing {0}…").format(self._endpoint))

        thread = _BrowseThread(self._endpoint, self)
        thread.succeeded.connect(self.show_nodes)
        thread.failed.connect(self.show_failure)
        thread.finished.connect(self._on_browse_finished)
        self._thread = thread
        thread.start()

    def show_nodes(self, nodes: object) -> None:
        """Fill the list. Public so a test can supply nodes without a server."""
        found = tuple(nodes) if isinstance(nodes, (list, tuple)) else ()
        self.node_tree.clear()
        for node in found:
            if not isinstance(node, OpcUaNode):
                continue
            item = QTreeWidgetItem(
                [
                    node.browse_path,
                    node.node_id,
                    node.data_type,
                    self.tr("read/write") if node.is_writable else self.tr("read"),
                ]
            )
            if not node.is_numeric:
                # Greyed rather than hidden: it is obvious then that the tag was
                # found and rejected, not that it is missing.
                item.setDisabled(True)
                item.setToolTip(0, self.tr("Not a number - nothing here can use it"))
            self.node_tree.addTopLevelItem(item)

        self.status_label.setText(self.tr("{0} variables found").format(len(found)))

    def show_failure(self, message: str) -> None:
        """Report a browse that did not happen, in the dialog rather than a modal
        on top of a modal."""
        self.status_label.setText(self.tr("Could not browse: {0}").format(message))

    def _on_browse_finished(self) -> None:
        self._thread = None
        self.browse_button.setEnabled(True)

    def _on_node_selected(self) -> None:
        """Picking from the list fills the field, which stays editable."""
        items = self.node_tree.selectedItems()
        if items:
            self.node_edit.setText(items[0].text(1))

    # -- values -------------------------------------------------------------

    @property
    def tag(self) -> VariableTag | None:
        """The tag the fields describe, or `None` when no node was given.

        `None` rather than an error: leaving the field empty is how a variable is
        deliberately left unbound.
        """
        node_id = self.node_edit.text().strip()
        if not node_id:
            return None
        return VariableTag(
            node_id=node_id,
            scale=self.scale_spin.value(),
            offset=self.offset_spin.value(),
        )

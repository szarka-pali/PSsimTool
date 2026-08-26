"""Connecting to an OPC UA server, and picking a tag off it.

Two dialogs, and the first of them is the flow the way it actually goes:

    type an endpoint → ask what it offers → pick one and say who you are
        → connect → see what happened → walk its address space

Three tabs, in that order. **Server** does everything up to connecting,
**Address Space** is the tree that only exists once there is a session, and
**Diagnostics** is the line-by-line record of the attempt — which is the tab that
matters when it did not work, and the whole reason "Disconnected" was not enough.

Nothing here imports asyncua. Discovery, the session and the security types all
come from `io/`; this decides what to show and what to ask.

Everything that talks to a server runs on a worker thread. A discovery against an
unreachable host takes as long as its timeout, and a window that stops repainting
meanwhile looks broken — the same reason `ui/loader.StepImportThread` exists.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final, TypeGuard

from PySide6.QtCore import Qt, QThread, Signal
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
    QListWidget,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pssim.io.opcua_browse_session import BrowseNode, OpcUaBrowseSession
from pssim.io.opcua_diagnostics import DiagnosticLog
from pssim.io.opcua_security import (
    POLICY_NONE,
    Credentials,
    EndpointOffer,
    SecurityMode,
    TokenType,
    discover_endpoints,
)
from pssim.observability import get_logger
from pssim.ui.labels import describe_tag_conversion
from pssim.ui.opcua_browse_tree import OpcUaBrowseTree
from pssim.ui.settings import MAX_DECIMALS, ConnectionSettings, VariableTag

logger = get_logger(__name__)

#: The publishing interval a server may be asked for. Below ~10 ms no PLC keeps
#: up and the subscription only queues; above a second the motion is a slideshow.
MIN_INTERVAL_MS: Final = 10
MAX_INTERVAL_MS: Final = 5_000

#: How many decimal places the **offset** spin box offers. Not the tag's own
#: `decimals`, which is a count of the PLC integer's implied places.
OFFSET_DECIMALS: Final = 4

OFFSET_LIMIT: Final = 1_000_000.0

OFFER_COLUMNS: Final = 4


class _DiscoverThread(QThread):
    """One discovery, off the thread that draws."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, endpoint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._endpoint = endpoint

    def run(self) -> None:
        """Never raises out of the thread: that would take the window with it."""
        try:
            self.succeeded.emit(discover_endpoints(self._endpoint))
        except Exception as exc:
            logger.warning("discovery failed", endpoint=self._endpoint, error=str(exc))
            self.failed.emit(str(exc))


class _ConnectThread(QThread):
    """One attempt at opening a browse session."""

    succeeded = Signal(object)
    failed = Signal(str, object)

    def __init__(self, session: OpcUaBrowseSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = session

    def run(self) -> None:
        try:
            self._session.open()
        except Exception as exc:
            # The diagnostics travel with the failure: which step it stopped on
            # is the answer, and the message alone rarely is.
            self.failed.emit(str(exc), self._session.diagnostics)
            return
        self.succeeded.emit(self._session)


class ConnectionDialog(QDialog):
    """Where the server is, how to get in, and what it turned out to hold."""

    def __init__(self, settings: ConnectionSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("OPC UA Connection"))
        self.setModal(True)

        self._settings = settings
        self._offers: tuple[EndpointOffer, ...] = ()
        self._discovering: _DiscoverThread | None = None
        self._connecting: _ConnectThread | None = None
        self._session: OpcUaBrowseSession | None = None

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_server_tab(), self.tr("Server"))
        self.tabs.addTab(self._build_address_tab(), self.tr("Address Space"))
        self.tabs.addTab(self._build_diagnostics_tab(), self.tr("Diagnostics"))
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_settings(settings)
        self._update_authentication()

    # -- the tabs -----------------------------------------------------------

    def _build_server_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        form = QFormLayout()
        endpoint_row = QHBoxLayout()
        self.endpoint_edit = QLineEdit(tab)
        self.endpoint_edit.setToolTip(
            self.tr("For example opc.tcp://192.168.0.10:4840/ - or the mock server on localhost")
        )
        self.endpoint_edit.setMinimumWidth(320)
        endpoint_row.addWidget(self.endpoint_edit)

        self.discover_button = QPushButton(self.tr("&Discover"), tab)
        self.discover_button.setToolTip(self.tr("Ask the server how it may be talked to"))
        self.discover_button.clicked.connect(self.start_discovery)
        endpoint_row.addWidget(self.discover_button)
        form.addRow(self.tr("Endpoint:"), endpoint_row)

        self.interval_spin = QSpinBox(tab)
        self.interval_spin.setRange(MIN_INTERVAL_MS, MAX_INTERVAL_MS)
        self.interval_spin.setSuffix(" ms")
        self.interval_spin.setToolTip(
            self.tr("How often to ask the server to publish. It may grant a different one.")
        )
        form.addRow(self.tr("Publish every:"), self.interval_spin)
        layout.addLayout(form)

        layout.addWidget(self._build_offers_group(tab))
        layout.addWidget(self._build_authentication_group(tab))
        layout.addWidget(self._build_writing_group(tab))

        connect_row = QHBoxLayout()
        self.connect_button = QPushButton(self.tr("&Connect"), tab)
        self.connect_button.setToolTip(
            self.tr("Open a session with the chosen security, and browse it")
        )
        self.connect_button.clicked.connect(self.start_connect)
        connect_row.addWidget(self.connect_button)
        connect_row.addStretch(1)
        layout.addLayout(connect_row)

        self.status_label = QLabel(self.tr("Not connected"), tab)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return tab

    def _build_offers_group(self, parent: QWidget) -> QGroupBox:
        group = QGroupBox(self.tr("What the server offers"), parent)
        inner = QVBoxLayout(group)

        self.offer_tree = QTreeWidget(group)
        self.offer_tree.setHeaderLabels(
            [self.tr("Policy"), self.tr("Mode"), self.tr("Level"), self.tr("Accepts")]
        )
        self.offer_tree.setRootIsDecorated(False)
        self.offer_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.offer_tree.setMaximumHeight(140)
        header = self.offer_tree.header()
        header.setStretchLastSection(False)
        for column, width in enumerate((170, 130, 55, 170)):
            self.offer_tree.setColumnWidth(column, width)
        self.offer_tree.itemSelectionChanged.connect(self._on_offer_selected)
        inner.addWidget(self.offer_tree)

        self.offers_label = QLabel(self.tr("Not asked yet"), group)
        self.offers_label.setWordWrap(True)
        self.offers_label.setEnabled(False)
        inner.addWidget(self.offers_label)
        return group

    def _build_authentication_group(self, parent: QWidget) -> QGroupBox:
        group = QGroupBox(self.tr("Authentication"), parent)
        layout = QVBoxLayout(group)

        self.anonymous_radio = QRadioButton(self.tr("Anonymous"), group)
        self.anonymous_radio.toggled.connect(self._on_token_changed)
        layout.addWidget(self.anonymous_radio)

        self.username_radio = QRadioButton(self.tr("User name and password"), group)
        self.username_radio.toggled.connect(self._on_token_changed)
        layout.addWidget(self.username_radio)

        form = QFormLayout()
        self.username_edit = QLineEdit(group)
        form.addRow(self.tr("User:"), self.username_edit)

        self.password_edit = QLineEdit(group)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setToolTip(
            self.tr(
                "Not saved anywhere. Type it each session, or set PSSIM_OPCUA_PASSWORD "
                "for an unattended run."
            )
        )
        form.addRow(self.tr("Password:"), self.password_edit)
        layout.addLayout(form)
        return group

    def _build_writing_group(self, parent: QWidget) -> QGroupBox:
        group = QGroupBox(self.tr("Writing"), parent)
        layout = QVBoxLayout(group)

        self.writing_check = QCheckBox(self.tr("Allow writing to the server"), group)
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
            group,
        )
        warning.setWordWrap(True)
        warning.setEnabled(False)
        layout.addWidget(warning)
        return group

    def _build_address_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        self.browse_tree = OpcUaBrowseTree(tab)
        self.browse_tree.failed.connect(self._on_browse_failed)
        layout.addWidget(self.browse_tree)

        self.address_label = QLabel(self.tr("Connect first"), tab)
        self.address_label.setWordWrap(True)
        self.address_label.setEnabled(False)
        layout.addWidget(self.address_label)
        return tab

    def _build_diagnostics_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        self.diagnostics_list = QListWidget(tab)
        # Monospaced by nature of what it holds: step, outcome, code, detail,
        # and columns that line up are what make a log scannable.
        self.diagnostics_list.setAlternatingRowColors(True)
        layout.addWidget(self.diagnostics_list)
        return tab

    # -- settings in and out ------------------------------------------------

    def _apply_settings(self, settings: ConnectionSettings) -> None:
        self.endpoint_edit.setText(settings.endpoint)
        self.interval_spin.setValue(settings.publishing_interval_ms)
        self.writing_check.setChecked(settings.allow_writing)
        self.username_edit.setText(settings.username)
        if settings.token_type is TokenType.USERNAME:
            self.username_radio.setChecked(True)
        else:
            self.anonymous_radio.setChecked(True)

    @property
    def settings(self) -> ConnectionSettings:
        """What the fields say, minus the secret.

        The tag mapping is carried through untouched — this dialog is about the
        connection, not about the assignments — and **the password is not here**,
        because this is the object that gets written to disk.
        """
        return replace(
            self._settings,
            endpoint=self.endpoint_edit.text().strip(),
            publishing_interval_ms=self.interval_spin.value(),
            allow_writing=self.writing_check.isChecked(),
            policy_name=self._chosen_policy_name(),
            security_mode=self._chosen_mode(),
            token_type=self._chosen_token(),
            username=self.username_edit.text().strip(),
        )

    @property
    def password(self) -> str:
        """What was typed, for this session only. Never returned by `settings`."""
        return self.password_edit.text()

    def credentials(self) -> Credentials:
        """Everything needed to open a session right now, secret included."""
        return self.settings.credentials(self.password)

    def _chosen_policy_name(self) -> str:
        offer = self.selected_offer
        return offer.policy_name if offer is not None else self._settings.policy_name

    def _chosen_mode(self) -> SecurityMode:
        offer = self.selected_offer
        return offer.mode if offer is not None else self._settings.security_mode

    def _chosen_token(self) -> TokenType:
        return TokenType.USERNAME if self.username_radio.isChecked() else TokenType.ANONYMOUS

    # -- discovery ----------------------------------------------------------

    @property
    def offers(self) -> tuple[EndpointOffer, ...]:
        return self._offers

    @property
    def selected_offer(self) -> EndpointOffer | None:
        items = self.offer_tree.selectedItems()
        if not items:
            return None
        index = self.offer_tree.indexOfTopLevelItem(items[0])
        return self._offers[index] if 0 <= index < len(self._offers) else None

    def start_discovery(self) -> None:
        """Ask the server what it offers. Does not block the window."""
        endpoint = self.endpoint_edit.text().strip()
        if not endpoint or self._discovering is not None:
            return
        self.discover_button.setEnabled(False)
        self.offers_label.setText(self.tr("Asking {0}…").format(endpoint))

        thread = _DiscoverThread(endpoint, self)
        thread.succeeded.connect(self.show_offers)
        thread.failed.connect(self.show_discovery_failure)
        thread.finished.connect(self._on_discovery_finished)
        self._discovering = thread
        thread.start()

    def show_offers(self, offers: object) -> None:
        """Fill the table. Public so a test can supply offers without a server."""
        found = tuple(offer for offer in _as_tuple(offers) if isinstance(offer, EndpointOffer))
        self._offers = found
        self.offer_tree.clear()
        for offer in found:
            self.offer_tree.addTopLevelItem(_offer_item(offer))

        if not found:
            self.offers_label.setText(self.tr("The server offered nothing usable"))
            return

        self.offers_label.setText(self.tr("{0} ways in").format(len(found)))
        # Strongest first is how `discover_endpoints` sorts them, so selecting
        # the first is selecting what the server itself would prefer.
        first = self.offer_tree.topLevelItem(0)
        if first is not None:
            self.offer_tree.setCurrentItem(first)

    def show_discovery_failure(self, message: str) -> None:
        """Report a discovery that did not happen, in the dialog rather than a
        modal on top of a modal."""
        self.offers_label.setText(self.tr("Could not ask: {0}").format(message))

    def _on_discovery_finished(self) -> None:
        self._discovering = None
        self.discover_button.setEnabled(True)

    def _on_offer_selected(self) -> None:
        self._update_authentication()

    def _update_authentication(self) -> None:
        """Offer only what the chosen endpoint accepts.

        A server that does not list `Anonymous` will refuse an anonymous session,
        and that refusal used to arrive with nothing to explain it. Greyed here
        instead, before the click.
        """
        offer = self.selected_offer
        accepts_anonymous = offer is None or offer.accepts(TokenType.ANONYMOUS)
        accepts_username = offer is None or offer.accepts(TokenType.USERNAME)

        self.anonymous_radio.setEnabled(accepts_anonymous)
        self.username_radio.setEnabled(accepts_username)
        if not accepts_anonymous and accepts_username:
            self.username_radio.setChecked(True)
        elif not accepts_username and accepts_anonymous:
            self.anonymous_radio.setChecked(True)

        typing_a_user = self.username_radio.isChecked()
        self.username_edit.setEnabled(typing_a_user)
        self.password_edit.setEnabled(typing_a_user)

    def _on_token_changed(self, _checked: bool) -> None:
        self._update_authentication()

    # -- connecting ---------------------------------------------------------

    @property
    def session(self) -> OpcUaBrowseSession | None:
        """The open session, or `None`. The dialog owns it while it is open."""
        return self._session

    def start_connect(self) -> None:
        """Open a session with the chosen security, and browse it."""
        endpoint = self.endpoint_edit.text().strip()
        if not endpoint or self._connecting is not None:
            return

        self.close_session()
        self.connect_button.setEnabled(False)
        self.status_label.setText(self.tr("Connecting to {0}…").format(endpoint))

        session = OpcUaBrowseSession(endpoint, self.credentials())
        thread = _ConnectThread(session, self)
        thread.succeeded.connect(self.show_connected)
        thread.failed.connect(self.show_connect_failure)
        thread.finished.connect(self._on_connect_finished)
        self._connecting = thread
        thread.start()

    def show_connected(self, session: object) -> None:
        """A session is up: show its tree and its log."""
        if not isinstance(session, OpcUaBrowseSession):
            return
        self._session = session
        self.status_label.setText(self.tr("Connected: {0}").format(self.credentials().describe()))
        self.address_label.setText(self.tr("Open a folder to read what is in it"))
        self.browse_tree.set_session(session)
        self.show_diagnostics(session.diagnostics)
        self.tabs.setCurrentIndex(1)

    def show_connect_failure(self, message: str, diagnostics: object) -> None:
        """Report a connection that did not happen, and show why.

        The Diagnostics tab is raised rather than merely filled: the answer is
        there, and a failure that leaves the user looking at the same form they
        just submitted tells them nothing.
        """
        self.status_label.setText(self.tr("Could not connect: {0}").format(message))
        if isinstance(diagnostics, DiagnosticLog):
            self.show_diagnostics(diagnostics)
        self.tabs.setCurrentIndex(2)

    def show_diagnostics(self, diagnostics: DiagnosticLog) -> None:
        """Render a log. Public so a test, or the window's own Diagnostics entry,
        can show one without a dialog having produced it."""
        self.diagnostics_list.clear()
        for entry in diagnostics.entries:
            self.diagnostics_list.addItem(entry.describe())

    def _on_connect_finished(self) -> None:
        self._connecting = None
        self.connect_button.setEnabled(True)

    def _on_browse_failed(self, message: str) -> None:
        self.address_label.setText(self.tr("Could not read that folder: {0}").format(message))

    def close_session(self) -> None:
        """Let go of the session. Idempotent."""
        self.browse_tree.set_session(None)
        session = self._session
        self._session = None
        if session is not None:
            session.close()

    def done(self, result: int) -> None:
        """Qt's own close path, whichever button was pressed.

        The session is closed here rather than in `accept`: Cancel, Escape and
        the window button all arrive here, and a session left open would hold a
        server's resources for as long as the application ran.
        """
        self.close_session()
        super().done(result)


class AssignTagDialog(QDialog):
    """Pick the node one variable reads from, and its unit conversion.

    The node id can be typed — a server that cannot be reached right now should
    not stop the assignment — but the tree is the point: a NodeId read off
    someone else's screen is how the wrong tag gets bound.
    """

    def __init__(
        self,
        variable: str,
        endpoint: str,
        current: VariableTag | None = None,
        credentials: Credentials | None = None,
        unit: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Assign a Tag to {0}").format(variable))
        self.setModal(True)
        self._endpoint = endpoint
        # What the PLC's number means: `mm` for a rail, `°` for a rotary head.
        # Passed in because only the window knows which joint named the variable;
        # empty for a sensor's, which has no joint to ask (R16).
        self._unit = unit or self.tr("units")
        self._credentials = credentials or Credentials()
        self._session: OpcUaBrowseSession | None = None
        self._connecting: _ConnectThread | None = None

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

        self.node_tree = OpcUaBrowseTree(group)
        self.node_tree.require_numeric()
        self.node_tree.node_selected.connect(self._on_node_selected)
        self.node_tree.node_activated.connect(self._on_node_activated)
        self.node_tree.failed.connect(self.show_failure)
        inner.addWidget(self.node_tree)
        return group

    def _build_tag_form(self, current: VariableTag | None) -> QFormLayout:
        form = QFormLayout()

        self.node_edit = QLineEdit(current.node_id if current is not None else "", self)
        self.node_edit.setToolTip(self.tr("The NodeId in text form, e.g. ns=2;s=Axes.X.ActPos"))
        form.addRow(self.tr("Node id:"), self.node_edit)

        # Shown as a field of its own rather than folded into the node id: they
        # are two different things, and a struct's every field shares one node
        # id, so the path is what says which of them this is.
        self.path_edit = QLineEdit(current.path if current is not None else "", self)
        self.path_edit.setPlaceholderText(self.tr("the whole value"))
        self.path_edit.setToolTip(
            self.tr(
                "Which field of a structure, or which element of an array: "
                "Position.X, Limits[1]. Empty reads the node's own value."
            )
        )
        form.addRow(self.tr("Path:"), self.path_edit)

        # Decimal places rather than a factor: a `REAL` is 1:1 and a `DINT`
        # has an implied decimal point, which is how a PLC programmer knows the
        # number. Working the unit conversion out by hand is what produced
        # `scale: 1.7453292519943296e-05` in `machines/example.yaml`.
        self.decimals_spin = QSpinBox(self)
        self.decimals_spin.setRange(0, MAX_DECIMALS)
        self.decimals_spin.setValue(current.decimals if current is not None else 0)
        self.decimals_spin.setToolTip(
            self.tr(
                "How many decimal places an integer value carries. 0 for a REAL or "
                "FLOAT, which is then 1:1. With 1, the PLC's 652 reads 65.2 {0}."
            ).format(self._unit)
        )
        form.addRow(self.tr("Decimal places:"), self.decimals_spin)

        self.offset_spin = QDoubleSpinBox(self)
        self.offset_spin.setRange(-OFFSET_LIMIT, OFFSET_LIMIT)
        self.offset_spin.setDecimals(OFFSET_DECIMALS)
        self.offset_spin.setValue(current.offset if current is not None else 0.0)
        self.offset_spin.setSuffix(f" {self._unit}")
        self.offset_spin.setToolTip(
            self.tr(
                "Added after the decimal places, in the same unit as the value. "
                "A zero point: -100 if the PLC reads 100 where the machine is at zero."
            )
        )
        form.addRow(self.tr("Offset:"), self.offset_spin)

        self.conversion_label = QLabel(self)
        self.conversion_label.setEnabled(False)
        form.addRow("", self.conversion_label)
        self._refresh_conversion()
        self.decimals_spin.valueChanged.connect(self._on_conversion_changed)
        self.offset_spin.valueChanged.connect(self._on_conversion_changed)
        return form

    def _on_conversion_changed(self, _value: object) -> None:
        self._refresh_conversion()

    def _refresh_conversion(self) -> None:
        """Show what one number would become, worked through.

        A line of arithmetic rather than a rule to be trusted: a wrong decimal
        place is invisible in the fields and obvious here, and this is the one
        setting in the application that silently puts a model somewhere else.
        """
        self.conversion_label.setText(
            describe_tag_conversion(
                self.decimals_spin.value(), self.offset_spin.value(), self._unit
            )
        )

    # -- browsing -----------------------------------------------------------

    def start_browse(self) -> None:
        """Open a session and show the address space. Does not block."""
        if self._connecting is not None:
            return
        self.browse_button.setEnabled(False)
        self.status_label.setText(self.tr("Connecting to {0}…").format(self._endpoint))

        session = OpcUaBrowseSession(self._endpoint, self._credentials)
        thread = _ConnectThread(session, self)
        thread.succeeded.connect(self.show_session)
        thread.failed.connect(self._on_connect_failed)
        thread.finished.connect(self._on_browse_finished)
        self._connecting = thread
        thread.start()

    def _on_connect_failed(self, message: str, _diagnostics: object) -> None:
        """The log is not shown here: this dialog assigns a tag, and the place to
        read a failed attempt is `Communication → Diagnostics…`."""
        self.show_failure(message)

    def show_session(self, session: object) -> None:
        """Show an open session's tree. Public so a test can supply one."""
        if not isinstance(session, OpcUaBrowseSession):
            return
        self._session = session
        self.node_tree.set_session(session)
        self.status_label.setText(self.tr("Open a folder to read what is in it"))

    def show_failure(self, message: str) -> None:
        """Report a browse that did not happen, in the dialog rather than a modal
        on top of a modal."""
        self.status_label.setText(self.tr("Could not browse: {0}").format(message))

    def _on_browse_finished(self) -> None:
        self._connecting = None
        self.browse_button.setEnabled(True)

    def _on_node_selected(self, node: object) -> None:
        """Picking in the tree fills the fields, which stay editable.

        Only something bindable: selecting a folder, a struct or an array is
        navigation — a struct is the row you open on the way to the field you
        want — and letting any of them overwrite what is already typed would lose
        it on the way past.
        """
        if _is_pickable(node):
            self._take(node)

    def _on_node_activated(self, node: object) -> None:
        """A double-click on something bindable is "that one, done"."""
        if _is_pickable(node):
            self._take(node)
            self.accept()

    def _take(self, node: BrowseNode) -> None:
        """Both halves of what a row identifies. The path is set even when it is
        empty: picking a plain node after a field has to clear the old path, or
        the tag would keep pointing inside a value the new node does not have."""
        self.node_edit.setText(node.node_id)
        self.path_edit.setText(node.path)

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
            decimals=self.decimals_spin.value(),
            offset=self.offset_spin.value(),
            path=self.path_edit.text().strip(),
        )

    def done(self, result: int) -> None:
        """Close the session however the dialog is dismissed."""
        self.node_tree.set_session(None)
        session = self._session
        self._session = None
        if session is not None:
            session.close()
        super().done(result)


def _is_pickable(node: object) -> TypeGuard[BrowseNode]:
    """Whether selecting this row means "bind that".

    A container is excluded: a struct's value is an object and an array's is a
    list, and neither is a number. It is still the row that gets opened to reach
    the field that is one.
    """
    # A `TypeGuard`, so the caller may then read the node's fields without a
    # second `isinstance` at every call site.
    return isinstance(node, BrowseNode) and node.is_variable and not node.is_container


class DiagnosticsDialog(QDialog):
    """The last connection attempt, line by line.

    Its own dialog because the question it answers — "why am I not connected" —
    comes up long after the connection dialog was closed, and reopening that one
    to read a log would start by offering to connect again.
    """

    def __init__(self, diagnostics: DiagnosticLog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("OPC UA Diagnostics"))
        self.setModal(True)

        layout = QVBoxLayout(self)
        self.entry_list = QListWidget(self)
        self.entry_list.setAlternatingRowColors(True)
        self.entry_list.setMinimumSize(620, 240)
        layout.addWidget(self.entry_list)

        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.show_diagnostics(diagnostics)

    def show_diagnostics(self, diagnostics: DiagnosticLog) -> None:
        """Render a log, and say in one line what it amounts to."""
        self.entry_list.clear()
        for entry in diagnostics.entries:
            self.entry_list.addItem(entry.describe())

        failure = diagnostics.last_failure
        if not diagnostics.entries:
            self.summary_label.setText(self.tr("Nothing has been attempted yet"))
        elif failure is None:
            self.summary_label.setText(self.tr("Nothing failed"))
        else:
            self.summary_label.setText(
                self.tr("Stopped at {0}: {1}").format(
                    failure.step.value, failure.status_code or failure.detail
                )
            )


def _offer_item(offer: EndpointOffer) -> QTreeWidgetItem:
    """One row of what the server offers."""
    item = QTreeWidgetItem(
        [
            offer.policy_name,
            offer.mode.value,
            str(offer.security_level),
            ", ".join(token.value for token in offer.token_types),
        ]
    )
    item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight)
    if offer.policy_name == POLICY_NONE:
        item.setToolTip(0, "No security: the traffic is readable by anything on the network")
    return item


def _as_tuple(value: object) -> tuple[object, ...]:
    """Whatever came over a signal, as a tuple. A `Signal(object)` carries lists
    and tuples alike, and neither is worth a branch at every call site."""
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()

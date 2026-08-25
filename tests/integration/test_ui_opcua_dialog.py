"""Tests of the connection dialog, the browse tree and the diagnostics dialog.

**No server.** Discovery, sessions and browsing all happen on worker threads
against `io/`, and every one of them has a public "show this" entry point so a
test can supply the answer directly. What is exercised here is what the dialog
does with an answer, which is where the decisions are.

The one property that is a safety matter rather than a convenience is pinned
twice: the password reaches `credentials()` and never reaches `settings`.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.io.opcua_browse_session import BrowseNode, BrowseResult, NodeKind  # noqa: E402
from pssim.io.opcua_diagnostics import DiagnosticLog, DiagnosticStep  # noqa: E402
from pssim.io.opcua_security import (  # noqa: E402
    POLICY_NONE,
    EndpointOffer,
    SecurityMode,
    TokenType,
)
from pssim.ui.opcua_browse_tree import (  # noqa: E402
    COLUMN_ACCESS,
    COLUMN_NAME,
    COLUMN_NODE_ID,
    OpcUaBrowseTree,
)
from pssim.ui.opcua_dialog import (  # noqa: E402
    AssignTagDialog,
    ConnectionDialog,
    DiagnosticsDialog,
)
from pssim.ui.settings import ConnectionSettings, VariableTag  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def offer(
    policy_name: str = "Basic256Sha256",
    mode: SecurityMode = SecurityMode.SIGN_AND_ENCRYPT,
    tokens: tuple[TokenType, ...] = (TokenType.ANONYMOUS, TokenType.USERNAME),
    level: int = 3,
) -> EndpointOffer:
    return EndpointOffer(
        endpoint_url="opc.tcp://plc:4840/",
        policy_name=policy_name,
        mode=mode,
        security_level=level,
        token_types=tokens,
    )


def node(
    node_id: str = "ns=2;s=Axes.X.ActPos",
    name: str = "Axes.X.ActPos",
    kind: NodeKind = NodeKind.VARIABLE,
    data_type: str = "Double",
    is_writable: bool = False,
    has_children: bool = False,
) -> BrowseNode:
    return BrowseNode(
        node_id=node_id,
        browse_name=name,
        display_name=name,
        kind=kind,
        data_type=data_type,
        is_writable=is_writable,
        has_children=has_children,
    )


@pytest.fixture
def dialog(qt_app: QApplication) -> ConnectionDialog:
    return ConnectionDialog(ConnectionSettings(endpoint="opc.tcp://plc:4840/"))


class TestTheTabs:
    def test_there_are_three(self, dialog: ConnectionDialog) -> None:
        assert dialog.tabs.count() == 3

    def test_they_read_in_the_order_it_happens(self, dialog: ConnectionDialog) -> None:
        titles = [dialog.tabs.tabText(index) for index in range(3)]

        assert titles == ["Server", "Address Space", "Diagnostics"]

    def test_it_opens_on_the_server_tab(self, dialog: ConnectionDialog) -> None:
        assert dialog.tabs.currentIndex() == 0


class TestDiscovery:
    def test_nothing_is_offered_before_asking(self, dialog: ConnectionDialog) -> None:
        assert dialog.offers == ()

    def test_offers_fill_the_table(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([offer(), offer(POLICY_NONE, SecurityMode.NONE, level=0)])

        assert dialog.offer_tree.topLevelItemCount() == 2

    def test_the_first_is_selected(self, dialog: ConnectionDialog) -> None:
        # `discover_endpoints` sorts strongest first, so selecting the first is
        # selecting what the server itself would prefer.
        dialog.show_offers([offer(), offer(POLICY_NONE, SecurityMode.NONE, level=0)])

        chosen = dialog.selected_offer
        assert chosen is not None
        assert chosen.policy_name == "Basic256Sha256"

    def test_choosing_an_offer_sets_the_policy(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([offer(POLICY_NONE, SecurityMode.NONE, level=0), offer()])

        dialog.offer_tree.setCurrentItem(dialog.offer_tree.topLevelItem(1))

        assert dialog.settings.policy_name == "Basic256Sha256"
        assert dialog.settings.security_mode is SecurityMode.SIGN_AND_ENCRYPT

    def test_a_failed_discovery_is_reported_in_the_dialog(self, dialog: ConnectionDialog) -> None:
        # Not a modal on top of a modal.
        dialog.show_discovery_failure("connection refused")

        assert "connection refused" in dialog.offers_label.text()

    def test_an_empty_answer_says_so(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([])

        assert "nothing" in dialog.offers_label.text().lower()


class TestAuthenticationFollowsTheOffer:
    """The thing that used to fail with no explanation: a server that does not
    offer anonymous refuses an anonymous session."""

    def test_anonymous_is_greyed_when_it_is_not_offered(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([offer(tokens=(TokenType.USERNAME,))])

        assert dialog.anonymous_radio.isEnabled() is False

    def test_and_the_user_is_chosen_instead(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([offer(tokens=(TokenType.USERNAME,))])

        assert dialog.username_radio.isChecked() is True

    def test_the_user_is_greyed_when_only_anonymous_is_offered(
        self, dialog: ConnectionDialog
    ) -> None:
        dialog.show_offers([offer(tokens=(TokenType.ANONYMOUS,))])

        assert dialog.username_radio.isEnabled() is False

    def test_both_stay_available_when_both_are_offered(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([offer()])

        assert dialog.anonymous_radio.isEnabled()
        assert dialog.username_radio.isEnabled()

    def test_the_name_and_password_follow_the_choice(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([offer()])

        dialog.anonymous_radio.setChecked(True)
        assert dialog.password_edit.isEnabled() is False

        dialog.username_radio.setChecked(True)
        assert dialog.password_edit.isEnabled() is True


class TestThePasswordNeverLeaves:
    def test_it_reaches_the_credentials(self, dialog: ConnectionDialog) -> None:
        dialog.show_offers([offer()])
        dialog.username_radio.setChecked(True)
        dialog.username_edit.setText("operator")
        dialog.password_edit.setText("s3cret")

        assert dialog.credentials().password == "s3cret"

    def test_the_settings_do_not_hold_it(self, dialog: ConnectionDialog) -> None:
        # `settings` is the object that gets written to disk.
        dialog.username_radio.setChecked(True)
        dialog.password_edit.setText("s3cret")

        assert "s3cret" not in str(dialog.settings.to_dict())

    def test_the_user_name_is_kept(self, dialog: ConnectionDialog) -> None:
        # The name is remembered; only the secret is not.
        dialog.username_radio.setChecked(True)
        dialog.username_edit.setText("operator")

        assert dialog.settings.username == "operator"

    def test_the_tags_are_carried_through(self, qt_app: QApplication) -> None:
        # This dialog is about the connection, not about the assignments.
        settings = ConnectionSettings().with_tag("X", VariableTag(node_id="ns=2;s=X"))

        dialog = ConnectionDialog(settings)

        assert dialog.settings.tag_for("X") is not None


class TestDiagnosticsInTheDialog:
    def test_a_failure_raises_the_diagnostics_tab(self, dialog: ConnectionDialog) -> None:
        # A failure that leaves the user looking at the form they just submitted
        # tells them nothing.
        log = DiagnosticLog()
        log.failed(DiagnosticStep.SESSION, RuntimeError("refused"))

        dialog.show_connect_failure("refused", log)

        assert dialog.tabs.currentIndex() == 2

    def test_the_failure_is_listed(self, dialog: ConnectionDialog) -> None:
        log = DiagnosticLog()
        log.failed(DiagnosticStep.SESSION, RuntimeError("refused"))

        dialog.show_connect_failure("refused", log)

        assert dialog.diagnostics_list.count() == 1

    def test_the_status_says_what_happened(self, dialog: ConnectionDialog) -> None:
        dialog.show_connect_failure("refused", DiagnosticLog())

        assert "refused" in dialog.status_label.text()


class TestTheDiagnosticsDialog:
    def test_an_empty_log_says_nothing_was_tried(self, qt_app: QApplication) -> None:
        dialog = DiagnosticsDialog(DiagnosticLog())

        assert "attempted" in dialog.summary_label.text()

    def test_a_clean_log_says_nothing_failed(self, qt_app: QApplication) -> None:
        log = DiagnosticLog()
        log.ok(DiagnosticStep.SESSION, "opc.tcp://plc:4840/")

        dialog = DiagnosticsDialog(log)

        assert "failed" in dialog.summary_label.text()

    def test_a_failure_names_the_step_it_stopped_on(self, qt_app: QApplication) -> None:
        from asyncua.ua.uaerrors import BadUserAccessDenied

        log = DiagnosticLog()
        log.ok(DiagnosticStep.CERTIFICATE, "none needed")
        log.failed(DiagnosticStep.SESSION, BadUserAccessDenied())

        dialog = DiagnosticsDialog(log)

        summary = dialog.summary_label.text()
        assert "session" in summary
        assert "BadUserAccessDenied" in summary

    def test_every_line_is_listed(self, qt_app: QApplication) -> None:
        log = DiagnosticLog()
        log.ok(DiagnosticStep.CERTIFICATE)
        log.ok(DiagnosticStep.SESSION)
        log.ok(DiagnosticStep.SUBSCRIBE)

        dialog = DiagnosticsDialog(log)

        assert dialog.entry_list.count() == 3


class TestTheBrowseTree:
    @pytest.fixture
    def tree(self, qt_app: QApplication) -> OpcUaBrowseTree:
        return OpcUaBrowseTree()

    def test_it_starts_empty(self, tree: OpcUaBrowseTree) -> None:
        assert tree.topLevelItemCount() == 0

    def test_children_fill_the_top_level(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(node(), node("ns=2;s=Y", "Y"))))

        assert tree.topLevelItemCount() == 2

    def test_a_row_shows_the_node_id(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(node(),)))

        assert tree.topLevelItem(0).text(COLUMN_NODE_ID) == "ns=2;s=Axes.X.ActPos"

    def test_a_read_only_variable_says_r(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(node(),)))

        assert tree.topLevelItem(0).text(COLUMN_ACCESS) == "R"

    def test_a_writable_variable_says_rw(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(node(is_writable=True),)))

        assert tree.topLevelItem(0).text(COLUMN_ACCESS) == "RW"

    def test_a_folder_gets_an_expander(self, tree: OpcUaBrowseTree) -> None:
        # Without a placeholder child a folder reads as empty until it is opened,
        # which is the one thing it cannot be.
        folder = node("ns=2;i=1", "Axes", NodeKind.OBJECT, "", has_children=True)
        tree._on_children("i=85", None, BrowseResult(nodes=(folder,)))

        assert tree.topLevelItem(0).childCount() == 1

    def test_a_truncated_folder_says_so(self, tree: OpcUaBrowseTree) -> None:
        # A folder cut off at the limit that looked complete would be a lie
        # about the server.
        tree._on_children("i=85", None, BrowseResult(nodes=(node(),), is_truncated=True))

        assert tree.topLevelItemCount() == 2
        assert "not shown" in tree.topLevelItem(1).text(COLUMN_NAME)

    def test_a_method_is_shown_but_greyed(self, tree: OpcUaBrowseTree) -> None:
        method = node("ns=2;i=9", "DoThing", NodeKind.OTHER, "")
        tree._on_children("i=85", None, BrowseResult(nodes=(method,)))

        assert tree.topLevelItem(0).isDisabled() is True

    def test_the_selected_node_reads_back(self, tree: OpcUaBrowseTree) -> None:
        tree._on_children("i=85", None, BrowseResult(nodes=(node(),)))

        tree.setCurrentItem(tree.topLevelItem(0))

        selected = tree.selected_node
        assert selected is not None
        assert selected.node_id == "ns=2;s=Axes.X.ActPos"


class TestAssigningFromTheTree:
    @pytest.fixture
    def assign(self, qt_app: QApplication) -> AssignTagDialog:
        return AssignTagDialog("X", "opc.tcp://plc:4840/")

    def test_picking_a_variable_fills_the_field(self, assign: AssignTagDialog) -> None:
        assign._on_node_selected(node())

        assert assign.node_edit.text() == "ns=2;s=Axes.X.ActPos"

    def test_picking_a_folder_does_not(self, assign: AssignTagDialog) -> None:
        # Selecting a folder is navigation; letting it overwrite a node id
        # already typed would lose it on the way past.
        assign.node_edit.setText("ns=2;s=Kept")

        assign._on_node_selected(node("ns=2;i=1", "Axes", NodeKind.OBJECT, ""))

        assert assign.node_edit.text() == "ns=2;s=Kept"

    def test_browsed_nodes_are_listed(self, assign: AssignTagDialog) -> None:
        assign.node_tree._on_children("i=85", None, BrowseResult(nodes=(node(),)))

        assert assign.node_tree.topLevelItemCount() == 1

    def test_a_non_numeric_variable_is_offered_but_disabled(self, assign: AssignTagDialog) -> None:
        # Greyed rather than hidden: it is obvious then that the tag was found
        # and rejected, not that it is missing.
        text = node("ns=2;s=Name", "Name", data_type="String")
        assign.node_tree._on_children("i=85", None, BrowseResult(nodes=(text,)))

        assert assign.node_tree.topLevelItem(0).isDisabled() is True

    def test_a_numeric_variable_is_not(self, assign: AssignTagDialog) -> None:
        assign.node_tree._on_children("i=85", None, BrowseResult(nodes=(node(),)))

        assert assign.node_tree.topLevelItem(0).isDisabled() is False

    def test_the_address_space_tree_greys_nothing(
        self, qt_app: QApplication, dialog: ConnectionDialog
    ) -> None:
        # Nothing is being picked there, so a `String` node greyed out would be
        # a judgement on a node nobody asked about.
        text = node("ns=2;s=Name", "Name", data_type="String")
        dialog.browse_tree._on_children("i=85", None, BrowseResult(nodes=(text,)))

        assert dialog.browse_tree.topLevelItem(0).isDisabled() is False

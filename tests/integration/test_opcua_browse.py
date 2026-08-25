"""The live browse session, against `pssim mock-server` and nothing else.

Its own file because it is its own module (`io/opcua_browse_session.py`), and
because the point of it is different from the rest of connecting: that **nothing
is read until something asks**. A real PLC address space runs to thousands of
nodes, and reading all of them to show one folder is not something a window can
do.

Run with: ``uv run pytest -m integration``
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from pssim.domain.errors import DataSourceError
from pssim.io.mock_server import MockSecurity, run_mock_server
from pssim.io.opcua_browse_session import OBJECTS_NODE_ID, OpcUaBrowseSession
from pssim.io.opcua_security import Credentials, SecurityMode, TokenType

pytestmark = pytest.mark.integration

USER = "operator"
PASSWORD = "letmein"


class SecureMockServer:
    """The mock server with a given security, in its own thread and on its own
    port — so tests that run one after another never meet a socket the previous
    one has not let go of yet."""

    def __init__(self, port: int, security: MockSecurity, duration_s: float = 12.0) -> None:
        self.endpoint = f"opc.tcp://127.0.0.1:{port}/pssim-browse/"
        self._security = security
        self._duration_s = duration_s
        self._thread = threading.Thread(target=self._run, name="mock-browse", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                self.endpoint,
                update_interval_s=0.1,
                duration_s=self._duration_s,
                security=self._security,
            )
        )

    def __enter__(self) -> SecureMockServer:
        self._thread.start()
        time.sleep(2.0)  # the endpoint has to be open before a client asks
        return self

    def __exit__(self, *_: object) -> None:
        self._thread.join(timeout=self._duration_s + 5.0)


class TestBrowseSession:
    """The session held open so the tree can be walked a folder at a time.

    The point being that nothing is read until something asks: a real PLC holds
    thousands of nodes, and reading all of them to show one folder is not
    something a window can do.
    """

    def test_it_opens_on_an_open_server(self) -> None:
        with (
            SecureMockServer(48450, MockSecurity()) as server,
            OpcUaBrowseSession(server.endpoint) as session,
        ):
            assert session.is_open is True

    def test_the_objects_folder_has_what_the_server_added(self) -> None:
        with (
            SecureMockServer(48451, MockSecurity()) as server,
            OpcUaBrowseSession(server.endpoint) as session,
        ):
            names = {node.label for node in session.children_of(OBJECTS_NODE_ID)}

        assert {"Axes", "Sim"} <= names

    def test_a_folder_is_read_only_when_it_is_opened(self) -> None:
        # The children of Axes are not in the answer for Objects; they arrive
        # when Axes itself is asked about.
        with (
            SecureMockServer(48452, MockSecurity()) as server,
            OpcUaBrowseSession(server.endpoint) as session,
        ):
            top = session.children_of(OBJECTS_NODE_ID)
            axes = next(node for node in top if node.label == "Axes")

            assert axes.has_children is True
            assert not any(node.label.startswith("Axes.") for node in top)

            inside = {node.label for node in session.children_of(axes.node_id)}

        assert "Axes.X.ActPos" in inside

    def test_a_variable_carries_its_type(self) -> None:
        with (
            SecureMockServer(48453, MockSecurity()) as server,
            OpcUaBrowseSession(server.endpoint) as session,
        ):
            axes = next(
                node for node in session.children_of(OBJECTS_NODE_ID) if node.label == "Axes"
            )
            variable = session.children_of(axes.node_id).nodes[0]

        assert variable.is_variable is True
        assert variable.data_type == "Double"

    def test_an_axis_is_read_only_and_an_output_is_not(self) -> None:
        with (
            SecureMockServer(48454, MockSecurity()) as server,
            OpcUaBrowseSession(server.endpoint) as session,
        ):
            top = session.children_of(OBJECTS_NODE_ID)
            axes = next(node for node in top if node.label == "Axes")
            sim = next(node for node in top if node.label == "Sim")
            axis = session.children_of(axes.node_id).nodes[0]
            output = session.children_of(sim.node_id).nodes[0]

        assert axis.is_writable is False
        assert output.is_writable is True

    def test_the_tree_keeps_the_standard_namespace(self) -> None:
        # Unlike the flat `browse_variables`, which drops it: this is the address
        # space as the server has it, and UaExpert shows the same.
        with (
            SecureMockServer(48455, MockSecurity()) as server,
            OpcUaBrowseSession(server.endpoint) as session,
        ):
            names = {node.label for node in session.children_of(OBJECTS_NODE_ID)}

        assert "Server" in names

    def test_it_browses_over_a_secure_connection(self) -> None:
        credentials = Credentials(policy_name="Basic256Sha256", mode=SecurityMode.SIGN_AND_ENCRYPT)

        with (
            SecureMockServer(48456, MockSecurity(is_secure=True)) as server,
            OpcUaBrowseSession(server.endpoint, credentials) as session,
        ):
            names = {node.label for node in session.children_of(OBJECTS_NODE_ID)}

        assert "Axes" in names

    def test_it_browses_as_an_authenticated_user(self) -> None:
        credentials = Credentials(token=TokenType.USERNAME, username=USER, password=PASSWORD)

        with (
            SecureMockServer(48457, MockSecurity(username=USER, password=PASSWORD)) as server,
            OpcUaBrowseSession(server.endpoint, credentials) as session,
        ):
            names = {node.label for node in session.children_of(OBJECTS_NODE_ID)}

        assert "Axes" in names

    def test_a_refused_password_does_not_open(self) -> None:
        credentials = Credentials(token=TokenType.USERNAME, username=USER, password="nope")

        with SecureMockServer(48458, MockSecurity(username=USER, password=PASSWORD)) as server:
            session = OpcUaBrowseSession(server.endpoint, credentials)
            with pytest.raises(DataSourceError):
                session.open()

        failure = session.diagnostics.last_failure
        assert failure is not None
        assert "AccessDenied" in (failure.status_code or failure.detail)

    def test_a_server_that_is_not_there_does_not_open(self) -> None:
        session = OpcUaBrowseSession("opc.tcp://127.0.0.1:1/nothing/")

        with pytest.raises(DataSourceError):
            session.open(timeout_s=10.0)

    def test_asking_a_closed_session_is_a_typed_error(self) -> None:
        session = OpcUaBrowseSession("opc.tcp://127.0.0.1:1/nothing/")

        with pytest.raises(DataSourceError):
            session.children_of(OBJECTS_NODE_ID)

    def test_closing_twice_is_harmless(self) -> None:
        with SecureMockServer(48459, MockSecurity()) as server:
            session = OpcUaBrowseSession(server.endpoint)
            session.open()
            session.close()
            session.close()

        assert session.is_open is False

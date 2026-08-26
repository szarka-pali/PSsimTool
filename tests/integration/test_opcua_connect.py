"""Connecting: security, authentication, and what the diagnostics say about it.

Against `pssim mock-server` and nothing else.

Named `test_opcua_connect` rather than `test_opcua_security` because pytest
imports test modules by basename, and `tests/unit/io/test_opcua_security.py`
already owns that one — two files with the same name and no package around them
collide at collection.

These are the paths a client that has only ever met an open server has never
taken: a real policy, a real user check, and the two refusals — wrong password,
and anonymous where anonymous is not offered.

The mock server is started per test with the security it is being asked about.
Never against a real machine (`.claude/rules/io-opcua.md`).

Run with: ``uv run pytest -m integration``
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from asyncua.ua.uaerrors import UaStatusCodeError

from pssim.cli import PASSWORD_ENV
from pssim.config.binding import JointBinding
from pssim.domain.errors import DataSourceError
from pssim.io._ready import wait_for_endpoint
from pssim.io.base import SourceStatus
from pssim.io.mock_server import MockSecurity, run_mock_server
from pssim.io.opcua_diagnostics import DiagnosticStep, Outcome
from pssim.io.opcua_security import (
    POLICY_NONE,
    Credentials,
    SecurityMode,
    TokenType,
    discover_endpoints,
)
from pssim.io.opcua_source import OpcUaConfig, OpcUaSource

pytestmark = pytest.mark.integration

NAMESPACE_INDEX = 2
AXIS_NODE = f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos"
STRUCT_NODE = f"ns={NAMESPACE_INDEX};s=Struct.AxisState"

USER = "operator"
PASSWORD = "letmein"


class SecureMockServer:
    """The mock server with a given security, in its own thread.

    Its own port per instance, so tests that run one after another never meet a
    socket the previous one has not let go of yet.
    """

    def __init__(self, port: int, security: MockSecurity, duration_s: float = 30.0) -> None:
        self.endpoint = f"opc.tcp://127.0.0.1:{port}/pssim-security/"
        self._security = security
        self._duration_s = duration_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mock-secure", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                self.endpoint,
                update_interval_s=0.1,
                duration_s=self._duration_s,
                security=self._security,
                stop_event=self._stop,
            )
        )

    def __enter__(self) -> SecureMockServer:
        self._thread.start()
        # Asked rather than slept for: a fixed wait is either longer than
        # the server needs or shorter than it on a slow machine.
        assert wait_for_endpoint(self.endpoint), f"no server on {self.endpoint}"
        return self

    def __exit__(self, *_: object) -> None:
        # Stopped the moment the test is done rather than left to run out its
        # span. Fifty tests each leaving a server alive for its full duration is
        # fifty servers at once, and the overlap made unrelated tests fail.
        self._stop.set()
        self._thread.join(timeout=10.0)


async def read_axis(endpoint: str, credentials: Credentials, pki_dir: Path) -> float:
    """One value, through whatever `credentials` describe. The real client path."""
    from asyncua import Client

    from pssim.io.opcua_security import configure

    client = Client(url=endpoint)
    await configure(client, credentials, pki_dir)
    async with client:
        return float(await client.get_node(AXIS_NODE).read_value())


def value_through(endpoint: str, credentials: Credentials, pki_dir: Path) -> float:
    return asyncio.run(read_axis(endpoint, credentials, pki_dir))


def wait_for(predicate: object, timeout_s: float = 10.0) -> bool:
    """Poll until true or the deadline. The condition is a connection attempt on
    another thread, so there is nothing to await."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def pki(tmp_path: Path) -> Iterator[Path]:
    """A throwaway certificate store. The real one is the user's install."""
    directory = tmp_path / "pki"
    directory.mkdir()
    yield directory


class TestDiscovery:
    def test_an_open_server_offers_one_way_in(self) -> None:
        with SecureMockServer(48410, MockSecurity()) as server:
            offers = discover_endpoints(server.endpoint)

        assert [offer.label for offer in offers] == [f"{POLICY_NONE} / {SecurityMode.NONE.value}"]

    def test_an_open_server_welcomes_anonymous(self) -> None:
        with SecureMockServer(48411, MockSecurity()) as server:
            offers = discover_endpoints(server.endpoint)

        assert offers[0].accepts(TokenType.ANONYMOUS)

    def test_a_secure_server_offers_two(self) -> None:
        with SecureMockServer(48412, MockSecurity(is_secure=True)) as server:
            offers = discover_endpoints(server.endpoint)

        assert {offer.policy_name for offer in offers} == {POLICY_NONE, "Basic256Sha256"}

    def test_the_strongest_offer_comes_first(self) -> None:
        # The server's own SecurityLevel decides, not our own ranking.
        with SecureMockServer(48413, MockSecurity(is_secure=True)) as server:
            offers = discover_endpoints(server.endpoint)

        assert offers[0].security_level >= offers[-1].security_level

    def test_a_secure_offer_carries_the_server_certificate(self) -> None:
        with SecureMockServer(48414, MockSecurity(is_secure=True)) as server:
            offers = discover_endpoints(server.endpoint)

        secure = next(offer for offer in offers if offer.policy_name == "Basic256Sha256")
        assert secure.has_server_certificate is True

    def test_a_server_demanding_a_user_does_not_offer_anonymous(self) -> None:
        # This is the failure that used to arrive with no explanation.
        with SecureMockServer(48415, MockSecurity(username=USER, password=PASSWORD)) as server:
            offers = discover_endpoints(server.endpoint)

        assert not offers[0].accepts(TokenType.ANONYMOUS)
        assert offers[0].accepts(TokenType.USERNAME)

    def test_the_server_names_itself(self) -> None:
        with SecureMockServer(48416, MockSecurity()) as server:
            offers = discover_endpoints(server.endpoint)

        assert offers[0].server_name == "PSsimTool Mock PLC"

    def test_a_server_that_is_not_there_is_a_typed_error(self) -> None:
        with pytest.raises(DataSourceError):
            discover_endpoints("opc.tcp://127.0.0.1:1/nothing/", timeout_s=2.0)


class TestConnectingSecurely:
    def test_a_generated_certificate_is_enough(self, pki: Path) -> None:
        # No certificate is asked of the user: one is generated and reused, which
        # is what UaExpert does on first run.
        credentials = Credentials(policy_name="Basic256Sha256", mode=SecurityMode.SIGN_AND_ENCRYPT)

        with SecureMockServer(48420, MockSecurity(is_secure=True)) as server:
            value = value_through(server.endpoint, credentials, pki)

        assert value == pytest.approx(1250.0, abs=1500.0)  # somewhere on its stroke

    def test_the_certificate_is_written_once_and_reused(self, pki: Path) -> None:
        credentials = Credentials(policy_name="Basic256Sha256", mode=SecurityMode.SIGN_AND_ENCRYPT)

        with SecureMockServer(48421, MockSecurity(is_secure=True)) as server:
            value_through(server.endpoint, credentials, pki)
            first = sorted(path.name for path in pki.iterdir())
            value_through(server.endpoint, credentials, pki)

        assert first == sorted(path.name for path in pki.iterdir())
        assert len(first) == 2

    def test_no_security_still_works(self, pki: Path) -> None:
        # The open path must not regress because the secure one arrived.
        with SecureMockServer(48422, MockSecurity()) as server:
            value = value_through(server.endpoint, Credentials(), pki)

        assert isinstance(value, float)


class TestAuthentication:
    def test_the_right_password_gets_in(self, pki: Path) -> None:
        credentials = Credentials(token=TokenType.USERNAME, username=USER, password=PASSWORD)

        with SecureMockServer(48430, MockSecurity(username=USER, password=PASSWORD)) as server:
            value = value_through(server.endpoint, credentials, pki)

        assert isinstance(value, float)

    def test_a_wrong_password_is_refused(self, pki: Path) -> None:
        credentials = Credentials(token=TokenType.USERNAME, username=USER, password="nope")

        with (
            SecureMockServer(48431, MockSecurity(username=USER, password=PASSWORD)) as server,
            pytest.raises(UaStatusCodeError) as raised,
        ):
            value_through(server.endpoint, credentials, pki)

        # The status code is the whole answer to "why not", and it is what the
        # diagnostics have to carry.
        assert "AccessDenied" in str(raised.value)

    def test_anonymous_is_refused_where_it_is_not_offered(self, pki: Path) -> None:
        with (
            SecureMockServer(48432, MockSecurity(username=USER, password=PASSWORD)) as server,
            pytest.raises(UaStatusCodeError),
        ):
            value_through(server.endpoint, Credentials(), pki)

    def test_security_and_a_user_together(self, pki: Path) -> None:
        # Both at once is what a commissioned machine actually looks like.
        credentials = Credentials(
            policy_name="Basic256Sha256",
            mode=SecurityMode.SIGN_AND_ENCRYPT,
            token=TokenType.USERNAME,
            username=USER,
            password=PASSWORD,
        )
        security = MockSecurity(is_secure=True, username=USER, password=PASSWORD)

        with SecureMockServer(48433, security) as server:
            value = value_through(server.endpoint, credentials, pki)

        assert isinstance(value, float)


class TestDiagnostics:
    """What the source records about its own attempt.

    `SourceStatus.DISCONNECTED` is true of a refused password, an absent server
    and a certificate we could not produce. These are the tests that the three
    are told apart.
    """

    def _source(self, endpoint: str, credentials: Credentials) -> OpcUaSource:
        return OpcUaSource(
            OpcUaConfig(
                endpoint=endpoint,
                bindings=(JointBinding(joint_name="axis_x", node_id=AXIS_NODE, scale=0.001),),
                credentials=credentials,
            )
        )

    def test_a_good_connection_records_every_step(self) -> None:
        with SecureMockServer(48440, MockSecurity()) as server:
            source = self._source(server.endpoint, Credentials())
            source.start()
            try:
                assert wait_for(lambda: source.status is SourceStatus.CONNECTED)
            finally:
                source.stop()

        steps = [entry.step for entry in source.diagnostics.entries]
        assert DiagnosticStep.SESSION in steps
        assert DiagnosticStep.SUBSCRIBE in steps

    def test_a_good_connection_reports_no_error(self) -> None:
        with SecureMockServer(48441, MockSecurity()) as server:
            source = self._source(server.endpoint, Credentials())
            source.start()
            try:
                assert wait_for(lambda: source.status is SourceStatus.CONNECTED)
                assert source.last_error is None
            finally:
                source.stop()

    def test_no_security_records_the_certificate_as_skipped(self) -> None:
        # "Nothing to do" is worth showing: it is how a reader knows the step was
        # reached rather than never attempted.
        with SecureMockServer(48442, MockSecurity()) as server:
            source = self._source(server.endpoint, Credentials())
            source.start()
            try:
                assert wait_for(lambda: source.status is SourceStatus.CONNECTED)
            finally:
                source.stop()

        certificate = next(
            entry
            for entry in source.diagnostics.entries
            if entry.step is DiagnosticStep.CERTIFICATE
        )
        assert certificate.outcome is Outcome.SKIPPED

    def test_a_refused_password_says_so(self) -> None:
        # The whole point of this stage: before it, this was "Disconnected".
        credentials = Credentials(token=TokenType.USERNAME, username=USER, password="nope")

        with SecureMockServer(48443, MockSecurity(username=USER, password=PASSWORD)) as server:
            source = self._source(server.endpoint, credentials)
            source.start()
            try:
                assert wait_for(lambda: source.last_error is not None)
            finally:
                source.stop()

        assert source.last_error is not None
        assert "AccessDenied" in source.last_error

    def test_the_failing_step_is_the_session(self) -> None:
        credentials = Credentials(token=TokenType.USERNAME, username=USER, password="nope")

        with SecureMockServer(48444, MockSecurity(username=USER, password=PASSWORD)) as server:
            source = self._source(server.endpoint, credentials)
            source.start()
            try:
                assert wait_for(lambda: source.diagnostics.last_failure is not None)
            finally:
                source.stop()

        failure = source.diagnostics.last_failure
        assert failure is not None
        assert failure.step is DiagnosticStep.SESSION

    def test_a_server_that_is_not_there_fails_differently(self) -> None:
        # Same status, different diagnosis — and no status code, because nothing
        # answered to give one.
        source = self._source("opc.tcp://127.0.0.1:1/nothing/", Credentials())
        source.start()
        try:
            assert wait_for(lambda: source.diagnostics.last_failure is not None, timeout_s=15.0)
        finally:
            source.stop()

        failure = source.diagnostics.last_failure
        assert failure is not None
        assert failure.status_code == ""

    def test_a_secure_connection_records_its_certificate(self) -> None:
        credentials = Credentials(policy_name="Basic256Sha256", mode=SecurityMode.SIGN_AND_ENCRYPT)

        with SecureMockServer(48445, MockSecurity(is_secure=True)) as server:
            source = self._source(server.endpoint, credentials)
            source.start()
            try:
                assert wait_for(lambda: source.status is SourceStatus.CONNECTED)
            finally:
                source.stop()

        certificate = next(
            entry
            for entry in source.diagnostics.entries
            if entry.step is DiagnosticStep.CERTIFICATE
        )
        assert certificate.outcome is Outcome.OK
        assert "Basic256Sha256" in certificate.detail


@pytest.fixture(scope="module")
def probe_server() -> Iterator[SecureMockServer]:
    """One server for the whole probe class: the command only reads, and starting
    five of them to run five assertions is what made this suite slow."""
    security = MockSecurity(is_secure=True, username=USER, password=PASSWORD)
    with SecureMockServer(48460, security, duration_s=120.0) as running:
        yield running


def run_probe(endpoint: str, *arguments: str, password: str = "") -> str:
    """`pssim probe` in a subprocess, which is the way a user runs it.

    Not through typer's `CliRunner`: it swaps `sys.stdout` for the duration of a
    call, structlog binds to whatever it finds on its first write, and every
    later line from a background thread then hits a closed file. A subprocess has
    none of that, and it exercises the real entry point.
    """
    environment = dict(os.environ)
    environment[PASSWORD_ENV] = password
    finished = subprocess.run(
        [sys.executable, "-m", "pssim.cli", "probe", endpoint, *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )
    # Both halves: the offers go to stdout, the diagnostics to stderr, and the
    # diagnostics are the half worth reading when the connection failed.
    return finished.stdout + finished.stderr


class TestTheProbeCommand:
    """`pssim probe` is the connection dialog's first tab on the command line."""

    def test_it_prints_what_the_server_offers(self, probe_server: SecureMockServer) -> None:
        # Before trying anything: a server that will refuse still says what it wants.
        assert "Basic256Sha256 / SignAndEncrypt" in run_probe(probe_server.endpoint)

    def test_it_says_which_tokens_are_accepted(self, probe_server: SecureMockServer) -> None:
        output = run_probe(probe_server.endpoint)

        assert TokenType.USERNAME.value in output
        assert TokenType.ANONYMOUS.value not in output

    def test_an_anonymous_attempt_is_refused_and_says_so(
        self, probe_server: SecureMockServer
    ) -> None:
        # The failure this whole wave exists for, on the command line.
        output = run_probe(probe_server.endpoint)

        assert "BadIdentityTokenRejected" in output

    def test_a_secure_authenticated_probe_lists_the_nodes(
        self, probe_server: SecureMockServer
    ) -> None:
        output = run_probe(
            probe_server.endpoint,
            "--policy",
            "Basic256Sha256",
            "--user",
            USER,
            "--browse",
            f"ns={NAMESPACE_INDEX};i=1",
            password=PASSWORD,
        )

        assert AXIS_NODE in output

    def test_a_wrong_password_says_which_step_refused_it(
        self, probe_server: SecureMockServer
    ) -> None:
        output = run_probe(
            probe_server.endpoint,
            "--policy",
            "Basic256Sha256",
            "--user",
            USER,
            password="not-the-password",
        )

        assert "BadUserAccessDenied" in output

    def test_it_looks_inside_a_struct(self, probe_server: SecureMockServer) -> None:
        output = self._authenticated(probe_server, "--browse", STRUCT_NODE)

        assert "Position" in output

    def test_it_looks_inside_an_array_within_a_struct(self, probe_server: SecureMockServer) -> None:
        # **Across processes**, which is the point of running probe as a
        # subprocess for this one. An array inside a struct needs the server's
        # type definitions to decode the value it is counting; in one process the
        # mock server has already registered the same generated classes on the
        # process-wide `ua` module, so the same test in-process passes whether or
        # not the session ever loads them.
        output = self._authenticated(probe_server, "--browse", STRUCT_NODE, "--path", "Limits")

        assert "Limits[1]" in output

    def test_and_inside_a_nested_struct(self, probe_server: SecureMockServer) -> None:
        output = self._authenticated(probe_server, "--browse", STRUCT_NODE, "--path", "Position")

        assert "Position.X" in output

    def _authenticated(self, server: SecureMockServer, *arguments: str) -> str:
        return run_probe(
            server.endpoint,
            "--policy",
            "Basic256Sha256",
            "--user",
            USER,
            *arguments,
            password=PASSWORD,
        )

    def test_a_writable_node_is_marked(self, probe_server: SecureMockServer) -> None:
        output = run_probe(
            probe_server.endpoint,
            "--policy",
            "Basic256Sha256",
            "--user",
            USER,
            "--browse",
            f"ns={NAMESPACE_INDEX};i=2",
            password=PASSWORD,
        )

        assert "[w]" in output

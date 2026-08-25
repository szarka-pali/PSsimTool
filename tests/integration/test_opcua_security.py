"""Security and authentication, against `pssim mock-server` and nothing else.

These are the paths a client that has only ever met an open server has never
taken: a real policy, a real user check, and the two refusals — wrong password,
and anonymous where anonymous is not offered.

The mock server is started per test with the security it is being asked about.
Never against a real machine (`.claude/rules/io-opcua.md`).

Run with: ``uv run pytest -m integration``
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from asyncua.ua.uaerrors import UaStatusCodeError

from pssim.domain.errors import DataSourceError
from pssim.io.mock_server import MockSecurity, run_mock_server
from pssim.io.opcua_security import (
    POLICY_NONE,
    Credentials,
    SecurityMode,
    TokenType,
    discover_endpoints,
)

pytestmark = pytest.mark.integration

NAMESPACE_INDEX = 2
AXIS_NODE = f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos"

USER = "operator"
PASSWORD = "letmein"


class SecureMockServer:
    """The mock server with a given security, in its own thread.

    Its own port per instance, so tests that run one after another never meet a
    socket the previous one has not let go of yet.
    """

    def __init__(self, port: int, security: MockSecurity, duration_s: float = 12.0) -> None:
        self.endpoint = f"opc.tcp://127.0.0.1:{port}/pssim-security/"
        self._security = security
        self._duration_s = duration_s
        self._thread = threading.Thread(target=self._run, name="mock-secure", daemon=True)

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

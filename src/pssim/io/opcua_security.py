"""What an OPC UA server will accept, and what we answer with.

A PLC decides how it may be talked to: which security policy, signed or encrypted,
anonymous or with a user. Until now this application could only ask one way — no
security, no user — so a server that wanted anything refused it with nothing on
screen to say why.

Two halves live here:

- **What the server offers.** `discover_endpoints` asks, without opening a session,
  and returns one `EndpointOffer` per way in. That is the first step of the flow:
  type an endpoint, see what it wants, pick one.
- **What we answer with.** `Credentials` is that choice plus the user's name and
  password, and `configure` is the single place an asyncua `Client` is set up from
  it.

`ui/` never imports asyncua. Everything here is spelled in this project's own
types, so a policy name in the settings file is a string this module defines
rather than a value from a library that may rename it.

Three things about the asyncua API that are **not** guessable and were verified
against `pssim mock-server` before this was written:

1. `Client.setup_self_signed_certificate(key_file, cert_file)` returns
   `(certificate, key)` — the **reverse** of its argument order. Getting it wrong
   fails later, inside `load_certificate`, with a message about PEM delimiters.
2. `set_security` does **not** need the server's certificate. asyncua fetches it
   from the endpoint during connect. Passing it is optional, so `Credentials` does
   not have to carry it.
3. A generated certificate identifies the application by `Client.application_uri`,
   which defaults to asyncua's own. Ours is set before generating, or every
   certificate this application makes claims to be opcua-asyncio.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pssim.domain.errors import DataSourceError
from pssim.observability import get_logger

logger = get_logger(__name__)

#: How long discovery may take, connection included. Generous, because a slow
#: server is one the user is still waiting on — but never unbounded.
DEFAULT_DISCOVERY_TIMEOUT_S: Final = 15.0

#: How this application names itself in the certificate it generates. A server
#: that checks the application uri against the certificate needs them to agree.
APPLICATION_URI: Final = "urn:pssim:PSsimTool"


class SecurityMode(StrEnum):
    """How much protection the channel has.

    Spelled as OPC UA spells it, and deliberately not asyncua's
    `ua.MessageSecurityMode` — that has an `Invalid` member and calls the first
    one `None_`, neither of which belongs in a settings file.
    """

    NONE = "None"
    SIGN = "Sign"
    SIGN_AND_ENCRYPT = "SignAndEncrypt"


class TokenType(StrEnum):
    """How the session proves who it is."""

    ANONYMOUS = "Anonymous"
    USERNAME = "UserName"
    CERTIFICATE = "Certificate"
    ISSUED = "IssuedToken"


@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    """One policy, by the name a datasheet uses and the URI the wire uses.

    The two are not the same string for every policy — `Aes128Sha256RsaOaep` is
    `#Aes128_Sha256_RsaOaep` on the wire — so both are stated rather than one
    derived from the other.
    """

    name: str
    uri: str

    @property
    def needs_certificate(self) -> bool:
        """Whether using it means having a client certificate. Everything but
        `None` does."""
        return self.name != POLICY_NONE


POLICY_NONE: Final = "None"

_POLICY_URI_PREFIX: Final = "http://opcfoundation.org/UA/SecurityPolicy#"

#: Every policy asyncua implements, weakest first. Ordered so the list reads the
#: way a server's own list usually does; discovery sorts by what the server says
#: rather than by this.
POLICIES: Final[tuple[SecurityPolicy, ...]] = (
    SecurityPolicy(POLICY_NONE, f"{_POLICY_URI_PREFIX}None"),
    SecurityPolicy("Basic128Rsa15", f"{_POLICY_URI_PREFIX}Basic128Rsa15"),
    SecurityPolicy("Basic256", f"{_POLICY_URI_PREFIX}Basic256"),
    SecurityPolicy("Basic256Sha256", f"{_POLICY_URI_PREFIX}Basic256Sha256"),
    SecurityPolicy("Aes128Sha256RsaOaep", f"{_POLICY_URI_PREFIX}Aes128_Sha256_RsaOaep"),
    SecurityPolicy("Aes256Sha256RsaPss", f"{_POLICY_URI_PREFIX}Aes256_Sha256_RsaPss"),
)

POLICY_NAMES: Final[tuple[str, ...]] = tuple(policy.name for policy in POLICIES)


def policy_by_name(name: str) -> SecurityPolicy | None:
    """The policy with this name, or `None` for one we cannot speak.

    `None` rather than an exception: a settings file may name a policy this build
    does not implement, and defaulting to no security is a better answer than
    refusing to start.
    """
    for policy in POLICIES:
        if policy.name == name:
            return policy
    return None


def policy_name_for_uri(uri: str) -> str:
    """The friendly name for a policy URI a server reported.

    An unknown URI keeps whatever follows the `#`. A server may offer a policy
    newer than this build, and showing its real name is more use than hiding it.
    """
    for policy in POLICIES:
        if policy.uri == uri:
            return policy.name
    return uri.rsplit("#", 1)[-1] if "#" in uri else uri


@dataclass(frozen=True, slots=True)
class EndpointOffer:
    """One way into a server, exactly as the server described it."""

    endpoint_url: str
    policy_name: str
    mode: SecurityMode
    security_level: int
    """The server's own opinion of how strong this is. Higher is stronger; it is
    what makes "offer the best one first" possible without ranking policies
    ourselves."""

    token_types: tuple[TokenType, ...]
    """What the session may prove itself with. A server that does not list
    `Anonymous` will refuse an anonymous session — which is exactly the failure
    that used to arrive with no explanation."""

    server_name: str = ""
    server_uri: str = ""
    has_server_certificate: bool = False

    @property
    def needs_certificate(self) -> bool:
        return self.mode is not SecurityMode.NONE

    @property
    def label(self) -> str:
        """`Basic256Sha256 / SignAndEncrypt` — how the offer reads in a list."""
        return f"{self.policy_name} / {self.mode.value}"

    def accepts(self, token: TokenType) -> bool:
        return token in self.token_types


def discover_endpoints(
    endpoint: str, *, timeout_s: float = DEFAULT_DISCOVERY_TIMEOUT_S
) -> tuple[EndpointOffer, ...]:
    """Ask a server how it may be talked to. Opens no session.

    Strongest first, by the server's own `SecurityLevel`, so the default choice
    is the one the server would prefer rather than the one that happens to be
    listed first.

    Raises `DataSourceError` when the server cannot be reached — discovery is a
    question with an answer, unlike a subscription dropping, which is a normal
    state to retry (R12).
    """
    if not endpoint:
        raise DataSourceError("endpoint must not be empty")
    try:
        return asyncio.run(_discover(endpoint, timeout_s=timeout_s))
    except DataSourceError:
        raise
    except TimeoutError as exc:
        raise DataSourceError(f"{endpoint} did not answer within {timeout_s:g} s") from exc
    except Exception as exc:  # asyncua raises a wide family of its own
        raise DataSourceError(f"could not reach {endpoint}: {exc}") from exc


async def _discover(endpoint: str, *, timeout_s: float) -> tuple[EndpointOffer, ...]:
    """The deadline covers the connection, not only the request.

    A host that accepts a TCP connection and then says nothing is the case this
    has to survive; asyncua's own connect timeout is not this one.
    """
    return await asyncio.wait_for(_ask_for_endpoints(endpoint), timeout=timeout_s)


async def _ask_for_endpoints(endpoint: str) -> tuple[EndpointOffer, ...]:
    from asyncua import Client

    client = Client(url=endpoint)
    client.application_uri = APPLICATION_URI
    described = await client.connect_and_get_server_endpoints()
    offers = tuple(_offer_from(description) for description in described)
    return tuple(sorted(offers, key=lambda offer: -offer.security_level))


def _offer_from(description: Any) -> EndpointOffer:
    """One `ua.EndpointDescription` in this project's own words."""
    application = description.Server
    name = application.ApplicationName.Text if application.ApplicationName else ""
    return EndpointOffer(
        endpoint_url=description.EndpointUrl,
        policy_name=policy_name_for_uri(description.SecurityPolicyUri),
        mode=_mode_from(description.SecurityMode),
        security_level=int(description.SecurityLevel),
        token_types=tuple(
            _token_from(token.TokenType) for token in description.UserIdentityTokens or ()
        ),
        server_name=name or "",
        server_uri=application.ApplicationUri or "",
        has_server_certificate=bool(description.ServerCertificate),
    )


def _mode_from(mode: Any) -> SecurityMode:
    """asyncua's `MessageSecurityMode` in ours. Its first member is `None_`, and
    `Invalid` has no counterpart here — an unusable endpoint reads as `None`,
    which is what a chooser can do something with."""
    name = getattr(mode, "name", "")
    if name == "SignAndEncrypt":
        return SecurityMode.SIGN_AND_ENCRYPT
    if name == "Sign":
        return SecurityMode.SIGN
    return SecurityMode.NONE


def _token_from(token_type: Any) -> TokenType:
    name = getattr(token_type, "name", "")
    for candidate in TokenType:
        if candidate.value == name:
            return candidate
    return TokenType.ANONYMOUS


@dataclass(frozen=True, slots=True)
class Credentials:
    """Everything needed to open a session, including the secret.

    The password is **never persisted** — `ui/settings.py` has no field for it,
    so there is nowhere for it to be written by accident. It is typed once per
    session, or comes from `PSSIM_OPCUA_PASSWORD` for an unattended run.

    `repr=False` on it is not decoration: a frozen dataclass prints every field,
    and this object ends up in log lines and tracebacks.
    """

    policy_name: str = POLICY_NONE
    mode: SecurityMode = SecurityMode.NONE
    token: TokenType = TokenType.ANONYMOUS
    username: str = ""
    password: str = field(default="", repr=False)
    certificate_path: str = ""
    key_path: str = ""
    """Where our own certificate and key live. Empty means "generate a pair and
    reuse it", which is what UaExpert does on first run."""

    @property
    def is_secure(self) -> bool:
        return self.mode is not SecurityMode.NONE

    @property
    def needs_certificate(self) -> bool:
        return self.is_secure

    def describe(self) -> str:
        """One line for a status bar or a diagnostic entry. Never the password."""
        security = f"{self.policy_name} / {self.mode.value}"
        if self.token is TokenType.USERNAME and self.username:
            return f"{security}, user {self.username!r}"
        return f"{security}, anonymous"


def client_pki_dir() -> Path:
    """Where the generated certificate and key are kept.

    Beside the application's own settings rather than in the project folder: it
    identifies *this installation*, not the scene, and a project handed to a
    colleague must not carry a key with it.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "PSsimTool" / "pki"


async def configure(client: Any, credentials: Credentials, pki_dir: Path | None = None) -> str:
    """Set a `Client` up from `credentials`. Returns what was applied, for the log.

    The one place security and authentication reach asyncua. Called from the
    thread that owns the client's loop — both `set_security` and
    `setup_self_signed_certificate` are coroutines.
    """
    client.application_uri = APPLICATION_URI

    if credentials.token is TokenType.USERNAME and credentials.username:
        client.set_user(credentials.username)
        if credentials.password:
            client.set_password(credentials.password)

    if not credentials.is_secure:
        return credentials.describe()

    policy = policy_by_name(credentials.policy_name)
    if policy is None:
        raise DataSourceError(f"unknown security policy {credentials.policy_name!r}")

    certificate, key = await _certificate_pair(client, credentials, pki_dir)
    from asyncua import ua

    # The server's own certificate is deliberately not passed: asyncua fetches it
    # from the endpoint while connecting. Verified against the mock server.
    await client.set_security(
        _policy_class(policy.name),
        str(certificate),
        str(key),
        mode=(
            ua.MessageSecurityMode.Sign
            if credentials.mode is SecurityMode.SIGN
            else ua.MessageSecurityMode.SignAndEncrypt
        ),
    )
    return f"{credentials.describe()}, certificate {certificate.name}"


async def _certificate_pair(
    client: Any, credentials: Credentials, pki_dir: Path | None
) -> tuple[Path, Path]:
    """The certificate and key to present, generating a pair if none was given.

    Generated once and reused: asyncua rebuilds only what is missing or expired,
    so a second call on the same paths is free.
    """
    if credentials.certificate_path and credentials.key_path:
        return Path(credentials.certificate_path), Path(credentials.key_path)

    directory = pki_dir if pki_dir is not None else client_pki_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # NOTE the order: it takes (key, cert) and returns (cert, key).
    certificate, key = await client.setup_self_signed_certificate(
        directory / "pssim_key.pem",
        directory / "pssim_cert.der",
        subject_attrs={"organizationName": "PSsimTool", "commonName": "PSsimTool"},
    )
    logger.info("client certificate ready", certificate=str(certificate))
    return certificate, key


def _policy_class(name: str) -> Any:
    """asyncua's policy class for one of our names.

    Looked up by attribute rather than imported one by one: the module names them
    `SecurityPolicy<Name>`, and a build of asyncua without one should fail here
    with a clear message rather than at import time.
    """
    from asyncua.crypto import security_policies

    policy_class = getattr(security_policies, f"SecurityPolicy{name}", None)
    if policy_class is None:
        raise DataSourceError(f"this build of asyncua cannot speak {name!r}")
    return policy_class

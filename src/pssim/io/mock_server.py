"""A simulated OPC UA server — development and tests without a PLC.

Without it nothing can be developed until the hardware is on the desk, and the
integration tests would have nothing to run against. Writing to OPC UA is tested
**exclusively** against this server, never against a real machine.

The server generates values **in PLC units** (mm, degrees), not in internal ones —
otherwise the conversion in `JointBinding` would go untested.

It can also **refuse** a connection, which matters as much as accepting one: a
client that only ever met an open server is a client nobody has tested against a
real PLC. `MockSecurity` turns on a real security policy and a real user check,
and the failure paths — wrong password, anonymous where anonymous is not
offered — are the ones the diagnostics have to explain.

Run it with: ``uv run pssim mock-server``, or
``uv run pssim mock-server --secure --require-user operator:letmein``.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pssim.observability import get_logger

logger = get_logger(__name__)

DEFAULT_ENDPOINT: Final = "opc.tcp://0.0.0.0:4840/pssim/"
DEFAULT_NAMESPACE: Final = "http://pssim.local/mock"


@dataclass(frozen=True, slots=True)
class MockAxis:
    """A simulated axis. `amplitude` and `center` are in PLC units, not in metres."""

    name: str
    amplitude: float
    center: float = 0.0
    period_s: float = 8.0
    phase_rad: float = 0.0

    def value_at(self, t_s: float) -> float:
        """Sinusoidal motion. A pure function — tested without the server."""
        angle = 2.0 * math.pi * t_s / self.period_s + self.phase_rad
        return self.center + self.amplitude * math.sin(angle)


#: The application uri the server claims and its certificate is issued for. They
#: have to agree, or a client that checks warns and may refuse.
MOCK_APPLICATION_URI: Final = "urn:pssim:mock"


@dataclass(frozen=True, slots=True)
class MockSecurity:
    """What the server demands of a client. Everything off is the old behaviour.

    A frozen bundle rather than three more parameters: `run_mock_server` was
    already at the argument limit `.claude/rules/code-style.md` sets.
    """

    is_secure: bool = False
    """Offer `Basic256Sha256/SignAndEncrypt` beside `NoSecurity`, with a
    certificate generated on the spot."""

    username: str = ""
    password: str = ""
    """When a username is given, anonymous sessions are not offered at all and
    anything but this pair is refused with `BadUserAccessDenied`."""

    pki_dir: Path | None = None
    """Where the server's own certificate goes. A temp directory when unset —
    a development server's key is not worth keeping."""

    @property
    def requires_user(self) -> bool:
        return bool(self.username)


#: An open server: no security, anonymous welcome. What `pssim mock-server` has
#: always been, and the default so nothing existing changes.
OPEN: Final = MockSecurity()


#: Writable nodes the simulation may publish into — a sensor's reading on its way
#: back to the PLC. Separate from the axes because the direction is the opposite
#: one, and because these are the only nodes on any server this project is ever
#: allowed to write to (see `.claude/rules/io-opcua.md`).
DEFAULT_OUTPUTS: Final = ("Sim.Sensor1", "Sim.Sensor2")


#: Axes matching `machines/example.yaml`. Values in mm and in thousandths of a degree,
#: that is, exactly as a servo typically sends them.
DEFAULT_AXES: Final = (
    MockAxis(name="Axes.X.ActPos", amplitude=1200.0, center=1250.0, period_s=8.0),
    MockAxis(name="Axes.Z.ActPos", amplitude=350.0, center=400.0, period_s=5.0, phase_rad=1.2),
    MockAxis(name="Axes.C.ActPos", amplitude=90_000.0, center=0.0, period_s=11.0),
)


#: The structured nodes: a struct, a nested struct inside it, an array inside
#: it, and a bare array beside it. On by default, because a mock server made only
#: of scalars is what let a client ship that stops browsing at a variable — a real
#: PLC's address space is mostly structures.
#:
#: `Struct.AxisState.Position` tracks the three axes, so binding a path into the
#: struct and binding the plain axis node must give the same number. That is the
#: test that pins the extraction.
DEFAULT_STRUCTS: Final = (
    "Struct.AxisState",
    "Struct.Point",
    "Struct.Positions",
)

#: The struct type names. Prefixed, because `load_data_type_definitions` generates
#: these as classes on the `ua` module itself — a process-wide namespace shared
#: with whatever else asyncua has loaded.
POINT_TYPE_NAME: Final = "PSsimPoint3D"
AXIS_STATE_TYPE_NAME: Final = "PSsimAxisState"


async def run_mock_server(
    endpoint: str = DEFAULT_ENDPOINT,
    axes: tuple[MockAxis, ...] = DEFAULT_AXES,
    *,
    update_interval_s: float = 0.05,
    duration_s: float | None = None,
    outputs: tuple[str, ...] = DEFAULT_OUTPUTS,
    security: MockSecurity = OPEN,
    stop_event: threading.Event | None = None,
    with_structs: bool = True,
) -> None:
    """Run the mock server. `duration_s=None` means run until interrupted.

    `stop_event` is how a test ends it the moment it is done, which is what keeps
    a suite of fifty from running fifty servers at once — `duration_s` alone
    leaves each one alive for its full span whatever the test is doing, and the
    overlap is enough to make unrelated tests fail. `duration_s` remains as the
    backstop for a harness that dies without setting the event.

    The axis nodes are read-only, exactly as a servo's actual position is. The
    `outputs` are writable, and are the only nodes anywhere this project writes
    to — the write path is tested here and nowhere else.

    `security` decides what a client must present. Left alone it is the open
    server this has always been.

    `with_structs` adds the structured nodes. On by default: a client that has
    only ever met scalars is a client nobody has tested against a real PLC, whose
    address space is mostly structures.
    """
    from asyncua import Server, ua  # a heavy import - only when actually needed

    server = Server(user_manager=_user_manager(security))
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("PSsimTool Mock PLC")
    # Awaited on purpose: `set_application_uri` is a coroutine, and calling it
    # without awaiting leaves the uri unset — the certificate then does not match
    # and every client warns about it.
    await server.set_application_uri(MOCK_APPLICATION_URI)
    await _apply_security(server, security)

    namespace_index = await server.register_namespace(DEFAULT_NAMESPACE)
    folder = await server.nodes.objects.add_folder(namespace_index, "Axes")

    variables = {}
    node_ids = [f"ns={namespace_index};s={axis.name}" for axis in axes]
    for axis, node_id in zip(axes, node_ids, strict=True):
        variable = await folder.add_variable(
            node_id,
            axis.name,
            axis.value_at(0.0),
            ua.VariantType.Double,
        )
        await variable.set_writable(False)
        variables[axis] = variable

    output_folder = await server.nodes.objects.add_folder(namespace_index, "Sim")
    output_ids = [f"ns={namespace_index};s={name}" for name in outputs]
    for name, node_id in zip(outputs, output_ids, strict=True):
        node = await output_folder.add_variable(node_id, name, 0.0, ua.VariantType.Double)
        await node.set_writable(True)

    structs = await _add_structs(server, namespace_index, axes) if with_structs else None

    logger.info(
        "mock server running",
        endpoint=endpoint,
        nodes=node_ids,
        writable=output_ids,
        structs=list(DEFAULT_STRUCTS) if structs is not None else [],
    )

    async with server:
        elapsed = 0.0
        while duration_s is None or elapsed < duration_s:
            if stop_event is not None and stop_event.is_set():
                logger.info("mock server stopping on request", endpoint=endpoint)
                return
            for axis, variable in variables.items():
                await variable.write_value(axis.value_at(elapsed))
            if structs is not None:
                await structs.update(elapsed)
            await asyncio.sleep(update_interval_s)
            elapsed += update_interval_s


class _Structs:
    """The structured nodes, and the one call that keeps them moving.

    A small class rather than a tuple of nodes: the point of these is that the
    struct's `Position` and the bare array follow the same axes as the scalar
    nodes, so a path into the struct and the plain node can be compared. That is
    two nodes and three axes to keep in step, which is a thing with state.
    """

    def __init__(self, axes: tuple[MockAxis, ...], state_node: Any, array_node: Any) -> None:
        self._axes = axes
        self._state = state_node
        self._array = array_node

    async def update(self, elapsed_s: float) -> None:
        from asyncua import ua

        values = [axis.value_at(elapsed_s) for axis in self._axes]
        point = _point(ua, values)
        state = getattr(ua, AXIS_STATE_TYPE_NAME)(
            Position=point,
            Enabled=values[0] > 0.0,
            Name="X",
            Limits=[0.0, 2450.0],
        )
        await self._state.write_value(state)
        await self._array.write_value(values)


def _point(ua: Any, values: list[float]) -> Any:
    """A `Point3D` from the first three axis values, whatever there are of them."""
    padded = (values + [0.0, 0.0, 0.0])[:3]
    return getattr(ua, POINT_TYPE_NAME)(X=padded[0], Y=padded[1], Z=padded[2])


async def _add_structs(server: Any, namespace_index: int, axes: tuple[MockAxis, ...]) -> _Structs:
    """A struct, a nested struct, an array inside it, and a bare array.

    `load_data_type_definitions` is called on the **server** as well as by the
    client: without it there is no `ua.PSsimAxisState` class to build a value
    from. It generates classes onto the `ua` module, which is process-wide — two
    mock servers in one process regenerate the same names, which is harmless
    because the definitions are identical.
    """
    from asyncua import ua
    from asyncua.common.structures104 import new_struct, new_struct_field

    # The `type: ignore` on each name below: `new_struct` annotates it
    # `int | ua.QualifiedName`, which is wrong. It hands the value straight to
    # `create_data_type`, whose own signature is `ua.QualifiedName | str` — so a
    # plain string is what it wants, and what the spike against a live server
    # used.

    point_type, _ = await new_struct(
        server,
        namespace_index,
        POINT_TYPE_NAME,  # type: ignore[arg-type]
        [
            new_struct_field("X", ua.VariantType.Double),
            new_struct_field("Y", ua.VariantType.Double),
            new_struct_field("Z", ua.VariantType.Double),
        ],
    )
    state_type, _ = await new_struct(
        server,
        namespace_index,
        AXIS_STATE_TYPE_NAME,  # type: ignore[arg-type]
        [
            # A struct inside a struct: the case a one-level-deep implementation
            # gets wrong and nobody notices until a real PLC.
            new_struct_field("Position", point_type),
            new_struct_field("Enabled", ua.VariantType.Boolean),
            # Deliberately not a number. Selecting it must be refused, not scaled.
            new_struct_field("Name", ua.VariantType.String),
            new_struct_field("Limits", ua.VariantType.Double, array=True),
        ],
    )
    await server.load_data_type_definitions()

    folder = await server.nodes.objects.add_folder(namespace_index, "Struct")
    values = [axis.value_at(0.0) for axis in axes]

    state = await folder.add_variable(
        f"ns={namespace_index};s=Struct.AxisState",
        "Struct.AxisState",
        getattr(ua, AXIS_STATE_TYPE_NAME)(
            Position=_point(ua, values), Enabled=True, Name="X", Limits=[0.0, 2450.0]
        ),
        datatype=state_type.nodeid,
    )
    await folder.add_variable(
        f"ns={namespace_index};s=Struct.Point",
        "Struct.Point",
        _point(ua, values),
        datatype=point_type.nodeid,
    )
    array = await folder.add_variable(
        f"ns={namespace_index};s=Struct.Positions",
        "Struct.Positions",
        values,
        ua.VariantType.Double,
    )
    return _Structs(axes, state, array)


def _user_manager(security: MockSecurity) -> Any:
    """A user check, or `None` for a server that asks for nobody.

    `None` rather than a permissive manager: asyncua's default already welcomes
    everyone, and a manager that always says yes would be a second place for
    that decision to live.
    """
    if not security.requires_user:
        return None

    from asyncua.crypto.permission_rules import User, UserRole
    from asyncua.server.user_managers import UserManager

    expected_name = security.username
    expected_password = security.password

    class _OnlyOneUser(UserManager):
        # The names are asyncua's: renaming the two we ignore would break the
        # override, and only `username` and `password` decide anything here.
        def get_user(  # noqa: PLR6301
            self,
            iserver: Any,  # noqa: ARG002
            username: str | None = None,
            password: str | None = None,
            certificate: Any = None,  # noqa: ARG002
        ) -> Any:
            """`None` is how asyncua is told to refuse — it answers the client
            with `BadUserAccessDenied`, which is what the diagnostics report."""
            if username == expected_name and password == expected_password:
                return User(role=UserRole.User)
            logger.info("refusing a session", username=username)
            return None

    return _OnlyOneUser()


async def _apply_security(server: Any, security: MockSecurity) -> None:
    """Offer the policies `security` asks for, and only the tokens it allows."""
    from asyncua import ua

    if security.requires_user:
        # Not merely "a username is accepted": anonymous is removed from what is
        # offered, so a client that tries it is refused at the endpoint rather
        # than at the session.
        server.set_identity_tokens([ua.UserNameIdentityToken])

    if not security.is_secure:
        # No security: this is a local development tool. Never do this on a real
        # server.
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        return

    directory = security.pki_dir or Path(tempfile.mkdtemp(prefix="pssim-mock-pki-"))
    directory.mkdir(parents=True, exist_ok=True)
    certificate, key = await _server_certificate(directory)
    await server.load_certificate(str(certificate))
    await server.load_private_key(str(key))
    # `NoSecurity` stays alongside, so one server can answer both a discovery
    # that finds two offers and a client that picks either.
    server.set_security_policy(
        [
            ua.SecurityPolicyType.NoSecurity,
            ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
        ]
    )
    logger.info("mock server security", policies=2, certificate=str(certificate))


async def _server_certificate(directory: Path) -> tuple[Path, Path]:
    """A self-signed pair for the server, issued for `MOCK_APPLICATION_URI`.

    `cert_gen` rather than the `Client` helper — that one takes its identity from
    a client's own uri, and this is a server.
    """
    from asyncua.crypto.cert_gen import setup_self_signed_certificate
    from cryptography.x509.oid import ExtendedKeyUsageOID

    key = directory / "mock_key.pem"
    certificate = directory / "mock_cert.der"
    await setup_self_signed_certificate(
        key,
        certificate,
        MOCK_APPLICATION_URI,
        "127.0.0.1",
        [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH],
        {"organizationName": "PSsimTool", "commonName": "PSsimTool Mock PLC"},
    )
    return certificate, key


def main(endpoint: str = DEFAULT_ENDPOINT, security: MockSecurity = OPEN) -> None:
    """The entry point for `pssim mock-server`."""
    try:
        asyncio.run(run_mock_server(endpoint, security=security))
    except KeyboardInterrupt:
        logger.info("mock server stopped")

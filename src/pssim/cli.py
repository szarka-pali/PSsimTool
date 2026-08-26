"""The application's entry points.

Heavy imports (`panda3d`, `OCP`, `asyncua`) are **inside the commands**, not at module
level — otherwise `pssim --help` would take seconds and the unit tests would drag in the
graphics stack.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from pssim.domain.errors import PSsimError
from pssim.observability import configure, get_logger

PASSWORD_ENV = "PSSIM_OPCUA_PASSWORD"
"""Where `probe` reads a password from. Never a command-line option: a
password typed on a command line lands in the shell's history."""

if TYPE_CHECKING:
    from pssim.cad.step_import import ImportSettings
    from pssim.config.loader import LoadedMachine
    from pssim.io.base import SourceStatus
    from pssim.io.store import StateStore


def _ensure_utf8_console() -> None:
    """Switch the console to UTF-8.

    The Windows console still uses cp1252, which cannot encode accented characters —
    without this, `pssim --help` dies with a `UnicodeEncodeError`. It has to happen at
    import time, not in a callback: `--help` is printed before that runs.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_console()

app = typer.Typer(
    name="pssim",
    help="3D machine simulation driven by live data from a PLC over OPC UA.",
    no_args_is_help=True,
    add_completion=False,
)

logger = get_logger(__name__)

DEFAULT_CACHE_DIR = Path("assets/cache")

MachineArg = Annotated[Path, typer.Argument(help="Path to machines/*.yaml")]
EndpointOpt = Annotated[
    str | None,
    typer.Option("--endpoint", "-e", envvar="PSSIM_OPCUA_ENDPOINT", help="OPC UA endpoint"),
]
LogLevelOpt = Annotated[str | None, typer.Option("--log-level", envvar="PSSIM_LOG_LEVEL")]


@app.callback()
def main(log_level: LogLevelOpt = None) -> None:
    configure(log_level)


@app.command()
def ui(
    lang: Annotated[
        str | None,
        typer.Option("--lang", envvar="PSSIM_LANG", help="UI language, e.g. en or sk"),
    ] = None,
) -> None:
    """Run the desktop application.

    Without a PLC and without a machine definition for now. The source language of the UI
    is English; `--lang` picks a translation, if a compiled `.qm` file exists for it.
    """
    try:
        from pssim.ui.i18n import SOURCE_LANGUAGE, available_languages
        from pssim.ui.main_window import run
    except ImportError as exc:  # PySide6 is an optional dependency
        typer.echo("PySide6 is not installed - run `uv sync --extra ui`", err=True)
        raise typer.Exit(code=1) from exc

    language = lang or SOURCE_LANGUAGE
    usable = available_languages()
    if language not in usable:
        raise typer.BadParameter(
            f"language {language!r} is not available; available: {', '.join(sorted(usable))}"
        )

    raise typer.Exit(code=run(language=language))


@app.command()
def validate(machine: MachineArg) -> None:
    """Validate a machine definition without connecting to a PLC and without opening a window."""
    from pssim.config.loader import load_machine

    loaded = _guard(lambda: load_machine(machine))
    typer.echo(f"machine:    {loaded.machine.name}")
    typer.echo(
        f"joints:     {len(loaded.machine.joints)} ({len(loaded.machine.moving_joints)} moving)"
    )
    typer.echo(f"signals:    {len(loaded.bindings)}")
    typer.echo(f"STEP:       {loaded.step_file}")
    typer.echo(f"units:      {loaded.units} (scale {loaded.scale_to_m})")
    typer.echo(f"cache:      {'present' if _cache_exists(loaded) else 'MISSING - run import-step'}")
    typer.echo("the definition is in order")


@app.command("import-step")
def import_step_command(
    step_file: Annotated[Path, typer.Argument(help="Path to the .step file")],
    machine: Annotated[Path | None, typer.Option("--machine", "-m")] = None,
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
    force: Annotated[bool, typer.Option("--force", help="Ignore the existing cache")] = False,
) -> None:
    """Import a STEP into the cache. Takes minutes — which is why it is a separate command."""
    from pssim.cad.step_import import ImportSettings, import_step

    units, linear, angular, scale = "mm", 0.5, 0.35, 1e-3
    if machine is not None:
        from pssim.config.loader import load_machine

        loaded = _guard(lambda: load_machine(machine))
        units = loaded.units
        scale = loaded.scale_to_m
        linear = loaded.linear_deflection_mm
        angular = loaded.angular_deflection_rad

    settings = ImportSettings(
        step_file=step_file,
        scale_to_m=scale,
        units=units,
        linear_deflection_mm=linear,
        angular_deflection_rad=angular,
    )
    metadata = _guard(lambda: import_step(settings, cache_dir, force=force))
    typer.echo(f"nodes:      {len(metadata.assembly.nodes)}")
    typer.echo(f"triangles:  {metadata.assembly.triangle_count}")


@app.command()
def run(
    machine: MachineArg,
    endpoint: EndpointOpt = None,
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
) -> None:
    """Run the simulation against a live OPC UA server."""
    from pssim.cad.cache import CacheEntry
    from pssim.cad.step_import import cache_key_for
    from pssim.config.loader import load_machine
    from pssim.io.opcua_source import build_source
    from pssim.viz.app import MachineViewer

    loaded = _guard(lambda: load_machine(machine))
    entry = _guard(lambda: CacheEntry(root=cache_dir, key=cache_key_for(_settings(loaded))))
    metadata = _guard(entry.read)
    source = build_source(loaded.source, loaded.bindings, endpoint_override=endpoint)

    viewer = MachineViewer(loaded, metadata.assembly, source, entry.directory)
    _guard(viewer.run)


@app.command()
def record(
    machine: MachineArg,
    output: Annotated[Path, typer.Option("--output", "-o")],
    endpoint: EndpointOpt = None,
    duration_s: Annotated[float, typer.Option("--duration", help="0 = until interrupted")] = 0.0,
) -> None:
    """Record the data stream from a PLC into JSONL. No window."""
    import time

    from pssim.config.loader import load_machine
    from pssim.io.opcua_source import build_source
    from pssim.io.recorder import RecordingStore

    loaded = _guard(lambda: load_machine(machine))
    with RecordingStore(output) as store:
        source = build_source(
            loaded.source, loaded.bindings, endpoint_override=endpoint, store=store
        )
        source.start()
        started = time.monotonic()
        try:
            while duration_s <= 0.0 or (time.monotonic() - started) < duration_s:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            source.stop()
        typer.echo(f"samples recorded: {store.sample_count} → {output}")


@app.command()
def replay(
    recording: Annotated[Path, typer.Argument(help="the JSONL recording")],
    machine: MachineArg,
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
    speed: Annotated[float, typer.Option("--speed", help="1.0 = the original pace")] = 1.0,
    loop: Annotated[bool, typer.Option("--loop")] = False,
) -> None:
    """Replay a recorded stream. The main tool for reproducing faults from the field."""
    from pssim.cad.cache import CacheEntry
    from pssim.cad.step_import import cache_key_for
    from pssim.config.loader import load_machine
    from pssim.io.replay import ReplaySource
    from pssim.viz.app import MachineViewer

    loaded = _guard(lambda: load_machine(machine))
    entry = CacheEntry(root=cache_dir, key=cache_key_for(_settings(loaded)))
    metadata = _guard(entry.read)
    source = ReplaySource(recording, speed=speed, loop=loop)
    typer.echo(f"recording: {source.duration_s:.1f} s")

    viewer = MachineViewer(loaded, metadata.assembly, source, entry.directory)
    _guard(viewer.run)


@app.command()
def screenshot(
    machine: MachineArg,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("screenshot.png"),
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
    values: Annotated[
        str | None,
        typer.Option("--values", help='Joint positions, e.g. "axis_x=1.2,axis_c=1.57"'),
    ] = None,
    view: Annotated[
        str, typer.Option("--view", help="iso | front | back | left | right | top")
    ] = "iso",
) -> None:
    """Render the scene into a PNG without opening a window and without a PLC.

    Verifies that the machine can actually be seen — unlike tests of node positions, which
    pass even when the window is empty.
    """
    from pssim.cad.cache import CacheEntry
    from pssim.cad.step_import import cache_key_for
    from pssim.config.loader import load_machine
    from pssim.io.store import StateStore
    from pssim.viz.app import MachineViewer

    loaded = _guard(lambda: load_machine(machine))
    entry = CacheEntry(root=cache_dir, key=cache_key_for(_settings(loaded)))
    metadata = _guard(entry.read)

    from pssim.viz.camera import view_direction

    try:
        view_direction(view)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    viewer = MachineViewer(loaded, metadata.assembly, _StaticSource(StateStore()), entry.directory)
    path = _guard(lambda: viewer.render_screenshot(output, view=view, values=_parse_values(values)))
    typer.echo(f"written: {path}")


def _parse_values(raw: str | None) -> dict[str, float]:
    """Parse `axis_x=1.2,axis_c=1.57` into joint values."""
    if not raw:
        return {}
    values: dict[str, float] = {}
    for item in raw.split(","):
        name, _, number = item.partition("=")
        if not number:
            raise typer.BadParameter(f"expected `name=value`, got {item!r}")
        try:
            values[name.strip()] = float(number)
        except ValueError as exc:
            raise typer.BadParameter(f"{number!r} is not a number") from exc
    return values


class _StaticSource:
    """A data source that sends nothing. For the modes without a PLC (screenshot)."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    @property
    def status(self) -> SourceStatus:
        from pssim.io.base import SourceStatus

        return SourceStatus.DISCONNECTED

    @property
    def store(self) -> StateStore:
        return self._store

    def start(self) -> None: ...

    def stop(self) -> None: ...


@app.command("write-icon")
def write_icon(
    path: Annotated[
        Path,
        typer.Argument(help="Where to write the icon, e.g. build/pssim.ico"),
    ],
) -> None:
    """Render the application icon to a file.

    The icon is drawn in code and nothing binary is committed (see
    docs/architecture.md R17). A packaging step needs a real file, so this is how
    it gets one that cannot drift from the drawing.
    """
    from PySide6.QtWidgets import QApplication  # a pixmap needs an application

    from pssim.ui.icons import write_app_icon

    _ = QApplication.instance() or QApplication([])
    written = write_app_icon(path)
    typer.echo(f"icon written: {written}")


@app.command("mock-server")
def mock_server(
    endpoint: Annotated[str, typer.Option("--endpoint", "-e")] = "opc.tcp://0.0.0.0:4840/pssim/",
    secure: Annotated[
        bool,
        typer.Option("--secure", help="Also offer Basic256Sha256/SignAndEncrypt"),
    ] = False,
    require_user: Annotated[
        str | None,
        typer.Option("--require-user", help="Demand USER:PASSWORD and refuse anonymous"),
    ] = None,
) -> None:
    """Run the simulated OPC UA server. Development and tests without a PLC.

    The options exist so the client can be tested against a server that says
    **no**: a client that has only ever met an open server is one nobody has
    tested against a real PLC.
    """
    from pssim.io.mock_server import MockSecurity
    from pssim.io.mock_server import main as run_server

    username, _, password = (require_user or "").partition(":")
    run_server(
        endpoint,
        MockSecurity(is_secure=secure, username=username, password=password),
    )


@app.command()
def probe(
    endpoint: Annotated[str, typer.Argument(help="opc.tcp://...")],
    browse: Annotated[
        str | None, typer.Option("--browse", help="List the children of this node id")
    ] = None,
    path: Annotated[
        str,
        typer.Option(
            "--path",
            help="Look inside a structure or an array: Position, Limits, Position.X",
        ),
    ] = "",
    policy: Annotated[
        str | None,
        typer.Option("--policy", help="Security policy, e.g. Basic256Sha256. Default: none"),
    ] = None,
    sign_only: Annotated[bool, typer.Option("--sign-only", help="Sign but do not encrypt")] = False,
    user: Annotated[
        str | None,
        typer.Option("--user", help=f"User name. The password comes from ${PASSWORD_ENV}"),
    ] = None,
) -> None:
    """Find out what an OPC UA server offers. The first step in a diagnosis.

    Always prints what the server advertises before trying anything, because
    "it will not connect" is usually answered there: a server that does not
    offer `Anonymous` refuses an anonymous session, and a server offering only
    `Basic256Sha256` refuses an unsecured one.
    """
    _guard(lambda: _probe(endpoint, browse, policy, sign_only, user, path))


def _probe(
    endpoint: str,
    browse: str | None,
    policy: str | None,
    sign_only: bool,
    user: str | None,
    path: str = "",
) -> None:
    from pssim.io.opcua_browse_session import OBJECTS_NODE_ID, OpcUaBrowseSession
    from pssim.io.opcua_security import (
        POLICY_NONE,
        Credentials,
        SecurityMode,
        TokenType,
        discover_endpoints,
    )

    offers = discover_endpoints(endpoint)
    typer.echo(f"{len(offers)} ways in:")
    for offer in offers:
        tokens = ", ".join(token.value for token in offer.token_types) or "-"
        typer.echo(f"  {offer.label:<44} level {offer.security_level:<3} {tokens}")

    mode = SecurityMode.NONE
    if policy:
        mode = SecurityMode.SIGN if sign_only else SecurityMode.SIGN_AND_ENCRYPT
    credentials = Credentials(
        policy_name=policy or POLICY_NONE,
        mode=mode,
        token=TokenType.USERNAME if user else TokenType.ANONYMOUS,
        username=user or "",
        password=os.environ.get(PASSWORD_ENV, ""),
    )
    typer.echo("")
    typer.echo(f"connecting: {credentials.describe()}")

    session = OpcUaBrowseSession(endpoint, credentials=credentials)
    try:
        session.open()
        for node in session.children_of(browse or OBJECTS_NODE_ID, path=path).nodes:
            access = " [w]" if node.is_writable else ""
            # A field carries its parent's node id, so the path is what says
            # which place the row is - print it where there is one.
            where = f"{node.node_id} -> {node.path}" if node.path else node.node_id
            typer.echo(f"  {where:<44} {node.label:<20} {node.data_type}{access}")
    finally:
        # The log is the point of the command when the connection failed, so it
        # is printed either way rather than only on success.
        for entry in session.diagnostics.entries:
            typer.echo(f"  {entry.describe()}", err=True)
        session.close()


# -- helpers ----------------------------------------------------------------


def _settings(loaded: LoadedMachine) -> ImportSettings:
    """Assemble `ImportSettings` from a loaded machine — the cache key must match the import."""
    from pssim.cad.step_import import ImportSettings

    return ImportSettings(
        step_file=loaded.step_file,
        scale_to_m=loaded.scale_to_m,
        units=loaded.units,
        linear_deflection_mm=loaded.linear_deflection_mm,
        angular_deflection_rad=loaded.angular_deflection_rad,
    )


def _cache_exists(loaded: LoadedMachine) -> bool:
    from pssim.cad.cache import CacheEntry
    from pssim.cad.step_import import cache_key_for

    try:
        return CacheEntry(root=DEFAULT_CACHE_DIR, key=cache_key_for(_settings(loaded))).exists
    except PSsimError:
        return False


def _guard[T](action: Callable[[], T]) -> T:
    """Translate domain errors into readable output instead of a traceback.

    A bug (an unexpected exception) **keeps** its traceback — that one we want to see.
    """
    try:
        return action()
    except PSsimError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()

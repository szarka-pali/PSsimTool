"""The application's entry points.

Heavy imports (`panda3d`, `OCP`, `asyncua`) are **inside the commands**, not at module
level — otherwise `pssim --help` would take seconds and the unit tests would drag in the
graphics stack.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from pssim.domain.errors import PSsimError
from pssim.observability import configure, get_logger

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

MachineArg = Annotated[Path, typer.Argument(help="Cesta k machines/*.yaml")]
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
        typer.Option("--lang", envvar="PSSIM_LANG", help="Jazyk UI, napr. en alebo sk"),
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
    typer.echo(f"stroj:      {loaded.machine.name}")
    typer.echo(
        f"joints:     {len(loaded.machine.joints)} ({len(loaded.machine.moving_joints)} moving)"
    )
    typer.echo(f"signals:    {len(loaded.bindings)}")
    typer.echo(f"STEP:       {loaded.step_file}")
    typer.echo(f"jednotky:   {loaded.units} (scale {loaded.scale_to_m})")
    typer.echo(
        f"cache:      {'existuje' if _cache_exists(loaded) else 'MISSING - run import-step'}"
    )
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
    typer.echo(f"uzlov:        {len(metadata.assembly.nodes)}")
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
        typer.Option("--values", help='Joint positions, e.g. "os_x=1.2,os_c=1.57"'),
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
    """Parse `os_x=1.2,os_c=1.57` into joint values."""
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


@app.command("mock-server")
def mock_server(
    endpoint: Annotated[str, typer.Option("--endpoint", "-e")] = "opc.tcp://0.0.0.0:4840/pssim/",
) -> None:
    """Run the simulated OPC UA server. Development and tests without a PLC."""
    from pssim.io.mock_server import main as run_server

    run_server(endpoint)


@app.command()
def probe(
    endpoint: Annotated[str, typer.Argument(help="opc.tcp://...")],
    browse: Annotated[
        str | None, typer.Option("--browse", help="List the nodes in a namespace")
    ] = None,
) -> None:
    """Find out what an OPC UA server offers. The first step in a diagnosis."""
    _guard(lambda: asyncio.run(_probe(endpoint, browse)))


async def _probe(endpoint: str, browse: str | None) -> None:
    from asyncua import Client

    async with Client(url=endpoint) as client:
        typer.echo(f"connected: {endpoint}")
        root = client.get_node(browse) if browse else client.nodes.objects
        for child in await root.get_children():
            name = await child.read_browse_name()
            typer.echo(f"  {child.nodeid.to_string()}  {name.Name}")


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

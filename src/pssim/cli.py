"""Vstupné body aplikácie.

Ťažké importy (`panda3d`, `OCP`, `asyncua`) sú **vnútri príkazov**, nie na module
level — inak by `pssim --help` trval sekundy a unit testy by ťahali grafický stack.
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
    """Prepne konzolu na UTF-8.

    Windows konzola má stále cp1252, v ktorom sa diakritika zakóduť nedá —
    bez tohto spadne `pssim --help` na `UnicodeEncodeError`. Musí sa to stať
    pri importe, nie v callbacku: `--help` sa vypíše ešte pred ním.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_console()

app = typer.Typer(
    name="pssim",
    help="3D simulácia strojov riadená live dátami z PLC cez OPC UA.",
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
    """Spustí desktopovú aplikáciu.

    Zatiaľ bez PLC a bez definície stroja. Zdrojový jazyk UI je angličtina;
    `--lang` vyberie preklad, ak je preň skompilovaný `.qm` súbor.
    """
    try:
        from pssim.ui.i18n import SOURCE_LANGUAGE, available_languages
        from pssim.ui.main_window import run
    except ImportError as exc:  # PySide6 je voliteľná závislosť
        typer.echo("PySide6 nie je nainštalované — spusti `uv sync --extra ui`", err=True)
        raise typer.Exit(code=1) from exc

    language = lang or SOURCE_LANGUAGE
    usable = available_languages()
    if language not in usable:
        raise typer.BadParameter(
            f"jazyk {language!r} nie je k dispozícii; dostupné: {', '.join(sorted(usable))}"
        )

    raise typer.Exit(code=run(language=language))


@app.command()
def validate(machine: MachineArg) -> None:
    """Overí definíciu stroja bez pripojenia k PLC a bez otvorenia okna."""
    from pssim.config.loader import load_machine

    loaded = _guard(lambda: load_machine(machine))
    typer.echo(f"stroj:      {loaded.machine.name}")
    typer.echo(
        f"kĺby:       {len(loaded.machine.joints)} ({len(loaded.machine.moving_joints)} pohyblivých)"
    )
    typer.echo(f"signály:    {len(loaded.bindings)}")
    typer.echo(f"STEP:       {loaded.step_file}")
    typer.echo(f"jednotky:   {loaded.units} (scale {loaded.scale_to_m})")
    typer.echo(
        f"cache:      {'existuje' if _cache_exists(loaded) else 'CHÝBA — spusti import-step'}"
    )
    typer.echo("definícia je v poriadku")


@app.command("import-step")
def import_step_command(
    step_file: Annotated[Path, typer.Argument(help="Cesta k .step súboru")],
    machine: Annotated[Path | None, typer.Option("--machine", "-m")] = None,
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
    force: Annotated[bool, typer.Option("--force", help="Ignoruj existujúcu cache")] = False,
) -> None:
    """Naimportuje STEP do cache. Trvá minúty — preto je to samostatný príkaz."""
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
    typer.echo(f"trojuholníkov:{metadata.assembly.triangle_count}")


@app.command()
def run(
    machine: MachineArg,
    endpoint: EndpointOpt = None,
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
) -> None:
    """Spustí simuláciu proti živému OPC UA serveru."""
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
    duration_s: Annotated[float, typer.Option("--duration", help="0 = do prerušenia")] = 0.0,
) -> None:
    """Zaznamená dátový tok z PLC do JSONL. Bez okna."""
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
        typer.echo(f"zaznamenaných vzoriek: {store.sample_count} → {output}")


@app.command()
def replay(
    recording: Annotated[Path, typer.Argument(help="JSONL záznam")],
    machine: MachineArg,
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
    speed: Annotated[float, typer.Option("--speed", help="1.0 = pôvodné tempo")] = 1.0,
    loop: Annotated[bool, typer.Option("--loop")] = False,
) -> None:
    """Prehrá zaznamenaný tok. Hlavný nástroj na reprodukciu chýb z prevádzky."""
    from pssim.cad.cache import CacheEntry
    from pssim.cad.step_import import cache_key_for
    from pssim.config.loader import load_machine
    from pssim.io.replay import ReplaySource
    from pssim.viz.app import MachineViewer

    loaded = _guard(lambda: load_machine(machine))
    entry = CacheEntry(root=cache_dir, key=cache_key_for(_settings(loaded)))
    metadata = _guard(entry.read)
    source = ReplaySource(recording, speed=speed, loop=loop)
    typer.echo(f"záznam: {source.duration_s:.1f} s")

    viewer = MachineViewer(loaded, metadata.assembly, source, entry.directory)
    _guard(viewer.run)


@app.command()
def screenshot(
    machine: MachineArg,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("screenshot.png"),
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = DEFAULT_CACHE_DIR,
    values: Annotated[
        str | None,
        typer.Option("--values", help='Polohy kĺbov, napr. "os_x=1.2,os_c=1.57"'),
    ] = None,
    view: Annotated[
        str, typer.Option("--view", help="iso | front | back | left | right | top")
    ] = "iso",
) -> None:
    """Vyrenderuje scénu do PNG bez otvorenia okna a bez PLC.

    Overí, že je stroj naozaj vidieť — na rozdiel od testov polôh uzlov,
    ktoré prejdú aj vtedy, keď je okno prázdne.
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
    typer.echo(f"zapísané: {path}")


def _parse_values(raw: str | None) -> dict[str, float]:
    """Rozparsuje `os_x=1.2,os_c=1.57` na hodnoty kĺbov."""
    if not raw:
        return {}
    values: dict[str, float] = {}
    for item in raw.split(","):
        name, _, number = item.partition("=")
        if not number:
            raise typer.BadParameter(f"očakávam `nazov=hodnota`, dostal som {item!r}")
        try:
            values[name.strip()] = float(number)
        except ValueError as exc:
            raise typer.BadParameter(f"{number!r} nie je číslo") from exc
    return values


class _StaticSource:
    """Zdroj dát, ktorý nič neposiela. Pre režimy bez PLC (screenshot)."""

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
    """Spustí simulovaný OPC UA server. Vývoj a testy bez PLC."""
    from pssim.io.mock_server import main as run_server

    run_server(endpoint)


@app.command()
def probe(
    endpoint: Annotated[str, typer.Argument(help="opc.tcp://...")],
    browse: Annotated[str | None, typer.Option("--browse", help="Vypíš nody v namespace")] = None,
) -> None:
    """Zistí, čo OPC UA server ponúka. Prvý krok pri diagnostike."""
    _guard(lambda: asyncio.run(_probe(endpoint, browse)))


async def _probe(endpoint: str, browse: str | None) -> None:
    from asyncua import Client

    async with Client(url=endpoint) as client:
        typer.echo(f"pripojené: {endpoint}")
        root = client.get_node(browse) if browse else client.nodes.objects
        for child in await root.get_children():
            name = await child.read_browse_name()
            typer.echo(f"  {child.nodeid.to_string()}  {name.Name}")


# -- pomocné ---------------------------------------------------------------


def _settings(loaded: LoadedMachine) -> ImportSettings:
    """Zloží `ImportSettings` z načítaného stroja — cache kľúč musí sedieť s importom."""
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
    """Preloží doménové chyby na čitateľný výstup namiesto tracebacku.

    Bug (neočakávaná výnimka) traceback naopak **ponechá** — ten chceme vidieť.
    """
    try:
        return action()
    except PSsimError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()

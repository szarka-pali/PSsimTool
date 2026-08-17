"""Renderovacie testy — overujú, že stroj je naozaj VIDIEŤ.

Toto je jediná trieda testov, ktorá by zachytila „otvorí sa prázdne okno".
Testy polôh uzlov prejdú aj vtedy, keď kamera mieri mimo, je v počiatku,
alebo je celá scéna za near rovinou.

Render ide do súboru cez offscreen buffer, okno sa neotvára.

Vyžaduje `uv sync --extra viz --extra cad`. Spustenie: ``uv run pytest -m viz``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pssim.cad.step_import import ImportSettings, import_step
from pssim.config.loader import load_machine
from pssim.io.store import StateStore
from pssim.viz.app import MachineViewer, ViewerConfig

pytestmark = [pytest.mark.viz, pytest.mark.cad]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_MACHINE = PROJECT_ROOT / "machines" / "demo.yaml"
FIXTURE = PROJECT_ROOT / "tests" / "data" / "fixture.step"

RENDER_SIZE = (320, 240)


class StubSource:
    """Zdroj bez dát — render nepotrebuje PLC."""

    def __init__(self) -> None:
        self._store = StateStore()

    @property
    def status(self) -> object:
        from pssim.io.base import SourceStatus

        return SourceStatus.DISCONNECTED

    @property
    def store(self) -> StateStore:
        return self._store

    def start(self) -> None: ...

    def stop(self) -> None: ...


def render(tmp_path: Path, *, view: str = "back", values: dict[str, float] | None = None) -> Path:
    """Naimportuje fixture, postaví scénu a vyrenderuje ju do PNG."""
    cache_root = tmp_path / "cache"
    loaded = load_machine(DEMO_MACHINE, project_root=PROJECT_ROOT)
    settings = ImportSettings(
        step_file=FIXTURE,
        scale_to_m=loaded.scale_to_m,
        units=loaded.units,
        linear_deflection_mm=loaded.linear_deflection_mm,
        angular_deflection_rad=loaded.angular_deflection_rad,
    )
    metadata = import_step(settings, cache_root)

    viewer = MachineViewer(
        loaded,
        metadata.assembly,
        StubSource(),  # type: ignore[arg-type]
        cache_root / metadata.key.digest,
        ViewerConfig(background=(0.0, 0.0, 0.0)),
    )
    return viewer.render_screenshot(
        tmp_path / "render.png", size=RENDER_SIZE, view=view, values=values
    )


def pixel_stats(path: Path) -> tuple[int, int]:
    """Vráti `(počet nepozadových pixelov, celkový počet)`.

    Pozadie je čierne, takže „nepozadový" znamená akýkoľvek svetlejší pixel.
    """
    from panda3d.core import Filename, PNMImage

    image = PNMImage()
    assert image.read(Filename.fromOsSpecific(str(path))), f"PNG sa nedá načítať: {path}"

    lit = 0
    total = image.getXSize() * image.getYSize()
    for x in range(image.getXSize()):
        for y in range(image.getYSize()):
            if max(image.getXel(x, y)) > 0.05:
                lit += 1
    return lit, total


class TestRenderNieJePrazdny:
    def test_subor_vznikne(self, tmp_path: Path) -> None:
        assert render(tmp_path).is_file()

    def test_obrazok_ma_rozmery(self, tmp_path: Path) -> None:
        # Presné rozmery sa neoverujú: Panda3D dovolí jedinú ShowBase na proces,
        # takže veľkosť bufferu určí prvý render v celej testovacej sade.
        from panda3d.core import Filename, PNMImage

        image = PNMImage()
        image.read(Filename.fromOsSpecific(str(render(tmp_path))))

        assert image.getXSize() > 0
        assert image.getYSize() > 0

    def test_stroj_je_vidiet(self, tmp_path: Path) -> None:
        # TOTO je ten test. Prázdne okno = 0 nepozadových pixelov.
        lit, total = pixel_stats(render(tmp_path))

        assert lit > 0, "render je prázdny — kamera mieri mimo alebo je scéna za near rovinou"
        assert lit > total * 0.01, f"vidieť len {lit}/{total} pixelov, stroj je mimo záberu"

    def test_stroj_nevyplna_cely_zaber(self, tmp_path: Path) -> None:
        # Ak by near rovina bola príliš blízko a kamera vnútri modelu,
        # vyplnil by obraz celý a nič by nebolo poznať.
        lit, total = pixel_stats(render(tmp_path))

        assert lit < total * 0.95, "stroj vyplňuje celý záber — kamera je príliš blízko"

    @pytest.mark.parametrize("view", ["iso", "front", "back", "left", "right", "top"])
    def test_kazdy_pohlad_nieco_ukaze(self, tmp_path: Path, view: str) -> None:
        lit, _ = pixel_stats(render(tmp_path, view=view))

        assert lit > 0, f"pohľad {view!r} je prázdny"


class TestPohybJeVidiet:
    def test_iny_stav_osi_da_iny_obrazok(self, tmp_path: Path) -> None:
        # Ak by sa hodnoty kĺbov na scénu nepremietali, obrázky by boli zhodné.
        rest = pixel_stats(render(tmp_path / "a"))
        moved = pixel_stats(render(tmp_path / "b", values={"os_x": 1.5, "os_z": 0.5}))

        assert rest[0] != moved[0]

"""Test celej reťaze: STEP → cache → scéna → hodnota z PLC → poloha dielu.

Toto je test, ktorý odpovedá na otázku „pohne hodnota z PLC naozaj tým správnym
dielom a správnym smerom?". Bez neho sa to dá zistiť len okom v okne.

Okno sa neotvára — `MachineViewer.build_scene()` je zámerne navrhnuté tak,
aby `ShowBase` nepotrebovalo.

Vyžaduje `uv sync --extra viz --extra cad`. Spustenie: ``uv run pytest -m viz``
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pssim.cad.step_import import ImportSettings, import_step
from pssim.config.loader import load_machine
from pssim.io.store import StateStore
from pssim.viz.app import MachineViewer

pytestmark = [pytest.mark.viz, pytest.mark.cad]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_MACHINE = PROJECT_ROOT / "machines" / "demo.yaml"
FIXTURE = PROJECT_ROOT / "tests" / "data" / "fixture.step"

#: Z machines/demo.yaml — portál je v CAD posunutý o 100 mm v X.
PORTAL_CAD_OFFSET_M = 0.1


class StubSource:
    """Zdroj dát, ktorý nič nerobí. Scéna ho potrebuje, test ho neplní."""

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


@pytest.fixture(scope="module")
def viewer(tmp_path_factory: pytest.TempPathFactory) -> MachineViewer:
    """Postavená scéna z demo stroja a fixture geometrie. Bez okna."""
    cache_root = tmp_path_factory.mktemp("cache")
    loaded = load_machine(DEMO_MACHINE, project_root=PROJECT_ROOT)
    settings = ImportSettings(
        step_file=FIXTURE,
        scale_to_m=loaded.scale_to_m,
        units=loaded.units,
        linear_deflection_mm=loaded.linear_deflection_mm,
        angular_deflection_rad=loaded.angular_deflection_rad,
    )
    metadata = import_step(settings, cache_root)

    instance = MachineViewer(
        loaded,
        metadata.assembly,
        StubSource(),  # type: ignore[arg-type]
        cache_root / metadata.key.digest,
    )
    instance.build_scene()
    return instance


def node_position(viewer: MachineViewer, path: str) -> tuple[float, float, float]:
    """Poloha uzla voči koreňu scény — teda tam, kde ho používateľ uvidí."""
    node_path = viewer.node_path(path)
    assert node_path is not None, f"uzol {path} v scéne nie je"
    point = node_path.getPos(viewer.scene_root)
    return (point[0], point[1], point[2])


class TestScenaJePostavena:
    def test_vsetky_uzly_maju_nodepath(self, viewer: MachineViewer) -> None:
        assert all(
            viewer.node_path(path) is not None
            for path in (
                "base",
                "base/portal",
                "base/portal/Part1[1]",
                "base/portal/Part1[2]",
                "base/portal/hlava",
                "base/kryt",
            )
        )

    def test_geometria_je_pripojena(self, viewer: MachineViewer) -> None:
        # Ak by mesh chýbal, uzol by nemal potomka s Geomom.
        node_path = viewer.node_path("base/kryt")

        assert node_path is not None
        assert node_path.getNumChildren() == 1

    def test_hierarchia_zodpoveda_assembly(self, viewer: MachineViewer) -> None:
        child = viewer.node_path("base/portal/hlava")

        assert child is not None
        assert child.getParent().getName() == "portal"


class TestPohybKlbov:
    def test_bez_hodnot_stoji_diel_na_cad_polohe(self, viewer: MachineViewer) -> None:
        # Kľúčové: pred prvou hodnotou z PLC musí diel byť tam, kde ho dal CAD.
        viewer.apply_values({})

        assert node_position(viewer, "base/portal")[0] == pytest.approx(
            PORTAL_CAD_OFFSET_M, abs=1e-6
        )

    def test_hodnota_posunie_diel_po_osi(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"os_x": 1.0})

        position = node_position(viewer, "base/portal")

        # Pohyb kĺbu sa pridáva NA VRCH polohy z CAD, preto 0.1 + 1.0.
        assert position[0] == pytest.approx(PORTAL_CAD_OFFSET_M + 1.0, abs=1e-6)

    def test_pohyb_je_len_po_zadanej_osi(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"os_x": 1.0})

        position = node_position(viewer, "base/portal")

        assert (position[1], position[2]) == pytest.approx((0.0, 0.0), abs=1e-6)

    def test_ina_hodnota_da_inu_polohu(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"os_x": 0.5})
        first = node_position(viewer, "base/portal")[0]
        viewer.apply_values({"os_x": 2.0})
        second = node_position(viewer, "base/portal")[0]

        assert second - first == pytest.approx(1.5, abs=1e-6)

    def test_hodnota_nad_limitom_sa_orezhe(self, viewer: MachineViewer) -> None:
        # Limit os_x je 2.5 m. PLC môže poslať čokoľvek, scéna nesmie ujsť.
        viewer.apply_values({"os_x": 99.0})

        assert node_position(viewer, "base/portal")[0] == pytest.approx(
            PORTAL_CAD_OFFSET_M + 2.5, abs=1e-6
        )

    def test_potomok_sa_hybe_s_rodicom(self, viewer: MachineViewer) -> None:
        # hlava je potomkom portálu — musí ísť s ním, aj keď má vlastnú hodnotu 0.
        viewer.apply_values({"os_x": 0.0})
        before = node_position(viewer, "base/portal/hlava")
        viewer.apply_values({"os_x": 1.0})
        after = node_position(viewer, "base/portal/hlava")

        assert after[0] - before[0] == pytest.approx(1.0, abs=1e-6)

    def test_pevny_klb_sa_nehybe(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"os_x": 2.0})
        before = node_position(viewer, "base/kryt")
        viewer.apply_values({"os_x": 0.0})
        after = node_position(viewer, "base/kryt")

        assert before == pytest.approx(after, abs=1e-9)


class TestRotacia:
    def test_rotacny_klb_otoci_diel(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"os_c": math.pi / 2})

        node_path = viewer.node_path("base/portal/hlava")
        assert node_path is not None
        quat = node_path.getQuat()

        # hlava má z CAD už otočenie o 90° okolo Z; kĺb pridá ďalších 90°,
        # takže spolu 180° → +X sa má stať -X.
        from panda3d.core import LPoint3

        rotated = quat.xform(LPoint3(1.0, 0.0, 0.0))

        assert (rotated[0], rotated[1]) == pytest.approx((-1.0, 0.0), abs=1e-5)

    def test_nulovy_uhol_zachova_cad_otocenie(self, viewer: MachineViewer) -> None:
        from panda3d.core import LPoint3

        viewer.apply_values({"os_c": 0.0})

        node_path = viewer.node_path("base/portal/hlava")
        assert node_path is not None
        quat = node_path.getQuat()
        rotated = quat.xform(LPoint3(1.0, 0.0, 0.0))

        # Samotné CAD otočenie je 90° okolo Z: +X → +Y.
        assert (rotated[0], rotated[1]) == pytest.approx((0.0, 1.0), abs=1e-5)

"""Integračné testy vizualizačnej vrstvy proti reálnemu Panda3D.

Vyžadujú `uv sync --extra viz`. Spustenie: ``uv run pytest -m viz``

Okno sa neotvára — `NodePath` aj `Geom` sa dajú postaviť a preveriť bez neho.
Testy overujú dve veci, ktoré sa inak zistia až vizuálne („nejako to nesedí"):

1. že geometria z cache dorazí do `Geom` nepoškodená
2. že **Panda3D interpretuje náš kvaternión rovnako, ako ho počítame** —
   to je konvenčná pasca, kde chyba vyzerá ako náhodne otočený diel
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pssim.cad.mesh import build_mesh, empty_mesh
from pssim.cad.step_import import ImportSettings, import_step
from pssim.viz.mesh_loader import geom_node_from_mesh, load_geom_node
from pssim.viz.transforms import axis_angle_to_quat, rotate_point, rpy_to_quat

pytestmark = pytest.mark.viz

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixture.step"

SQUARE_VERTICES = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)
SQUARE_INDICES = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)


class TestGeomZMeshu:
    def test_prazdny_mesh_da_prazdny_uzol(self) -> None:
        node = geom_node_from_mesh(empty_mesh(), "prazdny")

        assert node.getNumGeoms() == 0

    def test_stvorec_da_jeden_geom(self) -> None:
        node = geom_node_from_mesh(build_mesh(SQUARE_VERTICES, SQUARE_INDICES), "stvorec")

        assert node.getNumGeoms() == 1

    def test_pocet_vrcholov_sedi(self) -> None:
        node = geom_node_from_mesh(build_mesh(SQUARE_VERTICES, SQUARE_INDICES), "stvorec")

        assert node.getGeom(0).getVertexData().getNumRows() == 4

    def test_pocet_trojuholnikov_sedi(self) -> None:
        node = geom_node_from_mesh(build_mesh(SQUARE_VERTICES, SQUARE_INDICES), "stvorec")

        assert node.getGeom(0).getPrimitive(0).getNumPrimitives() == 2

    def test_bounding_box_zodpoveda_vrcholom(self) -> None:
        # Ak by sa interleaved buffer skopíroval zle, rozmery by boli nezmyselné.
        node = geom_node_from_mesh(build_mesh(SQUARE_VERTICES * 3.0, SQUARE_INDICES), "stvorec")

        bounds = node.getBounds()

        assert bounds.getRadius() == pytest.approx(math.sqrt(2) * 1.5, abs=1e-5)

    def test_velky_mesh_nepretecie_16bit_indexy(self) -> None:
        # Bez NTUint32 by sa diel nad 65 535 vrcholov rozsypal na spleť trojuholníkov.
        vertex_count = 70_000
        vertices = np.zeros((vertex_count, 3), dtype=np.float32)
        vertices[:, 0] = np.arange(vertex_count, dtype=np.float32)
        indices = np.array([[0, vertex_count - 2, vertex_count - 1]], dtype=np.uint32)

        node = geom_node_from_mesh(build_mesh(vertices, indices), "velky")

        assert node.getGeom(0).getVertexData().getNumRows() == vertex_count


class TestNacitanieZCache:
    def test_chybajuci_subor_vrati_none(self, tmp_path: Path) -> None:
        # Chýbajúci mesh nesmie zhodiť štart — zvyšok stroja sa má zobraziť.
        assert load_geom_node(tmp_path / "nic.npz", "chyba") is None

    def test_poskodeny_subor_vrati_none(self, tmp_path: Path) -> None:
        broken = tmp_path / "rozbity.npz"
        broken.write_bytes(b"toto nie je npz")

        assert load_geom_node(broken, "rozbity") is None

    def test_geometria_z_fixture_dorazi_do_geomu(self, tmp_path: Path) -> None:
        settings = ImportSettings(step_file=FIXTURE, scale_to_m=1e-3, units="mm")
        metadata = import_step(settings, tmp_path)
        cover = metadata.assembly.node("base/kryt")

        assert cover is not None
        assert cover.mesh is not None
        node = load_geom_node(tmp_path / metadata.key.digest / cover.mesh, "kryt")

        assert node is not None
        assert node.getGeom(0).getPrimitive(0).getNumPrimitives() == 12


class TestKonvenciaOtocenia:
    """Overuje, že Panda3D chápe náš kvaternión rovnako ako my.

    Toto je ten test, kvôli ktorému sú `viz/transforms.py` čisté funkcie:
    keby konvencia nesedela, diel by bol otočený inak, než hovorí STEP,
    a hľadalo by sa to očami.
    """

    @staticmethod
    def panda_transform(
        quat: tuple[float, float, float, float],
        point: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        """Otočí bod tak, ako to spraví Panda3D pri `setQuat`. Okno netreba."""
        from panda3d.core import LPoint3, LQuaternion, NodePath

        node_path = NodePath("test")
        node_path.setQuat(LQuaternion(*quat))
        result = node_path.getMat().xformPoint(LPoint3(*point))
        return (float(result[0]), float(result[1]), float(result[2]))

    @pytest.mark.parametrize(
        ("axis", "angle", "point"),
        [
            ((0.0, 0.0, 1.0), math.pi / 2, (1.0, 0.0, 0.0)),
            ((1.0, 0.0, 0.0), math.pi / 2, (0.0, 1.0, 0.0)),
            ((0.0, 1.0, 0.0), math.pi / 3, (1.0, 0.0, 0.0)),
            ((1.0, 1.0, 1.0), 0.9, (0.3, -0.7, 1.2)),
        ],
    )
    def test_axis_angle_sedi_s_panda3d(
        self,
        axis: tuple[float, float, float],
        angle: float,
        point: tuple[float, float, float],
    ) -> None:
        quat = axis_angle_to_quat(axis, angle)

        assert self.panda_transform(quat, point) == pytest.approx(
            rotate_point(quat, point), abs=1e-5
        )

    @pytest.mark.parametrize(
        "rpy",
        [(0.0, 0.0, math.pi / 2), (0.3, 0.4, 0.5), (-1.1, 0.7, 2.2)],
    )
    def test_rpy_sedi_s_panda3d(self, rpy: tuple[float, float, float]) -> None:
        quat = rpy_to_quat(rpy)
        point = (1.0, 2.0, 3.0)

        assert self.panda_transform(quat, point) == pytest.approx(
            rotate_point(quat, point), abs=1e-5
        )

    def test_otocenie_hlavy_z_fixture(self) -> None:
        # `hlava` je vo fixture otočená o 90° okolo Z: +X sa má stať +Y.
        quat = rpy_to_quat((0.0, 0.0, math.pi / 2))

        assert self.panda_transform(quat, (1.0, 0.0, 0.0)) == pytest.approx(
            (0.0, 1.0, 0.0), abs=1e-5
        )

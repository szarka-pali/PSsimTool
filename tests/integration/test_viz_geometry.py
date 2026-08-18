"""Integration tests of the visualisation layer against real Panda3D.

They require `uv sync --extra viz`. Run with: ``uv run pytest -m viz``

No window is opened — both `NodePath` and `Geom` can be built and checked without one.
The tests cover two things that would otherwise only show up visually ("something
looks off"):

1. that geometry from the cache reaches the `Geom` undamaged
2. that **Panda3D interprets our quaternion the same way we compute it** — a
   convention trap where the mistake looks like a randomly rotated part
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


class TestGeomFromMesh:
    def test_an_empty_mesh_gives_an_empty_node(self) -> None:
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
        # If the interleaved buffer were copied wrongly, the dimensions would be nonsense.
        node = geom_node_from_mesh(build_mesh(SQUARE_VERTICES * 3.0, SQUARE_INDICES), "stvorec")

        bounds = node.getBounds()

        assert bounds.getRadius() == pytest.approx(math.sqrt(2) * 1.5, abs=1e-5)

    def test_velky_mesh_nepretecie_16bit_indexy(self) -> None:
        # Without NTUint32 a part over 65 535 vertices falls apart into a tangle.
        vertex_count = 70_000
        vertices = np.zeros((vertex_count, 3), dtype=np.float32)
        vertices[:, 0] = np.arange(vertex_count, dtype=np.float32)
        indices = np.array([[0, vertex_count - 2, vertex_count - 1]], dtype=np.uint32)

        node = geom_node_from_mesh(build_mesh(vertices, indices), "velky")

        assert node.getGeom(0).getVertexData().getNumRows() == vertex_count


class TestLoadingFromCache:
    def test_a_missing_file_returns_none(self, tmp_path: Path) -> None:
        # A missing mesh must not bring down startup — the rest of the machine should show.
        assert load_geom_node(tmp_path / "nic.npz", "chyba") is None

    def test_a_damaged_file_returns_none(self, tmp_path: Path) -> None:
        broken = tmp_path / "rozbity.npz"
        broken.write_bytes(b"toto nie je npz")

        assert load_geom_node(broken, "rozbity") is None

    def test_the_fixture_geometry_reaches_the_geom(self, tmp_path: Path) -> None:
        settings = ImportSettings(step_file=FIXTURE, scale_to_m=1e-3, units="mm")
        metadata = import_step(settings, tmp_path)
        cover = metadata.assembly.node("base/cover")

        assert cover is not None
        assert cover.mesh is not None
        node = load_geom_node(tmp_path / metadata.key.digest / cover.mesh, "cover")

        assert node is not None
        assert node.getGeom(0).getPrimitive(0).getNumPrimitives() == 12


class TestRotationConvention:
    """Verifies that Panda3D understands our quaternion the way we do.

    This is the test the pure functions in `viz/transforms.py` exist for: if the
    convention did not match, the part would be rotated differently from what the STEP
    says, and it would have to be found by eye.
    """

    @staticmethod
    def panda_transform(
        quat: tuple[float, float, float, float],
        point: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        """Rotate a point the way Panda3D does on `setQuat`. No window needed."""
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

    def test_the_head_rotation_from_the_fixture(self) -> None:
        # `head` is rotated by 90° about Z in the fixture: +X should become +Y.
        quat = rpy_to_quat((0.0, 0.0, math.pi / 2))

        assert self.panda_transform(quat, (1.0, 0.0, 0.0)) == pytest.approx(
            (0.0, 1.0, 0.0), abs=1e-5
        )

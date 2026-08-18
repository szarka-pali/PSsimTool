"""A test of the whole chain: STEP → cache → scene → a value from the PLC → part position.

This is the test that answers "does a value from the PLC really move the right part, in
the right direction?". Without it, that can only be established by eye in a window.

No window is opened — `MachineViewer.build_scene()` is deliberately designed not to
need a `ShowBase`.

Requires `uv sync --extra viz --extra cad`. Run with: ``uv run pytest -m viz``
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

#: From machines/demo.yaml — in CAD the portal is offset by 100 mm in X.
PORTAL_CAD_OFFSET_M = 0.1


class StubSource:
    """A data source that does nothing. The scene needs one; the test does not fill it."""

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
    """The scene built from the demo machine and the fixture geometry. No window."""
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
    """A node's position relative to the scene root — that is, where the user will see it."""
    node_path = viewer.node_path(path)
    assert node_path is not None, f"the node {path} is not in the scene"
    point = node_path.getPos(viewer.scene_root)
    return (point[0], point[1], point[2])


class TestSceneIsBuilt:
    def test_every_node_has_a_nodepath(self, viewer: MachineViewer) -> None:
        assert all(
            viewer.node_path(path) is not None
            for path in (
                "base",
                "base/portal",
                "base/portal/Part1[1]",
                "base/portal/Part1[2]",
                "base/portal/head",
                "base/cover",
            )
        )

    def test_the_geometry_is_attached(self, viewer: MachineViewer) -> None:
        # If the mesh were missing, the node would have no child holding a Geom.
        node_path = viewer.node_path("base/cover")

        assert node_path is not None
        assert node_path.getNumChildren() == 1

    def test_the_hierarchy_matches_the_assembly(self, viewer: MachineViewer) -> None:
        child = viewer.node_path("base/portal/head")

        assert child is not None
        assert child.getParent().getName() == "portal"


class TestJointMovement:
    def test_with_no_values_a_part_stays_at_its_cad_placement(self, viewer: MachineViewer) -> None:
        # The key point: before the first value from the PLC the part must be where CAD put it.
        viewer.apply_values({})

        assert node_position(viewer, "base/portal")[0] == pytest.approx(
            PORTAL_CAD_OFFSET_M, abs=1e-6
        )

    def test_a_value_moves_the_part_along_the_axis(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"axis_x": 1.0})

        position = node_position(viewer, "base/portal")

        # Joint movement is added ON TOP of the placement from CAD, hence 0.1 + 1.0.
        assert position[0] == pytest.approx(PORTAL_CAD_OFFSET_M + 1.0, abs=1e-6)

    def test_movement_is_only_along_the_given_axis(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"axis_x": 1.0})

        position = node_position(viewer, "base/portal")

        assert (position[1], position[2]) == pytest.approx((0.0, 0.0), abs=1e-6)

    def test_another_value_gives_another_position(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"axis_x": 0.5})
        first = node_position(viewer, "base/portal")[0]
        viewer.apply_values({"axis_x": 2.0})
        second = node_position(viewer, "base/portal")[0]

        assert second - first == pytest.approx(1.5, abs=1e-6)

    def test_a_value_above_the_limit_is_clamped(self, viewer: MachineViewer) -> None:
        # The limit of axis_x is 2.5 m. The PLC may send anything; the scene must not run away.
        viewer.apply_values({"axis_x": 99.0})

        assert node_position(viewer, "base/portal")[0] == pytest.approx(
            PORTAL_CAD_OFFSET_M + 2.5, abs=1e-6
        )

    def test_a_child_moves_with_its_parent(self, viewer: MachineViewer) -> None:
        # head is a child of the portal — it has to go with it, even with its own value at 0.
        viewer.apply_values({"axis_x": 0.0})
        before = node_position(viewer, "base/portal/head")
        viewer.apply_values({"axis_x": 1.0})
        after = node_position(viewer, "base/portal/head")

        assert after[0] - before[0] == pytest.approx(1.0, abs=1e-6)

    def test_a_fixed_joint_does_not_move(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"axis_x": 2.0})
        before = node_position(viewer, "base/cover")
        viewer.apply_values({"axis_x": 0.0})
        after = node_position(viewer, "base/cover")

        assert before == pytest.approx(after, abs=1e-9)


class TestRotation:
    def test_a_revolute_joint_rotates_the_part(self, viewer: MachineViewer) -> None:
        viewer.apply_values({"axis_c": math.pi / 2})

        node_path = viewer.node_path("base/portal/head")
        assert node_path is not None
        quat = node_path.getQuat()

        # head already carries a 90° rotation about Z from CAD; the joint adds another 90°,
        # so 180° in total → +X should become -X.
        from panda3d.core import LPoint3

        rotated = quat.xform(LPoint3(1.0, 0.0, 0.0))

        assert (rotated[0], rotated[1]) == pytest.approx((-1.0, 0.0), abs=1e-5)

    def test_a_zero_angle_keeps_the_cad_rotation(self, viewer: MachineViewer) -> None:
        from panda3d.core import LPoint3

        viewer.apply_values({"axis_c": 0.0})

        node_path = viewer.node_path("base/portal/head")
        assert node_path is not None
        quat = node_path.getQuat()
        rotated = quat.xform(LPoint3(1.0, 0.0, 0.0))

        # The CAD rotation on its own is 90° about Z: +X → +Y.
        assert (rotated[0], rotated[1]) == pytest.approx((0.0, 1.0), abs=1e-5)

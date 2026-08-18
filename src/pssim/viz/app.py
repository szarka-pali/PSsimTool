"""The Panda3D application — the render loop and the mapping of joints onto the scene.

This is thread A. It reads from the `StateStore` that thread B (`io/`) fills. Never call
anything blocking, or anything from asyncua, here.

`viz/` must work without `ui/` as well (`pssim run --no-ui`) — for debugging and for tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pssim.cad.model import CadAssembly
from pssim.config.loader import LoadedMachine
from pssim.domain.kinematics import JointPose, joint_pose, rest_pose
from pssim.domain.machine import Transform, Vec3
from pssim.io.base import DataSource, SourceStatus
from pssim.observability import get_logger
from pssim.viz.camera import DEFAULT_VIEW, setup_camera, setup_lights
from pssim.viz.embed import offscreen_showbase
from pssim.viz.scene import build_scene
from pssim.viz.scene_builder import ScenePlan, plan_scene
from pssim.viz.transforms import Quaternion, axis_angle_to_quat, multiply_quat, rpy_to_quat

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ViewerConfig:
    """Window settings for a standalone run."""

    window_title: str = "PSsimTool"
    background: tuple[float, float, float] = (0.12, 0.13, 0.15)


def compute_poses(
    loaded: LoadedMachine,
    values: dict[str, float],
    stale: frozenset[str],
) -> dict[str, JointPose]:
    """Recompute the poses of all joints from a snapshot of values.

    A pure function with no Panda3D — which is why it can be tested in `tests/unit/`.

    A joint with no value gets `rest_pose()`, not zero: if it has limits that do not
    contain zero, zero would place the part outside the physically possible range.
    A stale signal **is used** (the last known value) — `stale` serves only to mark it
    visually, not to discard the data.
    """
    del stale  # informational for now, the HUD will handle it
    poses: dict[str, JointPose] = {}
    for joint in loaded.machine.joints:
        value = values.get(joint.name)
        poses[joint.name] = rest_pose(joint) if value is None else joint_pose(joint, value)
    return poses


class MachineViewer:
    """A Panda3D `ShowBase` wrapped so it can be run standalone or embedded in Qt.

    The class is deliberately thin: all the logic that can be tested without a window is
    outside it (`compute_poses`, `plan_scene`).
    """

    def __init__(
        self,
        loaded: LoadedMachine,
        assembly: CadAssembly,
        source: DataSource,
        cache_dir: Path,
        config: ViewerConfig | None = None,
    ) -> None:
        self._loaded = loaded
        self._assembly = assembly
        self._source = source
        self._cache_dir = cache_dir
        self._config = config or ViewerConfig()
        self._plan: ScenePlan = plan_scene(loaded.machine, assembly)
        self._base: Any = None
        self._node_paths: dict[str, Any] = {}
        self._base_transforms: dict[str, tuple[Vec3, Quaternion]] = {}
        """The node's placement from the CAD assembly. Joint movement is added on top of it."""
        self._stale_signals: frozenset[str] = frozenset()
        self._scene_root: Any = None
        self._render_delay_s = loaded.source.effective_render_delay_s(
            getattr(source, "revised_interval_ms", None)
        )

        logger.info(
            "scene plan",
            moving=len(self._plan.moving_nodes),
            static=len(self._plan.static_nodes),
            render_delay_s=round(self._render_delay_s, 3),
        )

    def run(self) -> None:
        """Open the window and run the render loop. Blocks until the window is closed."""
        from direct.showbase.ShowBase import ShowBase
        from panda3d.core import WindowProperties

        self._base = ShowBase()
        properties = WindowProperties()
        properties.setTitle(self._config.window_title)
        self._base.win.requestProperties(properties)
        self._base.setBackgroundColor(*self._config.background, 1.0)

        root = self.build_scene()
        root.reparentTo(self._base.render)
        # Camera and lights only AFTER the scene is attached — framing needs its dimensions.
        setup_lights(root)
        setup_camera(self._base, root)

        self._source.start()
        self._base.taskMgr.add(self._update_task, "pssim-update")

        try:
            self._base.run()
        finally:
            self._source.stop()

    def render_screenshot(
        self,
        output: Path,
        size: tuple[int, int] = (1280, 800),
        view: str = DEFAULT_VIEW,
        values: dict[str, float] | None = None,
    ) -> Path:
        """Render the scene into a PNG without opening a window.

        Serves to verify that the machine can actually **be seen** — node positions can
        be tested headless, but "an empty window" cannot. See `pssim screenshot`.
        """
        from panda3d.core import Filename

        self._base = offscreen_showbase(size)
        # The alpha has to be 1.0: without it the background has alpha 0 and comes out
        # transparent in the PNG, which most viewers show as a white area.
        self._base.setBackgroundColor(*self._config.background, 1.0)

        root = self.build_scene()
        root.reparentTo(self._base.render)
        try:
            # Values only after the scene is built — before that the NodePaths do not exist.
            self.apply_values(values or {})
            setup_lights(root)
            setup_camera(self._base, root, view=view)

            # Two frames: the first allocates the buffers, the second draws the finished scene.
            self._base.graphicsEngine.renderFrame()
            self._base.graphicsEngine.renderFrame()

            output.parent.mkdir(parents=True, exist_ok=True)
            self._base.win.saveScreenshot(Filename.fromOsSpecific(str(output)))
        finally:
            # The scene has to be detached, or the next render in the same process would
            # draw both machines at once.
            root.removeNode()
        return output

    def build_scene(self) -> Any:
        """Assemble the `NodePath` hierarchy from the assembly and return its root.

        Deliberately **needs no window** and no `ShowBase` — the caller attaches the root
        wherever they want. That makes the whole chain (cache → scene → kinematics → part
        position) testable headless; see `tests/integration/test_viz_scene.py`.

        The assembling itself is done by `viz.scene.build_scene()`, shared with browsing a
        plain STEP file in the UI. What happens additionally here is remembering the CAD
        placements of the nodes, on top of which joint movement is added.
        """
        built = build_scene(
            self._assembly,
            self._cache_dir,
            name="machine",
            flatten=frozenset(self._plan.static_nodes),
        )
        self._scene_root = built.root
        self._node_paths = built.node_paths
        self._base_transforms = built.base_transforms
        return built.root

    def apply_values(self, values: dict[str, float]) -> None:
        """Set the joint poses according to the given values.

        Public for the tests and for a future UI (a manual "move the axis"). At run time
        `_apply_snapshot()` calls it with the values from the `StateStore`.
        """
        from panda3d.core import LQuaternion

        poses = compute_poses(self._loaded, values, frozenset())
        for joint_name, pose in poses.items():
            node_path = self._node_paths.get(self._plan.joint_to_node[joint_name])
            if node_path is None:
                continue

            # Joint movement is added ON TOP of the placement from the CAD assembly.
            # Without that, the part would jump to its parent's origin on the first value
            # from the PLC.
            base_xyz, base_quat = self._base_transforms[self._plan.joint_to_node[joint_name]]
            node_path.setPos(
                base_xyz[0] + pose.translation[0],
                base_xyz[1] + pose.translation[1],
                base_xyz[2] + pose.translation[2],
            )
            joint_quat = axis_angle_to_quat(pose.rotation_axis, pose.rotation_angle_rad)
            node_path.setQuat(LQuaternion(*multiply_quat(base_quat, joint_quat)))

    @staticmethod
    def _apply_transform(node_path: Any, transform: Transform) -> None:
        """Set the fixed transformation of a node.

        The rotation goes through a quaternion, not through HPR: converting rpy → HPR
        would mean guessing Panda3D's axis order convention, whereas `rpy_to_quat` is
        verified against a rotation matrix in `tests/unit/viz/test_transforms.py`.
        """
        from panda3d.core import LQuaternion

        node_path.setPos(*transform.xyz)
        node_path.setQuat(LQuaternion(*rpy_to_quat(transform.rpy)))

    def _update_task(self, _task: Any) -> Any:
        """One frame: snapshot → kinematics → scene.

        Must never raise an exception — this task dying means a frozen scene.
        """
        from direct.task import Task

        try:
            self._apply_snapshot()
        except Exception:
            logger.exception("error in the update task, carrying on")
        return Task.cont

    def _apply_snapshot(self) -> None:
        store = self._source.store
        # We render against the time of the data, not against the local clock: on replay
        # and on a lagging connection that is the only meaningful reference.
        latest = store.latest_time()
        if latest is None:
            return

        at_time = max(latest - self._render_delay_s, 0.0)
        self._stale_signals = store.stale_signals(
            time.monotonic(), self._loaded.source.stale_after_s
        )
        self.apply_values(store.sample_all(at_time))

    @property
    def status(self) -> SourceStatus:
        return self._source.status

    @property
    def scene_root(self) -> Any:
        """The root of the machine's scene. `None` until `build_scene()` has run."""
        return self._scene_root

    def node_path(self, path: str) -> Any | None:
        """The `NodePath` of a node by its stable path, or `None`.

        Serves the tests and a future UI (selecting a part in the tree → highlighting it
        in the scene).
        """
        return self._node_paths.get(path)

    @property
    def stale_signals(self) -> frozenset[str]:
        """The signals that have stopped arriving. The scene shows them at their last known value.

        Serves the HUD for marking them visually — the data is not discarded, only
        flagged.
        """
        return self._stale_signals

"""Tests for the joint-frame + binding mechanism `EmbeddedRenderer.add_joint`/
`bind_model`/`set_anchor`/`set_joint_value` build on.

`EmbeddedRenderer` itself needs a real native window handle to embed into
(`openDefaultWindow` bound to a parent) and is never instantiated directly in any
test in this codebase — even the sensor/floor features only ever test the pure
marker functions and the domain math, verifying the renderer itself with a real
running-app script instead. This file does the analogous thing for joints: it
builds the same `NodePath` structure those methods build — a **base** node at the
joint's origin, a **move** node under it carrying the live value, and an
**anchor** node seating a bound model's contact point on it — and confirms the
Panda3D behaviour they depend on.

`TestTheChainFromThePlan` is the case the whole hierarchy inversion exists for: a
rail carrying a rotation head carrying a tool, with every expected coordinate
derived by hand before any of the code was written.

The first two classes still exercise the plain parent/child transform
composition (a frame moving whatever hangs off it), which the design continues to
rest on regardless of who owns whom.

Requires `uv sync --extra viz`. Run with ``uv run pytest -m viz``.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from pssim.domain.machine import Transform
from pssim.domain.model_joints import (
    Anchor,
    ModelJoint,
    anchor_pose,
    direction_of,
    joint_value_pose,
    rotation_onto,
)
from pssim.viz.axes import BOX_EDGES, box_corners
from pssim.viz.camera import scene_radius
from pssim.viz.sensor_markers import aabb_of
from pssim.viz.transforms import axis_angle_to_quat, rpy_to_quat
from tests.factories import axis_joint, trajectory_joint

pytestmark = pytest.mark.viz


def scene_root() -> Any:
    from panda3d.core import NodePath

    return NodePath("scene")


def pos_of_point(
    node: Any, reference: Any, point: tuple[float, float, float]
) -> tuple[float, float, float]:
    """A point of `node`'s own frame, expressed in `reference`'s."""
    from panda3d.core import Point3

    converted = reference.getRelativePoint(node, Point3(*point))
    return (converted[0], converted[1], converted[2])


def pos_of(node: Any, reference: Any) -> tuple[float, float, float]:
    """`node`'s position relative to `reference`, as a plain tuple — Panda3D's
    `LPoint3f` does not compare cleanly against `pytest.approx` on a bare tuple."""
    point = node.getPos(reference)
    return (point[0], point[1], point[2])


def box_node(parent: Any, low: tuple[float, float, float], high: tuple[float, float, float]) -> Any:
    """A `NodePath` whose geometry spans exactly `low` to `high` — the same helper
    `test_viz_sensor_markers.py` uses, so `getTightBounds()` has a known answer."""
    from panda3d.core import LineSegs, NodePath

    corners = box_corners(low, high)
    lines = LineSegs("box")
    for start, end in BOX_EDGES:
        lines.moveTo(*corners[start])
        lines.drawTo(*corners[end])

    node = NodePath(lines.create())
    node.reparentTo(parent)
    return node


class TestMountedModelTracksTheJoint:
    def test_a_mounted_model_moves_with_its_parents_joint(self) -> None:
        scene = scene_root()
        model_a = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        frame = model_a.attachNewNode("joint-frame")
        model_b = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

        model_b.reparentTo(frame)  # mount
        frame.setPos(2.0, 0.0, 0.0)  # the joint's live value

        assert pos_of(model_b, scene) == pytest.approx((2.0, 0.0, 0.0))

    def test_moving_the_owning_model_carries_the_mount_point_with_it(self) -> None:
        scene = scene_root()
        model_a = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        frame = model_a.attachNewNode("joint-frame")
        model_b = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        model_b.reparentTo(frame)
        frame.setPos(2.0, 0.0, 0.0)

        model_a.setPos(5.0, 0.0, 0.0)  # the owning model's own placement changes

        assert pos_of(model_b, scene) == pytest.approx((7.0, 0.0, 0.0))

    def test_a_rotation_on_the_frame_carries_the_mounted_model(self) -> None:
        from panda3d.core import LQuaternion

        scene = scene_root()
        model_a = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        frame = model_a.attachNewNode("joint-frame")
        model_b = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        model_b.reparentTo(frame)
        model_b.setPos(1.0, 0.0, 0.0)  # its own placement, relative to the frame

        frame.setQuat(LQuaternion(*axis_angle_to_quat((0.0, 0.0, 1.0), math.pi / 2)))

        # A 90 degree turn about Z sends the X axis onto the Y axis.
        assert pos_of(model_b, scene) == pytest.approx((0.0, 1.0, 0.0), abs=1e-6)

    def test_detaching_the_mount_returns_the_model_to_the_scene_root(self) -> None:
        scene = scene_root()
        model_a = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        frame = model_a.attachNewNode("joint-frame")
        frame.setPos(2.0, 0.0, 0.0)
        model_b = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        model_b.reparentTo(frame)
        assert pos_of(model_b, scene) == pytest.approx((2.0, 0.0, 0.0))

        model_b.reparentTo(scene)  # unmount, mirrors mount_model(child, None)

        assert pos_of(model_b, scene) == pytest.approx((0.0, 0.0, 0.0))


class TestBoundsAreDepthAgnostic:
    def test_aabb_of_a_mounted_model_reflects_its_composed_position(self) -> None:
        scene = scene_root()
        model_a = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        frame = model_a.attachNewNode("joint-frame")
        frame.setPos(2.0, 0.0, 0.0)
        model_b = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        model_b.reparentTo(frame)

        box = aabb_of(model_b, scene)

        assert box is not None
        assert box.low == pytest.approx((2.0, 0.0, 0.0))
        assert box.high == pytest.approx((3.0, 1.0, 1.0))

    def test_scene_radius_accounts_for_a_model_nested_two_levels_deep(self) -> None:
        scene = scene_root()
        model_a = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        frame = model_a.attachNewNode("joint-frame")
        frame.setPos(10.0, 0.0, 0.0)
        model_b = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        model_b.reparentTo(frame)

        _center, radius = scene_radius(scene)

        # A model 10 units away, nested under a joint frame under another model,
        # must still be seen — a radius that ignored it would stay tiny.
        assert radius > 5.0


def make_joint_frames(parent: Any, joint: ModelJoint, name: str) -> tuple[Any, Any]:
    """The base/move pair `EmbeddedRenderer.add_joint` builds, in the same
    shape: base at the joint's origin carrying **no rotation**, move node
    underneath holding the live value."""
    base = parent.attachNewNode(f"{name}-base")
    base.setPos(*joint.origin)
    return base, base.attachNewNode(f"{name}-move")


def drive(move: Any, joint: ModelJoint, value: float) -> None:
    """What `set_joint_value` does: the joint's motion alone onto the move node."""
    from panda3d.core import LQuaternion

    pose = joint_value_pose(joint, value)
    move.setPos(*pose.translation)
    move.setQuat(LQuaternion(*axis_angle_to_quat(pose.rotation_axis, pose.rotation_angle_rad)))


def seat(move: Any, anchor: Anchor, joint: ModelJoint, name: str) -> Any:
    """What `_apply_anchor` does: the node that puts a model's anchor point on
    the joint and turns its anchor direction onto the joint's."""
    from panda3d.core import LQuaternion

    pose = anchor_pose(anchor, direction_of(joint))
    node = move.attachNewNode(f"{name}-anchor")
    node.setPos(*pose.translation)
    node.setQuat(LQuaternion(*axis_angle_to_quat(pose.rotation_axis, pose.rotation_angle_rad)))
    return node


class TestTheChainFromThePlan:
    """The exact rail -> head -> tool case the hierarchy inversion exists for,
    with the numbers hand-derived before any of it was written.

    A trajectory along world +X carries a rotation axis about world +Z; a tool
    is bound to the axis; a probe point sits 0.3 off the rotation axis.
    """

    def scene(self) -> tuple[Any, Any, Any, ModelJoint, ModelJoint, Any]:
        scene = scene_root()
        rail = trajectory_joint(name="rail", origin=(0.0, 0.0, 0.0), target=(5.0, 0.0, 0.0))
        head = axis_joint(name="head", origin=(0.0, 0.0, 1.0), target=(0.0, 0.0, 2.0))

        _rail_base, rail_move = make_joint_frames(scene, rail, "rail")
        _head_base, head_move = make_joint_frames(rail_move, head, "head")
        anchor_node = seat(head_move, Anchor(), head, "tool")
        tool = anchor_node.attachNewNode("tool")

        drive(rail_move, rail, 0.0)
        drive(head_move, head, 0.0)
        return scene, rail_move, head_move, rail, head, tool

    def test_at_rest_the_probe_sits_where_the_geometry_says(self) -> None:
        scene, _rail_move, _head_move, _rail, _head, tool = self.scene()

        assert pos_of_point(tool, scene, (0.3, 0.0, 0.0)) == pytest.approx(
            (0.3, 0.0, 1.0), abs=1e-6
        )

    def test_the_rail_carries_the_head_and_everything_on_it(self) -> None:
        scene, rail_move, _head_move, rail, _head, tool = self.scene()

        drive(rail_move, rail, 2.0)

        assert pos_of_point(tool, scene, (0.3, 0.0, 0.0)) == pytest.approx(
            (2.3, 0.0, 1.0), abs=1e-6
        )

    def test_the_head_turns_the_tool_about_its_own_axis(self) -> None:
        scene, rail_move, head_move, rail, head, tool = self.scene()

        drive(rail_move, rail, 2.0)
        drive(head_move, head, math.pi / 2)

        # A quarter turn about Z sends the probe from +X onto +Y, and the rail's
        # 2 along X still applies.
        assert pos_of_point(tool, scene, (0.3, 0.0, 0.0)) == pytest.approx(
            (2.0, 0.3, 1.0), abs=1e-6
        )

    def test_a_child_joints_coordinates_are_not_rotated_by_a_trajectory_parent(self) -> None:
        # Why the base node carries no alignment rotation: the head's origin of
        # (0,0,1) has to mean "one up", not "one along the rail".
        scene, _rail_move, _head_move, _rail, _head, tool = self.scene()

        assert pos_of_point(tool, scene, (0.0, 0.0, 0.0))[2] == pytest.approx(1.0, abs=1e-6)


class TestAnchorSeating:
    def test_an_anchor_point_off_the_model_origin_lands_on_the_joint(self) -> None:
        # The contact point the user picked is what sits on the trajectory.
        scene = scene_root()
        rail = trajectory_joint(name="rail", origin=(1.0, 0.0, 0.0), target=(1.0, 0.0, 2.0))
        _base, move = make_joint_frames(scene, rail, "rail")
        anchor = Anchor(point=(0.0, 0.0, 0.5), direction=(0.0, 0.0, 1.0))
        model = seat(move, anchor, rail, "model").attachNewNode("model")

        drive(move, rail, 1.5)

        assert pos_of_point(model, scene, anchor.point) == pytest.approx((1.0, 0.0, 1.5), abs=1e-6)

    def test_the_anchor_direction_ends_up_along_the_joint(self) -> None:
        scene = scene_root()
        rail = trajectory_joint(name="rail", origin=(0.0, 0.0, 0.0), target=(0.0, 0.0, 2.0))
        _base, move = make_joint_frames(scene, rail, "rail")
        anchor = Anchor(point=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0))
        model = seat(move, anchor, rail, "model").attachNewNode("model")

        from panda3d.core import Point3

        turned = scene.getRelativeVector(model, Point3(*anchor.direction))

        assert (turned[0], turned[1], turned[2]) == pytest.approx((0.0, 0.0, 1.0), abs=1e-6)

    def test_a_model_bound_by_a_turned_anchor_rotates_about_that_point(self) -> None:
        # An axis through the anchor point must leave that point still.
        scene = scene_root()
        head = axis_joint(name="head", origin=(2.0, 0.0, 0.0), target=(2.0, 0.0, 1.0))
        _base, move = make_joint_frames(scene, head, "head")
        anchor = Anchor(point=(0.4, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
        model = seat(move, anchor, head, "model").attachNewNode("model")

        drive(move, head, math.pi / 2)

        assert pos_of_point(model, scene, anchor.point) == pytest.approx((2.0, 0.0, 0.0), abs=1e-6)


def make_ics_frames(parent: Any, joint: ModelJoint, name: str) -> tuple[Any, Any]:
    """The base/move/tangent/frame chain `add_joint` builds, in the same shape.
    Returns the move node and the initial-coordinate-system node."""
    from panda3d.core import LQuaternion

    base = parent.attachNewNode(f"{name}-base")
    base.setPos(*joint.origin)
    move = base.attachNewNode(f"{name}-move")

    tangent = move.attachNewNode(f"{name}-tangent")
    axis, angle = rotation_onto((0.0, 0.0, 1.0), direction_of(joint))
    tangent.setQuat(LQuaternion(*axis_angle_to_quat(axis, angle)))

    frame = tangent.attachNewNode(f"{name}-frame")
    frame.setPos(*joint.alignment.xyz)
    frame.setQuat(LQuaternion(*rpy_to_quat(joint.alignment.rpy)))
    return move, frame


def seat_on_frame(frame: Any, anchor: Anchor, name: str) -> Any:
    """What `_apply_anchor` does now: seat the anchor on the frame's own `+Z`."""
    from panda3d.core import LQuaternion

    pose = anchor_pose(anchor)
    node = frame.attachNewNode(f"{name}-anchor")
    node.setPos(*pose.translation)
    node.setQuat(LQuaternion(*axis_angle_to_quat(pose.rotation_axis, pose.rotation_angle_rad)))
    return node.attachNewNode(name)


class TestInitialCoordinateSystem:
    """The measured table from the plan: a rail from (1,0,0) running +X, 4 m
    long, a default anchor, and a default (identity) initial frame."""

    def rail(self, alignment: Transform | None = None) -> ModelJoint:
        return trajectory_joint(
            name="rail",
            origin=(1.0, 0.0, 0.0),
            target=(5.0, 0.0, 0.0),
            alignment=alignment,
        )

    def test_the_default_frame_starts_at_the_trajectory(self) -> None:
        scene = scene_root()
        joint = self.rail()
        move, frame = make_ics_frames(scene, joint, "rail")
        model = seat_on_frame(frame, Anchor(), "model")

        drive(move, joint, 0.0)

        assert pos_of_point(model, scene, (0.0, 0.0, 0.0)) == pytest.approx(
            (1.0, 0.0, 0.0), abs=1e-6
        )

    def test_the_model_rides_the_trajectory(self) -> None:
        scene = scene_root()
        joint = self.rail()
        move, frame = make_ics_frames(scene, joint, "rail")
        model = seat_on_frame(frame, Anchor(), "model")

        for value, expected_x in ((0.0, 1.0), (2.0, 3.0), (4.0, 5.0)):
            drive(move, joint, value)
            assert pos_of_point(model, scene, (0.0, 0.0, 0.0)) == pytest.approx(
                (expected_x, 0.0, 0.0), abs=1e-6
            )

    def test_the_default_frame_is_tangential(self) -> None:
        # The user's words: "defaultne nastaveny na zaciatok trajektorie a byt
        # tangencialny k trajektorii".
        from panda3d.core import Point3

        scene = scene_root()
        joint = self.rail()
        _move, frame = make_ics_frames(scene, joint, "rail")
        model = seat_on_frame(frame, Anchor(), "model")

        forward = scene.getRelativeVector(model, Point3(0.0, 0.0, 1.0))

        assert (forward[0], forward[1], forward[2]) == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)

    def test_the_frames_z_runs_along_the_trajectory_not_upwards(self) -> None:
        # The consequence worth pinning: the frame's own axes are the tangential
        # frame's, so an offset on Z moves the model *along* the rail.
        scene = scene_root()
        joint = self.rail(alignment=Transform(xyz=(0.0, 0.0, 0.5)))
        move, frame = make_ics_frames(scene, joint, "rail")
        model = seat_on_frame(frame, Anchor(), "model")

        drive(move, joint, 2.0)

        assert pos_of_point(model, scene, (0.0, 0.0, 0.0)) == pytest.approx(
            (3.5, 0.0, 0.0), abs=1e-6
        )

    def test_rolling_the_frame_turns_the_model_about_the_trajectory(self) -> None:
        """What the ICS is for: the roll about the joint stops being arbitrary.

        A quarter turn about the frame's own **Z** — which is the rail direction —
        leaves the model still pointing along the rail but swings its
        perpendicular axes. That is exactly the degree of freedom `rotation_onto`
        leaves undetermined on its own.
        """
        from panda3d.core import Point3

        scene = scene_root()
        joint = self.rail(alignment=Transform(rpy=(0.0, 0.0, math.pi / 2)))
        _move, frame = make_ics_frames(scene, joint, "rail")
        model = seat_on_frame(frame, Anchor(), "model")

        forward = scene.getRelativeVector(model, Point3(0.0, 0.0, 1.0))
        assert (forward[0], forward[1], forward[2]) == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)

        sideways = scene.getRelativeVector(model, Point3(0.0, 1.0, 0.0))
        assert (sideways[0], sideways[1], sideways[2]) == pytest.approx((0.0, 0.0, 1.0), abs=1e-6)

    def test_pitching_the_frame_tilts_the_model_off_the_trajectory(self) -> None:
        # The other half of the same knob: a turn about an axis that is *not* the
        # trajectory direction genuinely aims the model elsewhere.
        from panda3d.core import Point3

        scene = scene_root()
        joint = self.rail(alignment=Transform(rpy=(math.pi / 2, 0.0, 0.0)))
        _move, frame = make_ics_frames(scene, joint, "rail")
        model = seat_on_frame(frame, Anchor(), "model")

        forward = scene.getRelativeVector(model, Point3(0.0, 0.0, 1.0))

        assert (forward[0], forward[1], forward[2]) == pytest.approx((0.0, -1.0, 0.0), abs=1e-6)

    def test_an_axis_gets_the_same_frame(self) -> None:
        # Applied to both kinds, not just trajectories: for an axis "tangential"
        # is along the rotation axis.
        from panda3d.core import Point3

        scene = scene_root()
        joint = axis_joint(name="head", origin=(0.0, 0.0, 1.0), target=(0.0, 2.0, 1.0))
        _move, frame = make_ics_frames(scene, joint, "head")
        model = seat_on_frame(frame, Anchor(), "model")

        forward = scene.getRelativeVector(model, Point3(0.0, 0.0, 1.0))

        assert (forward[0], forward[1], forward[2]) == pytest.approx((0.0, 1.0, 0.0), abs=1e-6)


class TestRebuildingTheFrame:
    def test_destroying_a_frame_orphans_whatever_hung_off_it(self) -> None:
        """The Panda3D behaviour the rebuild has to work around.

        `removeNode()` on an ancestor does not error and does not empty the
        descendant's handle — it silently takes it out of the scene. A bound
        model would just vanish, which is why `_build_joint_frame` parks its
        models on the scene root before replacing the frame.
        """
        scene = scene_root()
        joint = trajectory_joint(name="rail", target=(2.0, 0.0, 0.0))
        _move, frame = make_ics_frames(scene, joint, "rail")
        model = seat_on_frame(frame, Anchor(), "model")
        assert scene.isAncestorOf(model)

        frame.removeNode()

        assert model.isEmpty() is False
        assert scene.isAncestorOf(model) is False

"""Tests of the scene plan.

Splitting the geometry into static and moving is pure logic — tested without Panda3D
and without opening a window.
"""

from __future__ import annotations

import pytest

from pssim.domain.errors import ConfigError
from pssim.viz.scene_builder import plan_scene
from tests.factories import assembly, fixed_joint, machine, prismatic_joint, revolute_joint


class TestSplitting:
    def test_a_child_of_a_moving_joint_moves(self) -> None:
        # The children move together with the part, so they must not be flattened.
        plan = plan_scene(
            machine(prismatic_joint(parent="base", child="base/portal")),
            assembly("base", "base/portal", "base/portal/carriage"),
        )

        assert plan.moving_nodes == ("base/portal", "base/portal/carriage")

    def test_a_node_outside_a_moving_subtree_is_static(self) -> None:
        plan = plan_scene(
            machine(prismatic_joint(parent="base", child="base/portal")),
            assembly("base", "base/portal", "base/cover"),
        )

        assert plan.static_nodes == ("base", "base/cover")

    def test_a_fixed_joint_does_not_make_a_node_move(self) -> None:
        plan = plan_scene(
            machine(fixed_joint(parent="base", child="base/cover")),
            assembly("base", "base/cover"),
        )

        assert plan.moving_nodes == ()

    def test_prefix_nesmie_matchovat_ciastocne(self) -> None:
        # `base/portal2` nie je potomkom `base/portal`.
        plan = plan_scene(
            machine(prismatic_joint(parent="base", child="base/portal")),
            assembly("base", "base/portal", "base/portal2"),
        )

        assert "base/portal2" in plan.static_nodes

    def test_several_joints_in_a_chain(self) -> None:
        plan = plan_scene(
            machine(
                prismatic_joint(name="x", parent="base", child="base/portal"),
                revolute_joint(name="c", parent="base/portal", child="base/portal/head"),
            ),
            assembly("base", "base/portal", "base/portal/head"),
        )

        assert plan.moving_nodes == ("base/portal", "base/portal/head")


class TestMapping:
    def test_a_joint_points_at_its_child(self) -> None:
        plan = plan_scene(
            machine(prismatic_joint(name="axis_x", parent="base", child="base/portal")),
            assembly("base", "base/portal"),
        )

        assert plan.joint_to_node["axis_x"] == "base/portal"


class TestBadNodes:
    def test_a_missing_child_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="does not exist"):
            plan_scene(
                machine(prismatic_joint(parent="base", child="base/neexistuje")),
                assembly("base", "base/portal"),
            )

    def test_a_missing_parent_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="parent"):
            plan_scene(
                machine(prismatic_joint(parent="nieje", child="base")),
                assembly("base"),
            )

    def test_the_error_offers_similar_paths(self) -> None:
        # An assembly has a thousand nodes — without a hint the error cannot be resolved.
        with pytest.raises(ConfigError, match="Similar paths"):
            plan_scene(
                machine(prismatic_joint(parent="base", child="portal")),
                assembly("base", "base/portal"),
            )

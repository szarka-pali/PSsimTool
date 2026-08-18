"""Tests of machine model validation."""

from __future__ import annotations

import pytest

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Joint, JointType, Machine
from tests.factories import fixed_joint, machine, prismatic_joint, revolute_joint


class TestJoint:
    def test_an_unnormalised_axis_is_an_error(self) -> None:
        # The length of the vector would otherwise scale the movement unnoticed.
        with pytest.raises(ConfigError, match="not a unit vector"):
            Joint(name="a", parent="p", child="c", type=JointType.PRISMATIC, axis=(0.0, 0.0, 2.0))

    def test_a_fixed_joint_ignores_the_axis(self) -> None:
        joint = Joint(name="a", parent="p", child="c", type=JointType.FIXED, axis=(0.0, 0.0, 5.0))

        assert joint.type is JointType.FIXED

    def test_swapped_limits_are_an_error(self) -> None:
        with pytest.raises(ConfigError, match="greater than the upper"):
            prismatic_joint(limits=(2.0, 1.0))

    def test_an_empty_name_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="non-empty name"):
            prismatic_joint(name="")


class TestMachine:
    def test_a_duplicate_joint_name_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="duplicate joint name"):
            machine(prismatic_joint(name="a"), prismatic_joint(name="a", child="iny"))

    def test_a_node_with_two_parents_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="must be a tree"):
            machine(
                prismatic_joint(name="a", parent="p1", child="spolocny"),
                prismatic_joint(name="b", parent="p2", child="spolocny"),
            )

    def test_a_cycle_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="cycle"):
            machine(
                prismatic_joint(name="a", parent="x", child="y"),
                prismatic_joint(name="b", parent="y", child="x"),
            )

    def test_an_empty_machine_name_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="non-empty name"):
            Machine(name="", joints=(prismatic_joint(),))


class TestQueries:
    def test_moving_joints_vynecha_fixed(self) -> None:
        result = machine(prismatic_joint(name="a"), fixed_joint(name="b"))

        assert result.moving_joints == (result.joint("a"),)

    def test_an_unknown_joint_raises_with_a_list(self) -> None:
        with pytest.raises(ConfigError, match="available: axis_x"):
            machine(prismatic_joint(name="axis_x")).joint("neexistuje")

    def test_chain_to_root_returns_joints_from_node_to_root(self) -> None:
        result = machine(
            prismatic_joint(name="x", parent="base", child="portal"),
            revolute_joint(name="c", parent="portal", child="head"),
        )

        chain = result.chain_to_root("head")

        assert tuple(joint.name for joint in chain) == ("c", "x")

    def test_chain_to_root_of_the_root_is_empty(self) -> None:
        assert machine(prismatic_joint()).chain_to_root("base") == ()

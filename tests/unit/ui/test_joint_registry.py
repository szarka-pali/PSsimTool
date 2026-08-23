"""Tests for the placed-joint collection.

Pure state, so no Qt and no Panda3D. Mirrors `test_sensor_registry.py` — the
cases worth covering are the ones easy to get wrong by hand: a repeated name,
what happens to the selection when the selected joint is removed, and the
hierarchy of joints (`children_of`/`ancestors_of`/`would_cycle`) that is new
relative to both sensors and models.
"""

from __future__ import annotations

import math

import pytest

from pssim.ui.joint_registry import JointRegistry, descendants_of, would_cycle
from tests.factories import axis_joint


def registry_with(*names: str) -> JointRegistry:
    registry = JointRegistry()
    for name in names:
        registry.add(axis_joint(name=name))
    return registry


class TestAdding:
    def test_empty_at_start(self) -> None:
        assert JointRegistry().is_empty is True

    def test_adding_grows_the_registry(self) -> None:
        assert len(registry_with("a", "b")) == 2

    def test_ids_are_unique(self) -> None:
        registry = registry_with("gate", "gate", "gate")

        assert len({entry.joint_id for entry in registry}) == 3

    def test_insertion_order_is_preserved(self) -> None:
        assert registry_with("first", "second", "third").names == ("first", "second", "third")

    def test_a_repeated_name_gets_a_counter(self) -> None:
        assert registry_with("gate", "gate").names == ("gate", "gate (2)")

    def test_the_counter_skips_names_already_in_use(self) -> None:
        assert registry_with("gate", "gate", "gate").names == ("gate", "gate (2)", "gate (3)")

    def test_adding_selects_by_default(self) -> None:
        registry = JointRegistry()

        entry = registry.add(axis_joint())

        assert registry.selected_id == entry.joint_id

    def test_adding_without_selecting_keeps_the_selection(self) -> None:
        registry = registry_with("first")
        first_id = registry.selected_id

        registry.add(axis_joint(name="second"), select=False)

        assert registry.selected_id == first_id

    def test_a_joint_starts_in_the_scene(self) -> None:
        entry = JointRegistry().add(axis_joint())

        assert entry.parent_joint_id is None

    def test_a_joint_can_be_added_under_another(self) -> None:
        registry = JointRegistry()
        rail = registry.add(axis_joint(name="rail"))

        head = registry.add(axis_joint(name="head"), parent_joint_id=rail.joint_id)

        assert head.parent_joint_id == rail.joint_id


class TestRemoving:
    def test_removing_shrinks_the_registry(self) -> None:
        registry = registry_with("a")

        registry.remove(registry.entries[0].joint_id)

        assert registry.is_empty is True

    def test_removing_an_unknown_id_returns_none(self) -> None:
        assert JointRegistry().remove("joint-99") is None

    def test_removing_the_selection_moves_to_a_neighbour(self) -> None:
        registry = registry_with("a", "b")
        first_id, second_id = (entry.joint_id for entry in registry.entries)

        registry.remove(first_id)

        assert registry.selected_id == second_id

    def test_removing_the_last_joint_clears_the_selection(self) -> None:
        registry = registry_with("a")

        registry.remove(registry.entries[0].joint_id)

        assert registry.selected_id is None

    def test_clear_empties_the_registry_and_the_selection(self) -> None:
        registry = registry_with("a", "b")

        registry.clear()

        assert registry.is_empty is True
        assert registry.selected_id is None


class TestSelecting:
    def test_selecting_a_known_id_changes_it(self) -> None:
        # Adding selects the newest by default, so "b" already holds the
        # selection - select "a" instead to see a real change.
        registry = registry_with("a", "b")
        first_id, _ = (entry.joint_id for entry in registry.entries)

        assert registry.select(first_id) is True
        assert registry.selected_id == first_id

    def test_selecting_the_current_selection_reports_no_change(self) -> None:
        registry = registry_with("a")

        assert registry.select(registry.selected_id) is False

    def test_selecting_an_unknown_id_clears_the_selection(self) -> None:
        registry = registry_with("a")

        registry.select("joint-99")

        assert registry.selected_id is None


class TestReplacingJoint:
    def test_the_new_joint_replaces_the_old_one(self) -> None:
        registry = registry_with("gate")
        joint_id = registry.entries[0].joint_id
        edited = axis_joint(name="gate", origin=(1.0, 0.0, 0.0), target=(2.0, 0.0, 0.0))

        updated = registry.replace_joint(joint_id, edited)

        assert updated is not None
        assert updated.joint.origin == (1.0, 0.0, 0.0)

    def test_replacing_an_unknown_id_returns_none(self) -> None:
        assert JointRegistry().replace_joint("joint-99", axis_joint()) is None

    def test_renaming_into_a_taken_name_gets_a_counter(self) -> None:
        registry = registry_with("gate", "zone")
        gate_id = registry.entries[0].joint_id

        updated = registry.replace_joint(gate_id, axis_joint(name="zone"))

        assert updated is not None
        assert updated.joint.name == "zone (2)"

    def test_keeping_the_same_name_does_not_collide_with_itself(self) -> None:
        registry = registry_with("gate")
        joint_id = registry.entries[0].joint_id

        updated = registry.replace_joint(joint_id, axis_joint(name="gate"))

        assert updated is not None
        assert updated.joint.name == "gate"

    def test_the_id_survives_a_replace(self) -> None:
        registry = registry_with("gate")
        joint_id = registry.entries[0].joint_id

        registry.replace_joint(joint_id, axis_joint(name="gate"))

        assert registry.entries[0].joint_id == joint_id

    def test_replace_does_not_change_the_parent(self) -> None:
        registry = JointRegistry()
        rail = registry.add(axis_joint(name="rail"))
        head = registry.add(axis_joint(name="head"), parent_joint_id=rail.joint_id)

        updated = registry.replace_joint(head.joint_id, axis_joint(name="head"))

        assert updated is not None
        assert updated.parent_joint_id == rail.joint_id


class TestValue:
    def test_a_new_joint_starts_at_zero_when_that_is_within_range(self) -> None:
        registry = JointRegistry()

        entry = registry.add(axis_joint(limits=(-1.0, 1.0)))

        assert entry.value == pytest.approx(0.0)

    def test_a_new_joint_starts_at_the_nearest_limit_when_zero_is_out_of_range(self) -> None:
        registry = JointRegistry()

        entry = registry.add(axis_joint(limits=(0.5, 1.0)))

        assert entry.value == pytest.approx(0.5)

    def test_set_value_updates_the_entry(self) -> None:
        registry = JointRegistry()
        entry = registry.add(axis_joint())

        updated = registry.set_value(entry.joint_id, 1.5)

        assert updated is not None
        assert updated.value == pytest.approx(1.5)

    def test_setting_the_same_value_is_a_no_op(self) -> None:
        registry = JointRegistry()
        entry = registry.add(axis_joint())
        registry.set_value(entry.joint_id, 1.5)

        assert registry.set_value(entry.joint_id, 1.5) is None

    def test_setting_an_unknown_id_is_a_no_op(self) -> None:
        assert JointRegistry().set_value("joint-99", 1.0) is None

    def test_replacing_a_joint_reclamps_the_value_to_the_new_limits(self) -> None:
        registry = JointRegistry()
        entry = registry.add(axis_joint(limits=None))
        registry.set_value(entry.joint_id, math.pi - 0.01)

        updated = registry.replace_joint(entry.joint_id, axis_joint(name="gate", limits=(0.0, 1.0)))

        assert updated is not None
        assert updated.value == pytest.approx(1.0)

    def test_replacing_a_joint_keeps_an_in_range_value(self) -> None:
        registry = JointRegistry()
        entry = registry.add(axis_joint(limits=(0.0, 2.0)))
        registry.set_value(entry.joint_id, 1.0)

        updated = registry.replace_joint(entry.joint_id, axis_joint(name="gate", limits=(0.0, 2.0)))

        assert updated is not None
        assert updated.value == pytest.approx(1.0)


def chain(*names: str) -> tuple[JointRegistry, tuple[str, ...]]:
    """A registry holding one chain: each joint carried by the previous one.
    Returns the registry and the ids, outermost first."""
    registry = JointRegistry()
    ids: list[str] = []
    parent: str | None = None
    for name in names:
        entry = registry.add(axis_joint(name=name), parent_joint_id=parent)
        ids.append(entry.joint_id)
        parent = entry.joint_id
    return registry, tuple(ids)


class TestChildrenOf:
    def test_top_level_joints_are_children_of_none(self) -> None:
        registry = registry_with("a", "b")

        assert len(registry.children_of(None)) == 2

    def test_a_child_is_not_top_level(self) -> None:
        registry, (rail, _head) = chain("rail", "head")

        assert tuple(e.joint_id for e in registry.children_of(None)) == (rail,)

    def test_children_are_listed_under_their_parent(self) -> None:
        registry, (rail, head) = chain("rail", "head")

        assert tuple(e.joint_id for e in registry.children_of(rail)) == (head,)

    def test_a_leaf_has_no_children(self) -> None:
        registry, (_rail, head) = chain("rail", "head")

        assert registry.children_of(head) == ()

    def test_insertion_order_is_preserved_among_siblings(self) -> None:
        registry = JointRegistry()
        rail = registry.add(axis_joint(name="rail"))
        registry.add(axis_joint(name="first"), parent_joint_id=rail.joint_id)
        registry.add(axis_joint(name="second"), parent_joint_id=rail.joint_id)

        names = tuple(e.joint.name for e in registry.children_of(rail.joint_id))
        assert names == ("first", "second")


class TestAncestorsOf:
    def test_a_top_level_joint_is_its_own_whole_chain(self) -> None:
        registry = registry_with("rail")
        joint_id = registry.entries[0].joint_id

        assert tuple(e.joint_id for e in registry.ancestors_of(joint_id)) == (joint_id,)

    def test_the_chain_runs_nearest_first(self) -> None:
        registry, (rail, head, tool) = chain("rail", "head", "tool")

        assert tuple(e.joint_id for e in registry.ancestors_of(tool)) == (tool, head, rail)

    def test_an_unknown_joint_has_no_chain(self) -> None:
        assert JointRegistry().ancestors_of("joint-99") == ()


class TestSetParent:
    def test_hanging_a_joint_under_another_reports_the_change(self) -> None:
        registry = registry_with("rail", "head")
        rail, head = (e.joint_id for e in registry.entries)

        assert registry.set_parent(head, rail) is True

        entry = registry.get(head)
        assert entry is not None
        assert entry.parent_joint_id == rail

    def test_setting_the_same_parent_reports_no_change(self) -> None:
        registry, (rail, head) = chain("rail", "head")

        assert registry.set_parent(head, rail) is False

    def test_releasing_to_the_scene_with_none(self) -> None:
        registry, (_rail, head) = chain("rail", "head")

        assert registry.set_parent(head, None) is True

        entry = registry.get(head)
        assert entry is not None
        assert entry.parent_joint_id is None

    def test_setting_the_parent_of_an_unknown_joint_is_harmless(self) -> None:
        assert JointRegistry().set_parent("joint-99", None) is False


class TestRemovingKeepsChildren:
    def test_a_child_survives_its_parent_and_moves_up(self) -> None:
        # Removing a rail must not silently delete the head that sat on it.
        registry, (rail, head) = chain("rail", "head")

        registry.remove(rail)

        entry = registry.get(head)
        assert entry is not None
        assert entry.parent_joint_id is None

    def test_a_grandchild_keeps_its_own_parent(self) -> None:
        registry, (rail, head, tool) = chain("rail", "head", "tool")

        registry.remove(rail)

        tool_entry = registry.get(tool)
        assert tool_entry is not None
        assert tool_entry.parent_joint_id == head

    def test_removing_a_middle_joint_reattaches_to_the_grandparent(self) -> None:
        registry, (rail, head, tool) = chain("rail", "head", "tool")

        registry.remove(head)

        tool_entry = registry.get(tool)
        assert tool_entry is not None
        assert tool_entry.parent_joint_id == rail


class TestWouldCycle:
    def test_the_scene_is_never_a_cycle(self) -> None:
        registry, (_rail, head) = chain("rail", "head")

        assert would_cycle(registry, head, None) is False

    def test_an_unrelated_parent_is_fine(self) -> None:
        registry = registry_with("a", "b")
        first, second = (e.joint_id for e in registry.entries)

        assert would_cycle(registry, first, second) is False

    def test_a_joint_cannot_carry_itself(self) -> None:
        registry = registry_with("a")
        joint_id = registry.entries[0].joint_id

        assert would_cycle(registry, joint_id, joint_id) is True

    def test_a_joint_cannot_be_carried_by_its_own_child(self) -> None:
        registry, (rail, head) = chain("rail", "head")

        assert would_cycle(registry, rail, head) is True

    def test_a_joint_cannot_be_carried_by_a_distant_descendant(self) -> None:
        registry, (rail, _head, tool) = chain("rail", "head", "tool")

        assert would_cycle(registry, rail, tool) is True

    def test_an_unknown_parent_is_not_a_cycle(self) -> None:
        registry = registry_with("a")

        assert would_cycle(registry, registry.entries[0].joint_id, "joint-99") is False


class TestDescendantsOf:
    def test_a_leaf_has_none(self) -> None:
        registry, (_rail, head) = chain("rail", "head")

        assert descendants_of(registry, head) == frozenset()

    def test_every_depth_is_included(self) -> None:
        registry, (rail, head, tool) = chain("rail", "head", "tool")

        assert descendants_of(registry, rail) == frozenset({head, tool})

    def test_a_joint_is_not_its_own_descendant(self) -> None:
        registry, (rail, _head) = chain("rail", "head")

        assert rail not in descendants_of(registry, rail)

    def test_siblings_are_not_descendants_of_each_other(self) -> None:
        registry = JointRegistry()
        rail = registry.add(axis_joint(name="rail"))
        left = registry.add(axis_joint(name="left"), parent_joint_id=rail.joint_id)
        right = registry.add(axis_joint(name="right"), parent_joint_id=rail.joint_id)

        assert descendants_of(registry, left.joint_id) == frozenset()
        assert right.joint_id not in descendants_of(registry, left.joint_id)


class TestAxesVisibility:
    def test_the_cross_starts_shown(self) -> None:
        registry = JointRegistry()

        assert registry.add(axis_joint()).show_axes is True

    def test_hiding_the_cross_is_remembered(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id

        registry.set_axes_visible(joint_id, False)

        entry = registry.get(joint_id)
        assert entry is not None
        assert entry.show_axes is False

    def test_hiding_the_cross_reports_the_change(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id

        assert registry.set_axes_visible(joint_id, False) is True

    def test_hiding_the_cross_twice_reports_no_change(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id
        registry.set_axes_visible(joint_id, False)

        assert registry.set_axes_visible(joint_id, False) is False

    def test_hiding_the_cross_on_an_unknown_joint_is_harmless(self) -> None:
        assert JointRegistry().set_axes_visible("nonsense", False) is False

    def test_each_joint_has_its_own_setting(self) -> None:
        registry = JointRegistry()
        rail = registry.add(axis_joint(name="rail")).joint_id
        turn = registry.add(axis_joint(name="turn")).joint_id

        registry.set_axes_visible(rail, False)

        entry = registry.get(turn)
        assert entry is not None
        assert entry.show_axes is True


class TestNameVisibility:
    def test_a_name_starts_shown(self) -> None:
        registry = JointRegistry()

        assert registry.add(axis_joint()).show_name is True

    def test_hiding_a_name_is_remembered(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id

        registry.set_name_visible(joint_id, False)

        entry = registry.get(joint_id)
        assert entry is not None
        assert entry.show_name is False

    def test_hiding_a_name_reports_the_change(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id

        assert registry.set_name_visible(joint_id, False) is True

    def test_hiding_a_name_twice_reports_no_change(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id
        registry.set_name_visible(joint_id, False)

        assert registry.set_name_visible(joint_id, False) is False

    def test_hiding_a_name_on_an_unknown_joint_is_harmless(self) -> None:
        assert JointRegistry().set_name_visible("nonsense", False) is False

    def test_the_name_and_the_cross_are_independent(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id

        registry.set_name_visible(joint_id, False)

        entry = registry.get(joint_id)
        assert entry is not None
        assert entry.show_axes is True


class TestJointColor:
    def test_a_joint_starts_with_no_override(self) -> None:
        registry = JointRegistry()

        assert registry.add(axis_joint()).color is None

    def test_a_colour_is_remembered(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id

        registry.set_color(joint_id, (1.0, 0.5, 0.0, 1.0))

        entry = registry.get(joint_id)
        assert entry is not None
        assert entry.color == (1.0, 0.5, 0.0, 1.0)

    def test_clearing_it_goes_back_to_no_override(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id
        registry.set_color(joint_id, (1.0, 0.5, 0.0, 1.0))

        registry.set_color(joint_id, None)

        entry = registry.get(joint_id)
        assert entry is not None
        assert entry.color is None

    def test_setting_the_same_colour_reports_no_change(self) -> None:
        registry = JointRegistry()
        joint_id = registry.add(axis_joint()).joint_id
        registry.set_color(joint_id, (1.0, 0.5, 0.0, 1.0))

        assert registry.set_color(joint_id, (1.0, 0.5, 0.0, 1.0)) is False

    def test_colouring_an_unknown_joint_is_harmless(self) -> None:
        assert JointRegistry().set_color("nonsense", (1.0, 0.0, 0.0, 1.0)) is False

    def test_a_colour_survives_being_re_parented(self) -> None:
        registry = JointRegistry()
        rail = registry.add(axis_joint(name="rail")).joint_id
        turn = registry.add(axis_joint(name="turn")).joint_id
        registry.set_color(turn, (0.0, 0.0, 1.0, 1.0))

        registry.set_parent(turn, rail)

        entry = registry.get(turn)
        assert entry is not None
        assert entry.color == (0.0, 0.0, 1.0, 1.0)

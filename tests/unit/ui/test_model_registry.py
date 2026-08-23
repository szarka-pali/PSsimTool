"""Tests for the loaded-model collection.

Pure state, so no Qt and no Panda3D. The cases worth covering are the ones that
are easy to get wrong by hand: the same file loaded twice, and what happens to
the selection when the selected model is removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pssim.domain.machine import Transform
from pssim.domain.model_joints import Anchor
from pssim.ui.model_registry import ModelRegistry


def registry_with(*names: str) -> ModelRegistry:
    registry = ModelRegistry()
    for name in names:
        registry.add(Path(f"C:/models/{name}.step"))
    return registry


class TestAdding:
    def test_empty_at_start(self) -> None:
        assert ModelRegistry().is_empty is True

    def test_adding_grows_the_registry(self) -> None:
        assert len(registry_with("a", "b")) == 2

    def test_name_comes_from_the_file_stem(self) -> None:
        entry = ModelRegistry().add(Path("C:/models/gantry.step"))

        assert entry.name == "gantry"

    def test_path_is_kept(self) -> None:
        path = Path("C:/models/gantry.step")

        assert ModelRegistry().add(path).path == path

    def test_ids_are_unique(self) -> None:
        registry = registry_with("a", "a", "a")

        assert len({entry.model_id for entry in registry}) == 3

    def test_insertion_order_is_preserved(self) -> None:
        # The tree lists models in the order they were opened.
        assert registry_with("first", "second", "third").names == (
            "first",
            "second",
            "third",
        )

    def test_counts_are_stored(self) -> None:
        entry = ModelRegistry().add(Path("a.step"), node_count=6, triangle_count=48)

        assert (entry.node_count, entry.triangle_count) == (6, 48)

    def test_adding_selects_by_default(self) -> None:
        registry = ModelRegistry()

        entry = registry.add(Path("a.step"))

        assert registry.selected_id == entry.model_id

    def test_adding_without_selecting_keeps_selection(self) -> None:
        registry = ModelRegistry()
        first = registry.add(Path("a.step"))

        registry.add(Path("b.step"), select=False)

        assert registry.selected_id == first.model_id


class TestDuplicateNames:
    def test_second_copy_gets_a_counter(self) -> None:
        # A machine can legitimately contain ten of the same part.
        assert registry_with("bolt", "bolt").names == ("bolt", "bolt (2)")

    def test_third_copy_continues_counting(self) -> None:
        assert registry_with("bolt", "bolt", "bolt").names[2] == "bolt (3)"

    def test_counter_fills_a_freed_name(self) -> None:
        registry = registry_with("bolt", "bolt")
        second = registry.entries[1]
        registry.remove(second.model_id)

        registry.add(Path("bolt.step"))

        assert registry.names == ("bolt", "bolt (2)")

    def test_different_names_are_untouched(self) -> None:
        assert registry_with("a", "b").names == ("a", "b")


class TestSelection:
    def test_nothing_selected_in_an_empty_registry(self) -> None:
        assert ModelRegistry().selected is None

    def test_selecting_returns_the_entry(self) -> None:
        registry = registry_with("a", "b")
        first = registry.entries[0]

        registry.select(first.model_id)

        assert registry.selected == first

    def test_selecting_reports_a_change(self) -> None:
        registry = registry_with("a", "b")

        assert registry.select(registry.entries[0].model_id) is True

    def test_reselecting_reports_no_change(self) -> None:
        # The tree emits selection signals freely; the window must be able to
        # ignore the ones that change nothing.
        registry = registry_with("a")

        assert registry.select(registry.selected_id) is False

    def test_unknown_id_clears_the_selection(self) -> None:
        registry = registry_with("a")

        registry.select("nonsense")

        assert registry.selected is None

    def test_none_clears_the_selection(self) -> None:
        registry = registry_with("a")

        registry.select(None)

        assert registry.selected_id is None


class TestRemoving:
    def test_removing_shrinks_the_registry(self) -> None:
        registry = registry_with("a", "b")

        registry.remove(registry.entries[0].model_id)

        assert len(registry) == 1

    def test_removing_returns_the_entry(self) -> None:
        registry = registry_with("a")
        entry = registry.entries[0]

        assert registry.remove(entry.model_id) == entry

    def test_removing_an_unknown_id_is_harmless(self) -> None:
        registry = registry_with("a")

        assert registry.remove("nonsense") is None
        assert len(registry) == 1

    def test_removing_the_selection_moves_to_a_neighbour(self) -> None:
        # After deleting one of several models the user carries on with another.
        registry = registry_with("a", "b")
        registry.select(registry.entries[1].model_id)

        registry.remove(registry.entries[1].model_id)

        assert registry.selected is not None

    def test_removing_the_last_model_clears_the_selection(self) -> None:
        registry = registry_with("a")

        registry.remove(registry.entries[0].model_id)

        assert registry.selected is None

    def test_removing_an_unselected_model_keeps_the_selection(self) -> None:
        registry = registry_with("a", "b")
        selected = registry.selected_id

        registry.remove(registry.entries[0].model_id)

        assert registry.selected_id == selected

    def test_clear_empties_everything(self) -> None:
        registry = registry_with("a", "b")

        registry.clear()

        assert registry.is_empty
        assert registry.selected is None


class TestPlacement:
    def test_new_model_sits_at_the_origin(self) -> None:
        assert ModelRegistry().add(Path("a.step")).is_placed is False

    def test_placement_is_stored_per_model(self) -> None:
        registry = registry_with("a", "b")
        first, second = registry.entries

        registry.set_placement(first.model_id, Transform(xyz=(1.0, 0.0, 0.0)))

        moved = registry.get(first.model_id)
        untouched = registry.get(second.model_id)
        assert moved is not None
        assert untouched is not None
        assert moved.placement.xyz == (1.0, 0.0, 0.0)
        assert untouched.placement.xyz == (0.0, 0.0, 0.0)

    def test_placed_model_reports_it(self) -> None:
        registry = registry_with("a")
        entry = registry.entries[0]

        updated = registry.set_placement(entry.model_id, Transform(rpy=(0.0, 0.0, 1.0)))

        assert updated is not None
        assert updated.is_placed is True

    def test_placing_an_unknown_model_is_harmless(self) -> None:
        assert ModelRegistry().set_placement("nonsense", Transform()) is None

    def test_entries_are_immutable(self) -> None:
        # A stale reference must not silently disagree with the registry.
        registry = registry_with("a")
        entry = registry.entries[0]

        registry.set_placement(entry.model_id, Transform(xyz=(5.0, 0.0, 0.0)))

        assert entry.placement.xyz == (0.0, 0.0, 0.0)

    def test_placement_does_not_change_identity(self) -> None:
        registry = registry_with("a")
        entry = registry.entries[0]

        updated = registry.set_placement(entry.model_id, Transform(xyz=(1.0, 2.0, 3.0)))

        assert updated is not None
        assert updated.model_id == entry.model_id
        assert updated.name == entry.name


class TestMembership:
    def test_known_id_is_contained(self) -> None:
        registry = registry_with("a")

        assert registry.entries[0].model_id in registry

    def test_unknown_id_is_not_contained(self) -> None:
        assert "nonsense" not in registry_with("a")

    def test_get_returns_none_for_unknown(self) -> None:
        assert registry_with("a").get("nonsense") is None

    def test_iteration_yields_entries(self) -> None:
        registry = registry_with("a", "b")

        assert [entry.name for entry in registry] == ["a", "b"]


@pytest.mark.parametrize("count", [1, 5, 20])
def test_many_copies_all_get_distinct_names(count: int) -> None:
    registry = ModelRegistry()
    for _ in range(count):
        registry.add(Path("bolt.step"))

    assert len(set(registry.names)) == count


class TestBinding:
    def test_a_new_model_is_bound_to_nothing(self) -> None:
        assert ModelRegistry().add(Path("a.step")).bound_to_joint_id is None

    def test_binding_stores_the_joint_id(self) -> None:
        registry = registry_with("a")
        model_id = registry.entries[0].model_id

        assert registry.bind(model_id, "joint-1") is True
        entry = registry.get(model_id)
        assert entry is not None
        assert entry.bound_to_joint_id == "joint-1"

    def test_binding_the_same_joint_again_reports_no_change(self) -> None:
        registry = registry_with("a")
        model_id = registry.entries[0].model_id
        registry.bind(model_id, "joint-1")

        assert registry.bind(model_id, "joint-1") is False

    def test_releasing_with_none_clears_it(self) -> None:
        registry = registry_with("a")
        model_id = registry.entries[0].model_id
        registry.bind(model_id, "joint-1")

        registry.bind(model_id, None)

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.bound_to_joint_id is None

    def test_binding_an_unknown_model_is_harmless(self) -> None:
        assert ModelRegistry().bind("nonsense", "joint-1") is False

    def test_binding_does_not_disturb_the_placement(self) -> None:
        registry = registry_with("a")
        model_id = registry.entries[0].model_id
        registry.set_placement(model_id, Transform(xyz=(1.0, 0.0, 0.0)))

        registry.bind(model_id, "joint-1")

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.placement.xyz == (1.0, 0.0, 0.0)


class TestAnchor:
    def test_a_new_model_anchors_at_its_own_origin(self) -> None:
        entry = ModelRegistry().add(Path("a.step"))

        assert entry.anchor == Anchor()

    def test_setting_an_anchor_stores_it(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        anchor = Anchor(point=(0.1, 0.0, 0.0), direction=(1.0, 0.0, 0.0))

        assert registry.set_anchor(model_id, anchor) is True

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.anchor == anchor

    def test_setting_the_same_anchor_reports_no_change(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        anchor = Anchor(point=(0.1, 0.0, 0.0))
        registry.set_anchor(model_id, anchor)

        assert registry.set_anchor(model_id, anchor) is False

    def test_setting_an_anchor_on_an_unknown_model_is_harmless(self) -> None:
        assert ModelRegistry().set_anchor("nonsense", Anchor()) is False

    def test_the_anchor_survives_a_rename(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        registry.set_anchor(model_id, Anchor(point=(0.1, 0.0, 0.0)))

        registry.rename(model_id, "renamed")

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.anchor.point == (0.1, 0.0, 0.0)


class TestVisibility:
    def test_a_model_starts_visible(self) -> None:
        registry = ModelRegistry()

        assert registry.add(Path("a.step")).is_visible is True

    def test_hiding_a_model_is_remembered(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id

        registry.set_visible(model_id, False)

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.is_visible is False

    def test_hiding_reports_the_change(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id

        assert registry.set_visible(model_id, False) is True

    def test_hiding_twice_reports_no_change(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        registry.set_visible(model_id, False)

        assert registry.set_visible(model_id, False) is False

    def test_hiding_an_unknown_model_is_harmless(self) -> None:
        assert ModelRegistry().set_visible("nonsense", False) is False

    def test_visibility_survives_a_rename(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        registry.set_visible(model_id, False)

        registry.rename(model_id, "renamed")

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.is_visible is False


class TestAxesVisibility:
    def test_the_cross_starts_shown(self) -> None:
        registry = ModelRegistry()

        assert registry.add(Path("a.step")).show_axes is True

    def test_hiding_the_cross_is_remembered(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id

        registry.set_axes_visible(model_id, False)

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.show_axes is False

    def test_hiding_the_cross_twice_reports_no_change(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        registry.set_axes_visible(model_id, False)

        assert registry.set_axes_visible(model_id, False) is False

    def test_hiding_the_cross_on_an_unknown_model_is_harmless(self) -> None:
        assert ModelRegistry().set_axes_visible("nonsense", False) is False

    def test_the_cross_and_the_model_are_independent(self) -> None:
        # Hiding a cross must not hide the model it belongs to.
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id

        registry.set_axes_visible(model_id, False)

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.is_visible is True


class TestColor:
    def test_a_model_starts_with_no_override(self) -> None:
        registry = ModelRegistry()

        assert registry.add(Path("a.step")).color is None

    def test_a_colour_is_remembered(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id

        registry.set_color(model_id, (1.0, 0.0, 0.0, 1.0))

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.color == (1.0, 0.0, 0.0, 1.0)

    def test_setting_a_colour_reports_the_change(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id

        assert registry.set_color(model_id, (1.0, 0.0, 0.0, 1.0)) is True

    def test_setting_the_same_colour_reports_no_change(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        registry.set_color(model_id, (1.0, 0.0, 0.0, 1.0))

        assert registry.set_color(model_id, (1.0, 0.0, 0.0, 1.0)) is False

    def test_clearing_it_goes_back_to_no_override(self) -> None:
        # `None` has to be reachable: it is the only way back to the colours the
        # STEP file carries.
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        registry.set_color(model_id, (1.0, 0.0, 0.0, 1.0))

        registry.set_color(model_id, None)

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.color is None

    def test_clearing_an_absent_override_reports_no_change(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id

        assert registry.set_color(model_id, None) is False

    def test_colouring_an_unknown_model_is_harmless(self) -> None:
        assert ModelRegistry().set_color("nonsense", (1.0, 0.0, 0.0, 1.0)) is False

    def test_a_colour_survives_a_rename(self) -> None:
        registry = ModelRegistry()
        model_id = registry.add(Path("a.step")).model_id
        registry.set_color(model_id, (0.0, 1.0, 0.0, 1.0))

        registry.rename(model_id, "renamed")

        entry = registry.get(model_id)
        assert entry is not None
        assert entry.color == (0.0, 1.0, 0.0, 1.0)

    def test_each_model_has_its_own_colour(self) -> None:
        registry = ModelRegistry()
        first = registry.add(Path("a.step")).model_id
        second = registry.add(Path("b.step")).model_id

        registry.set_color(first, (1.0, 0.0, 0.0, 1.0))

        entry = registry.get(second)
        assert entry is not None
        assert entry.color is None

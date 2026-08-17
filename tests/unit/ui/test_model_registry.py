"""Tests for the loaded-model collection.

Pure state, so no Qt and no Panda3D. The cases worth covering are the ones that
are easy to get wrong by hand: the same file loaded twice, and what happens to
the selection when the selected model is removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pssim.domain.machine import Transform
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


class TestRenaming:
    def test_name_changes(self) -> None:
        registry = registry_with("gantry")
        model_id = registry.entries[0].model_id

        assert registry.rename(model_id, "conveyor") is not None
        assert registry.names == ("conveyor",)

    def test_updated_entry_is_returned(self) -> None:
        registry = registry_with("gantry")

        updated = registry.rename(registry.entries[0].model_id, "conveyor")

        assert updated is not None
        assert updated.name == "conveyor"

    def test_surrounding_whitespace_is_dropped(self) -> None:
        registry = registry_with("gantry")

        updated = registry.rename(registry.entries[0].model_id, "  conveyor  ")

        assert updated is not None
        assert updated.name == "conveyor"

    def test_blank_name_is_refused(self) -> None:
        # An unnamed model would be a row in the tree with nothing to click on.
        registry = registry_with("gantry")

        assert registry.rename(registry.entries[0].model_id, "   ") is None

    def test_refused_rename_leaves_the_name_alone(self) -> None:
        registry = registry_with("gantry")

        registry.rename(registry.entries[0].model_id, "")

        assert registry.names == ("gantry",)

    def test_unknown_id_is_refused(self) -> None:
        assert registry_with("gantry").rename("model-99", "conveyor") is None

    def test_renaming_to_the_same_name_is_allowed(self) -> None:
        # Reopening the dialog and pressing OK must not add a counter suffix.
        registry = registry_with("gantry")

        updated = registry.rename(registry.entries[0].model_id, "gantry")

        assert updated is not None
        assert updated.name == "gantry"

    def test_a_taken_name_gets_a_counter(self) -> None:
        registry = registry_with("gantry", "conveyor")

        updated = registry.rename(registry.entries[1].model_id, "gantry")

        assert updated is not None
        assert updated.name == "gantry (2)"

    def test_counter_skips_names_already_in_use(self) -> None:
        registry = registry_with("gantry", "gantry", "conveyor")

        updated = registry.rename(registry.entries[2].model_id, "gantry")

        assert updated is not None
        assert updated.name == "gantry (3)"

    def test_placement_survives_a_rename(self) -> None:
        registry = registry_with("gantry")
        model_id = registry.entries[0].model_id
        registry.set_placement(model_id, Transform(xyz=(0.3, 0.0, 0.0)))

        updated = registry.rename(model_id, "conveyor")

        assert updated is not None
        assert updated.placement.xyz[0] == pytest.approx(0.3)

    def test_id_survives_a_rename(self) -> None:
        # The renderer refers to models by id; a rename must not disturb it.
        registry = registry_with("gantry")
        model_id = registry.entries[0].model_id

        registry.rename(model_id, "conveyor")

        assert registry.entries[0].model_id == model_id

    def test_selection_survives_a_rename(self) -> None:
        registry = registry_with("gantry", "conveyor")
        model_id = registry.selected_id
        assert model_id is not None

        registry.rename(model_id, "head")

        assert registry.selected_id == model_id

    def test_selected_name_follows_the_rename(self) -> None:
        # What a project file stores, so it has to be the new name.
        registry = registry_with("gantry")
        model_id = registry.entries[0].model_id

        registry.rename(model_id, "conveyor")

        assert registry.selected_name == "conveyor"

    def test_order_survives_a_rename(self) -> None:
        registry = registry_with("first", "second")

        registry.rename(registry.entries[0].model_id, "renamed")

        assert registry.names == ("renamed", "second")

    def test_a_freed_name_can_be_reused(self) -> None:
        registry = registry_with("gantry", "conveyor")
        registry.rename(registry.entries[0].model_id, "head")

        updated = registry.rename(registry.entries[1].model_id, "gantry")

        assert updated is not None
        assert updated.name == "gantry"

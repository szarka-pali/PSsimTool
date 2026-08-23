"""Tests for the placed-sensor collection.

Pure state, so no Qt and no Panda3D. Mirrors `test_model_registry.py` — the
cases worth covering are the ones easy to get wrong by hand: a repeated name,
and what happens to the selection when the selected sensor is removed.
"""

from __future__ import annotations

from pssim.ui.sensor_registry import SensorRegistry
from tests.factories import beam_sensor


def registry_with(*names: str) -> SensorRegistry:
    registry = SensorRegistry()
    for name in names:
        registry.add(beam_sensor(name=name))
    return registry


class TestAdding:
    def test_empty_at_start(self) -> None:
        assert SensorRegistry().is_empty is True

    def test_adding_grows_the_registry(self) -> None:
        assert len(registry_with("a", "b")) == 2

    def test_ids_are_unique(self) -> None:
        registry = registry_with("gate", "gate", "gate")

        assert len({entry.sensor_id for entry in registry}) == 3

    def test_insertion_order_is_preserved(self) -> None:
        assert registry_with("first", "second", "third").names == ("first", "second", "third")

    def test_a_repeated_name_gets_a_counter(self) -> None:
        assert registry_with("gate", "gate").names == ("gate", "gate (2)")

    def test_the_counter_skips_names_already_in_use(self) -> None:
        assert registry_with("gate", "gate", "gate").names == ("gate", "gate (2)", "gate (3)")

    def test_adding_selects_by_default(self) -> None:
        registry = SensorRegistry()

        entry = registry.add(beam_sensor())

        assert registry.selected_id == entry.sensor_id

    def test_adding_without_selecting_keeps_the_selection(self) -> None:
        registry = registry_with("first")
        first_id = registry.selected_id

        registry.add(beam_sensor(name="second"), select=False)

        assert registry.selected_id == first_id


class TestRemoving:
    def test_removing_shrinks_the_registry(self) -> None:
        registry = registry_with("a")

        registry.remove(registry.entries[0].sensor_id)

        assert registry.is_empty is True

    def test_removing_an_unknown_id_returns_none(self) -> None:
        assert SensorRegistry().remove("sensor-99") is None

    def test_removing_the_selection_moves_to_a_neighbour(self) -> None:
        registry = registry_with("a", "b")
        first_id, second_id = (entry.sensor_id for entry in registry.entries)

        registry.remove(first_id)

        assert registry.selected_id == second_id

    def test_removing_the_last_sensor_clears_the_selection(self) -> None:
        registry = registry_with("a")

        registry.remove(registry.entries[0].sensor_id)

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
        first_id, _ = (entry.sensor_id for entry in registry.entries)

        assert registry.select(first_id) is True
        assert registry.selected_id == first_id

    def test_selecting_the_current_selection_reports_no_change(self) -> None:
        registry = registry_with("a")

        assert registry.select(registry.selected_id) is False

    def test_selecting_an_unknown_id_clears_the_selection(self) -> None:
        registry = registry_with("a")

        registry.select("sensor-99")

        assert registry.selected_id is None


class TestReplacingSensor:
    def test_the_new_sensor_replaces_the_old_one(self) -> None:
        registry = registry_with("gate")
        sensor_id = registry.entries[0].sensor_id
        edited = beam_sensor(name="gate", origin=(1.0, 0.0, 0.0), target=(2.0, 0.0, 0.0))

        updated = registry.replace_sensor(sensor_id, edited)

        assert updated is not None
        assert updated.sensor.origin == (1.0, 0.0, 0.0)

    def test_replacing_an_unknown_id_returns_none(self) -> None:
        assert SensorRegistry().replace_sensor("sensor-99", beam_sensor()) is None

    def test_renaming_into_a_taken_name_gets_a_counter(self) -> None:
        registry = registry_with("gate", "zone")
        gate_id = registry.entries[0].sensor_id

        updated = registry.replace_sensor(gate_id, beam_sensor(name="zone"))

        assert updated is not None
        assert updated.sensor.name == "zone (2)"

    def test_keeping_the_same_name_does_not_collide_with_itself(self) -> None:
        registry = registry_with("gate")
        sensor_id = registry.entries[0].sensor_id

        updated = registry.replace_sensor(sensor_id, beam_sensor(name="gate"))

        assert updated is not None
        assert updated.sensor.name == "gate"

    def test_the_id_survives_a_replace(self) -> None:
        registry = registry_with("gate")
        sensor_id = registry.entries[0].sensor_id

        registry.replace_sensor(sensor_id, beam_sensor(name="gate"))

        assert registry.entries[0].sensor_id == sensor_id


class TestSetActive:
    def test_setting_a_new_state_updates_the_entry(self) -> None:
        registry = registry_with("gate")
        sensor_id = registry.entries[0].sensor_id

        updated = registry.set_active(sensor_id, True)

        assert updated is not None
        assert updated.is_active is True

    def test_setting_the_same_state_is_a_no_op(self) -> None:
        registry = registry_with("gate")
        sensor_id = registry.entries[0].sensor_id

        assert registry.set_active(sensor_id, False) is None

    def test_setting_an_unknown_id_is_a_no_op(self) -> None:
        assert SensorRegistry().set_active("sensor-99", True) is None

    def test_new_sensors_start_inactive(self) -> None:
        registry = registry_with("gate")

        assert registry.entries[0].is_active is False

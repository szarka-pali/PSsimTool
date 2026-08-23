"""The set of placed sensors and which one is selected.

Pure state: no Qt, no Panda3D. Mirrors `ui/model_registry.py` — the tree widget
renders it, the renderer draws it, but neither owns it.

Entries are immutable. Changing a sensor replaces the entry rather than mutating
it, so a stale reference can never silently disagree with the registry.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Final

from pssim.domain.sensors import Sensor, SensorReading

#: Separator used when a sensor's name collides with an existing one.
DUPLICATE_SUFFIX: Final = " ({0})"


@dataclass(frozen=True, slots=True)
class SensorEntry:
    """One placed sensor.

    `sensor_id` is stable for the lifetime of the sensor and is what the
    renderer and the tree use to refer to it. `sensor.name` is only for display
    and may repeat across sessions, so never key anything by it.
    """

    sensor_id: str
    sensor: Sensor
    is_active: bool = False

    mounted_on: str | None = None
    """The model or joint carrying it, or `None` for one sitting in the scene.
    A sensor's point and direction are in that thing's frame, so this is what
    makes a sensor on a carriage ride the carriage."""

    reading: SensorReading = SensorReading(value=0.0)
    """What it last read. Display state the tree needs between evaluations,
    mirroring `is_active` — which is derived from this."""


class SensorRegistry:
    """Ordered sensors plus at most one selection.

    Order is insertion order — the dock lists sensors in the order they were
    added, which is what the user expects.
    """

    __slots__ = ("_entries", "_selected_id", "_next_number")

    def __init__(self) -> None:
        self._entries: dict[str, SensorEntry] = {}
        self._selected_id: str | None = None
        self._next_number = 0

    # -- reading ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[SensorEntry]:
        return iter(self._entries.values())

    def __contains__(self, sensor_id: object) -> bool:
        return sensor_id in self._entries

    @property
    def entries(self) -> tuple[SensorEntry, ...]:
        return tuple(self._entries.values())

    @property
    def is_empty(self) -> bool:
        return not self._entries

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def selected(self) -> SensorEntry | None:
        """The selected sensor, or `None` when nothing is selected."""
        if self._selected_id is None:
            return None
        return self._entries.get(self._selected_id)

    def get(self, sensor_id: str) -> SensorEntry | None:
        return self._entries.get(sensor_id)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.sensor.name for entry in self._entries.values())

    # -- writing ------------------------------------------------------------

    def add(self, sensor: Sensor, select: bool = True) -> SensorEntry:
        """Register a sensor and return its entry.

        A name collision gets a counter suffix rather than being rejected —
        the same treatment `ModelRegistry.add` gives a repeated file.
        """
        self._next_number += 1
        sensor_id = f"sensor-{self._next_number}"
        unique = replace(sensor, name=self._unique_name(sensor.name))
        entry = SensorEntry(sensor_id=sensor_id, sensor=unique)
        self._entries[sensor_id] = entry
        if select:
            self._selected_id = sensor_id
        return entry

    def remove(self, sensor_id: str) -> SensorEntry | None:
        """Remove a sensor. Returns the removed entry, or `None` if unknown.

        Removing the selected sensor moves the selection to a neighbour rather
        than clearing it, mirroring `ModelRegistry.remove`.
        """
        entry = self._entries.pop(sensor_id, None)
        if entry is None:
            return None
        if self._selected_id == sensor_id:
            self._selected_id = next(iter(self._entries), None)
        return entry

    def set_mount(self, sensor_id: str, mounted_on: str | None) -> bool:
        """Mount the sensor on a model or joint, or take it off. Returns `True`
        if it changed."""
        entry = self._entries.get(sensor_id)
        if entry is None or entry.mounted_on == mounted_on:
            return False
        self._entries[sensor_id] = replace(entry, mounted_on=mounted_on)
        return True

    def set_reading(self, sensor_id: str, reading: SensorReading) -> bool:
        """Record what the sensor read. Returns `True` if it changed."""
        entry = self._entries.get(sensor_id)
        if entry is None or entry.reading == reading:
            return False
        self._entries[sensor_id] = replace(entry, reading=reading)
        return True

    def clear(self) -> None:
        self._entries.clear()
        self._selected_id = None

    def select(self, sensor_id: str | None) -> bool:
        """Set the selection. Returns `True` if it changed.

        An unknown id clears the selection instead of raising — the tree can
        legitimately report "nothing selected" as an empty id.
        """
        resolved = sensor_id if sensor_id in self._entries else None
        if resolved == self._selected_id:
            return False
        self._selected_id = resolved
        return True

    def replace_sensor(self, sensor_id: str, sensor: Sensor) -> SensorEntry | None:
        """Store an edited sensor for an existing id. Returns the updated entry.

        A name change is uniquified the same way `add` uniquifies a new one —
        editing a sensor into a name another already has must not silently
        produce two identically-named rows.
        """
        entry = self._entries.get(sensor_id)
        if entry is None:
            return None
        unique = replace(sensor, name=self._unique_name(sensor.name, ignoring=sensor_id))
        updated = replace(entry, sensor=unique)
        self._entries[sensor_id] = updated
        return updated

    def set_active(self, sensor_id: str, is_active: bool) -> SensorEntry | None:
        """Store a sensor's current active state. Returns `None` — unchanged —
        when the id is unknown or the state already matches, mirroring
        `viz.embed.EmbeddedRenderer.set_sensor_active`'s own no-op-on-no-change,
        so a dock cell never redraws for nothing.
        """
        entry = self._entries.get(sensor_id)
        if entry is None or entry.is_active == is_active:
            return None
        updated = replace(entry, is_active=is_active)
        self._entries[sensor_id] = updated
        return updated

    # -- helpers ------------------------------------------------------------

    def _unique_name(self, base: str, ignoring: str | None = None) -> str:
        """`gate`, then `gate (2)`, `gate (3)`… for a repeated name."""
        taken = tuple(
            entry.sensor.name for entry in self._entries.values() if entry.sensor_id != ignoring
        )
        if base not in taken:
            return base
        counter = 2
        while f"{base}{DUPLICATE_SUFFIX.format(counter)}" in taken:
            counter += 1
        return f"{base}{DUPLICATE_SUFFIX.format(counter)}"

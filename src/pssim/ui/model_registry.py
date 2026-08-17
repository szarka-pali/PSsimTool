"""The set of loaded models and which one is selected.

Pure state: no Qt, no Panda3D. The tree widget renders it, the renderer draws it,
but neither owns it. That keeps the interesting logic — unique names, what happens
to the selection when a model is removed — testable without a window.

Entries are immutable. Changing a placement replaces the entry rather than
mutating it, so a stale reference can never silently disagree with the registry.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from pssim.domain.machine import Transform
from pssim.domain.placement import IDENTITY_PLACEMENT

#: Separator used when the same file is loaded more than once: `gantry (2)`.
DUPLICATE_SUFFIX: Final = " ({0})"


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """One loaded model.

    `model_id` is stable for the lifetime of the model and is what the renderer
    and the tree use to refer to it. `name` is only for display and may repeat
    across sessions, so never key anything by it.
    """

    model_id: str
    name: str
    path: Path
    placement: Transform = IDENTITY_PLACEMENT
    node_count: int = 0
    triangle_count: int = 0

    @property
    def is_placed(self) -> bool:
        """Whether the model has been moved or rotated away from the origin."""
        from pssim.domain.placement import is_identity

        return not is_identity(self.placement)


class ModelRegistry:
    """Ordered models plus at most one selection.

    Order is insertion order — the tree shows models in the order they were
    opened, which is what the user expects.
    """

    __slots__ = ("_entries", "_selected_id", "_next_number")

    def __init__(self) -> None:
        self._entries: dict[str, ModelEntry] = {}
        self._selected_id: str | None = None
        self._next_number = 0

    # -- reading ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[ModelEntry]:
        return iter(self._entries.values())

    def __contains__(self, model_id: object) -> bool:
        return model_id in self._entries

    @property
    def entries(self) -> tuple[ModelEntry, ...]:
        return tuple(self._entries.values())

    @property
    def is_empty(self) -> bool:
        return not self._entries

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def selected(self) -> ModelEntry | None:
        """The selected model, or `None` when nothing is selected.

        Actions that need a target check this — with no selection they must be
        disabled rather than guessing which model the user meant.
        """
        if self._selected_id is None:
            return None
        return self._entries.get(self._selected_id)

    def get(self, model_id: str) -> ModelEntry | None:
        return self._entries.get(model_id)

    @property
    def selected_name(self) -> str | None:
        """Display name of the selection, or `None`.

        What a project file records: ids are generated per session and would mean
        nothing after a reload, whereas the name is stable across saves.
        """
        entry = self.selected
        return entry.name if entry is not None else None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self._entries.values())

    # -- writing ------------------------------------------------------------

    def add(
        self,
        path: Path,
        node_count: int = 0,
        triangle_count: int = 0,
        select: bool = True,
    ) -> ModelEntry:
        """Register a model loaded from `path` and return its entry.

        The same file may be loaded repeatedly — a machine can legitimately
        contain ten of the same part — so the id is generated and the display
        name gets a counter suffix instead of the call being rejected.
        """
        self._next_number += 1
        model_id = f"model-{self._next_number}"
        entry = ModelEntry(
            model_id=model_id,
            name=self._unique_name(path.stem),
            path=path,
            node_count=node_count,
            triangle_count=triangle_count,
        )
        self._entries[model_id] = entry
        if select:
            self._selected_id = model_id
        return entry

    def remove(self, model_id: str) -> ModelEntry | None:
        """Remove a model. Returns the removed entry, or `None` if unknown.

        Removing the selected model moves the selection to a neighbour rather
        than clearing it — after deleting one of several models the user almost
        always wants to carry on with another.
        """
        entry = self._entries.pop(model_id, None)
        if entry is None:
            return None
        if self._selected_id == model_id:
            self._selected_id = next(iter(self._entries), None)
        return entry

    def clear(self) -> None:
        self._entries.clear()
        self._selected_id = None

    def select(self, model_id: str | None) -> bool:
        """Set the selection. Returns `True` if it changed.

        An unknown id clears the selection instead of raising: the tree can
        legitimately report "nothing selected" as an empty id.
        """
        resolved = model_id if model_id in self._entries else None
        if resolved == self._selected_id:
            return False
        self._selected_id = resolved
        return True

    def set_placement(self, model_id: str, placement: Transform) -> ModelEntry | None:
        """Store a new placement for one model. Returns the updated entry."""
        entry = self._entries.get(model_id)
        if entry is None:
            return None
        updated = replace(entry, placement=placement)
        self._entries[model_id] = updated
        return updated

    # -- helpers ------------------------------------------------------------

    def _unique_name(self, base: str) -> str:
        """`gantry`, then `gantry (2)`, `gantry (3)`… for repeated files."""
        taken = self.names
        if base not in taken:
            return base
        counter = 2
        while f"{base}{DUPLICATE_SUFFIX.format(counter)}" in taken:
            counter += 1
        return f"{base}{DUPLICATE_SUFFIX.format(counter)}"

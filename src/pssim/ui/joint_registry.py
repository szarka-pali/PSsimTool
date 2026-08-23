"""The set of placed joints (axes/trajectories) and which one is selected.

Pure state: no Qt, no Panda3D. Mirrors `ui/sensor_registry.py` — the tree renders
it, the renderer draws it, but neither owns it.

The one thing neither sensors nor models need: **joints form a hierarchy of their
own.** A joint may be carried by another joint, so an entry carries its parent's
id and `children_of()`/`ancestors_of()` exist for the tree's recursion and for
the properties panel's driving chain. Models are the leaves of that hierarchy,
bound to a joint by `ModelRegistry.bind`, never owning one — which is why the
only cycle possible is joint→joint, and why `would_cycle` here is simpler than
the model-walking version it replaced.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Final

from pssim.domain.machine import Rgba
from pssim.domain.model_joints import ModelJoint, effective_limits

#: Separator used when a joint's name collides with an existing one.
DUPLICATE_SUFFIX: Final = " ({0})"


@dataclass(frozen=True, slots=True)
class JointEntry:
    """One placed joint.

    `joint_id` is stable for the lifetime of the joint and is what the renderer
    and the tree use to refer to it.

    A joint belongs to **no model**: it sits in the scene, optionally carried by
    another joint, and models are bound *to* it (see `ModelRegistry.bind`). That
    is what lets a rail carry a carriage that carries a rotation axis — the
    hierarchy is made of joints, and models are its leaves.
    """

    joint_id: str
    joint: ModelJoint

    value: float = 0.0
    """The joint's current live value (radians for `AXIS`, metres for
    `TRAJECTORY`) — mirrors `SensorEntry.is_active`: viz-driven display state
    the dock needs to remember between opens, not just the joint's own
    definition."""

    parent_joint_id: str | None = None
    """The joint carrying this one, or `None` when it sits directly in the scene."""

    show_axes: bool = True
    """Whether the cross on its initial coordinate system is drawn while it is
    selected. The joint's own marker — the arrow or the path line — is a
    different thing and is not affected."""

    show_name: bool = True
    """Whether its name is drawn beside it in the scene. Combined with the
    scene-wide switch: the label appears when both are on, so hiding all the
    names does not forget which individual ones were already off."""

    color: Rgba | None = None
    """An override for its marker and label, or `None` for the default marker
    colour. Same reasoning as a model's."""


def _rest_value(joint: ModelJoint) -> float:
    """The value a joint starts at — nearest to zero within its own limits,
    mirroring `domain.model_joints.rest_model_joint_pose`'s own choice."""
    low, high = effective_limits(joint)
    return min(max(0.0, low), high)


class JointRegistry:
    """Ordered joints plus at most one selection.

    Order is insertion order — the tree lists a model's joints in the order they
    were added, mirroring `ModelRegistry`/`SensorRegistry`.
    """

    __slots__ = ("_entries", "_selected_id", "_next_number")

    def __init__(self) -> None:
        self._entries: dict[str, JointEntry] = {}
        self._selected_id: str | None = None
        self._next_number = 0

    # -- reading ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[JointEntry]:
        return iter(self._entries.values())

    def __contains__(self, joint_id: object) -> bool:
        return joint_id in self._entries

    @property
    def entries(self) -> tuple[JointEntry, ...]:
        return tuple(self._entries.values())

    @property
    def is_empty(self) -> bool:
        return not self._entries

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def selected(self) -> JointEntry | None:
        if self._selected_id is None:
            return None
        return self._entries.get(self._selected_id)

    def get(self, joint_id: str) -> JointEntry | None:
        return self._entries.get(joint_id)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.joint.name for entry in self._entries.values())

    def children_of(self, parent_joint_id: str | None) -> tuple[JointEntry, ...]:
        """Every joint carried directly by `parent_joint_id`, in insertion
        order — or the top-level ones when it is `None`. What the tree recurses
        over."""
        return tuple(
            entry for entry in self._entries.values() if entry.parent_joint_id == parent_joint_id
        )

    def ancestors_of(self, joint_id: str) -> tuple[JointEntry, ...]:
        """The chain carrying `joint_id`, nearest first, `joint_id` included.

        This is the whole set of joints whose value moves that joint — so a
        model bound to it can show every slider that actually drives it, not
        just the last one.

        Stops on a repeat rather than trusting the graph to be acyclic: this is
        read on every properties refresh, and spinning forever there would take
        the window with it.
        """
        chain: list[JointEntry] = []
        seen: set[str] = set()
        current: str | None = joint_id
        while current is not None and current not in seen:
            entry = self._entries.get(current)
            if entry is None:
                break
            seen.add(current)
            chain.append(entry)
            current = entry.parent_joint_id
        return tuple(chain)

    # -- writing ------------------------------------------------------------

    def add(
        self,
        joint: ModelJoint,
        parent_joint_id: str | None = None,
        select: bool = True,
    ) -> JointEntry:
        """Register a joint and return its entry.

        `parent_joint_id` is the joint carrying it, or `None` for one sitting
        directly in the scene. A name collision gets a counter suffix rather
        than being rejected — the same treatment
        `ModelRegistry.add`/`SensorRegistry.add` give a repeat.
        """
        self._next_number += 1
        joint_id = f"joint-{self._next_number}"
        unique = replace(joint, name=self._unique_name(joint.name))
        entry = JointEntry(
            joint_id=joint_id,
            joint=unique,
            value=_rest_value(unique),
            parent_joint_id=parent_joint_id,
        )
        self._entries[joint_id] = entry
        if select:
            self._selected_id = joint_id
        return entry

    def remove(self, joint_id: str) -> JointEntry | None:
        """Remove a joint. Returns the removed entry, or `None` if unknown.

        Joints carried by it are **re-hung on its own parent** rather than
        removed with it, matching what the scene graph does
        (`viz.embed.EmbeddedRenderer.remove_joint`): deleting a rail should not
        silently delete the head that sat on it.

        Removing the selected joint moves the selection to a neighbour rather
        than clearing it, mirroring `SensorRegistry.remove`.
        """
        entry = self._entries.pop(joint_id, None)
        if entry is None:
            return None

        for child_id, child in list(self._entries.items()):
            if child.parent_joint_id == joint_id:
                self._entries[child_id] = replace(child, parent_joint_id=entry.parent_joint_id)

        if self._selected_id == joint_id:
            self._selected_id = next(iter(self._entries), None)
        return entry

    def set_parent(self, joint_id: str, parent_joint_id: str | None) -> bool:
        """Re-hang a joint under another one, or in the scene with `None`.
        Returns `True` if it changed.

        Does not check for cycles — see the `would_cycle` free function below;
        the caller checks before calling, the same split `ModelRegistry.bind`
        uses.
        """
        entry = self._entries.get(joint_id)
        if entry is None or entry.parent_joint_id == parent_joint_id:
            return False
        self._entries[joint_id] = replace(entry, parent_joint_id=parent_joint_id)
        return True

    def set_name_visible(self, joint_id: str, show_name: bool) -> bool:
        """Show or hide this joint's name in the scene. Returns `True` if it
        changed."""
        entry = self._entries.get(joint_id)
        if entry is None or entry.show_name == show_name:
            return False
        self._entries[joint_id] = replace(entry, show_name=show_name)
        return True

    def set_color(self, joint_id: str, color: Rgba | None) -> bool:
        """Override the joint's colour, or clear the override with `None`.
        Returns `True` if it changed."""
        entry = self._entries.get(joint_id)
        if entry is None or entry.color == color:
            return False
        self._entries[joint_id] = replace(entry, color=color)
        return True

    def set_axes_visible(self, joint_id: str, show_axes: bool) -> bool:
        """Show or hide the cross on this joint's initial coordinate system.
        Returns `True` if it changed. Mirrors `ModelRegistry.set_axes_visible`."""
        entry = self._entries.get(joint_id)
        if entry is None or entry.show_axes == show_axes:
            return False
        self._entries[joint_id] = replace(entry, show_axes=show_axes)
        return True

    def clear(self) -> None:
        self._entries.clear()
        self._selected_id = None

    def select(self, joint_id: str | None) -> bool:
        """Set the selection. Returns `True` if it changed.

        An unknown id clears the selection instead of raising — the tree can
        legitimately report "nothing selected" as an empty id.
        """
        resolved = joint_id if joint_id in self._entries else None
        if resolved == self._selected_id:
            return False
        self._selected_id = resolved
        return True

    def replace_joint(self, joint_id: str, joint: ModelJoint) -> JointEntry | None:
        """Store an edited joint for an existing id. Returns the updated entry.

        The joint's place in the hierarchy never changes here — only its own
        geometry does (see `set_parent` for the other). A name change is
        uniquified the same way `add` uniquifies a new one. The stored value is
        reclamped against the edited joint's own limits, mirroring
        `viz.embed.EmbeddedRenderer.update_joint`'s own reclamping — edited
        limits may no longer contain the old value.
        """
        entry = self._entries.get(joint_id)
        if entry is None:
            return None
        unique = replace(joint, name=self._unique_name(joint.name, ignoring=joint_id))
        low, high = effective_limits(unique)
        clamped_value = min(max(entry.value, low), high)
        updated = replace(entry, joint=unique, value=clamped_value)
        self._entries[joint_id] = updated
        return updated

    def set_value(self, joint_id: str, value: float) -> JointEntry | None:
        """Store a joint's current live value. Returns the updated entry, or
        `None` when the id is unknown or the value has not changed — mirrors
        `SensorRegistry.set_active`'s no-op-on-no-change contract, so a dock
        row never redraws for nothing.
        """
        entry = self._entries.get(joint_id)
        if entry is None or entry.value == value:
            return None
        updated = replace(entry, value=value)
        self._entries[joint_id] = updated
        return updated

    # -- helpers ------------------------------------------------------------

    def _unique_name(self, base: str, ignoring: str | None = None) -> str:
        """`axis-1`, then `axis-1 (2)`, `axis-1 (3)`… for a repeated name."""
        taken = tuple(
            entry.joint.name for entry in self._entries.values() if entry.joint_id != ignoring
        )
        if base not in taken:
            return base
        counter = 2
        while f"{base}{DUPLICATE_SUFFIX.format(counter)}" in taken:
            counter += 1
        return f"{base}{DUPLICATE_SUFFIX.format(counter)}"


def would_cycle(joints: JointRegistry, joint_id: str, parent_joint_id: str | None) -> bool:
    """Whether hanging `joint_id` under `parent_joint_id` would close a loop.

    Walks up from the proposed parent looking for `joint_id` itself. Cheaper
    and narrower than the model-based check this replaces: models cannot carry
    joints any more, so they are leaves and the only possible cycle is
    joint→joint.

    The visited set is not decoration — it stops this from spinning if the
    graph is *already* broken, which the version this replaces could not do.
    """
    if parent_joint_id is None:
        return False

    seen: set[str] = set()
    current: str | None = parent_joint_id
    while current is not None:
        if current == joint_id or current in seen:
            return True
        seen.add(current)
        entry = joints.get(current)
        current = entry.parent_joint_id if entry is not None else None
    return False


def descendants_of(joints: JointRegistry, joint_id: str) -> frozenset[str]:
    """Every joint carried by `joint_id`, at any depth, excluding itself.

    Used to filter what a dialog may offer as a new parent: offering one of a
    joint's own descendants is exactly the cycle `would_cycle` refuses, so it
    should never appear in the list rather than be offered and then rejected.
    """
    found: set[str] = set()
    frontier = [joint_id]
    while frontier:
        current = frontier.pop()
        for child in joints.children_of(current):
            if child.joint_id in found:
                continue
            found.add(child.joint_id)
            frontier.append(child.joint_id)
    return frozenset(found)

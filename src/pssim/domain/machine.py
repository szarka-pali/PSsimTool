"""The runtime machine model — a kinematic tree with no knowledge of CAD or the PLC.

This is what `viz/` and `domain/kinematics.py` are given. Bindings to OPC UA nodes
are **not** here — those are held by `config.binding.JointBinding`, so that the
domain knows nothing about the PLC.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pssim.domain.errors import ConfigError

#: A triple (x, y, z) in metres, or (roll, pitch, yaw) in radians.
Vec3 = tuple[float, float, float]

_AXIS_NORM_TOLERANCE: Final = 1e-6


class JointType(StrEnum):
    """The kind of degree of freedom."""

    PRISMATIC = "prismatic"
    """Translation along an axis. The signal value is a displacement in metres."""

    REVOLUTE = "revolute"
    """Rotation about an axis. The signal value is an angle in radians."""

    FIXED = "fixed"
    """No movement. Serves only as a fixed offset in the hierarchy; the value is ignored."""


@dataclass(frozen=True, slots=True)
class Transform:
    """A fixed transformation: translation in metres + rotation as roll/pitch/yaw in radians."""

    xyz: Vec3 = (0.0, 0.0, 0.0)
    rpy: Vec3 = (0.0, 0.0, 0.0)


IDENTITY: Final = Transform()


@dataclass(frozen=True, slots=True)
class Joint:
    """One degree of freedom between two nodes of the CAD assembly.

    `parent` and `child` are stable node paths (`base/portal/Carriage[2]`), not part
    names. `axis` is a unit vector in the parent's coordinate system. `limits` are in
    metres (prismatic) or radians (revolute); `None` means unrestricted movement.
    """

    name: str
    parent: str
    child: str
    type: JointType
    axis: Vec3 = (0.0, 0.0, 1.0)
    limits: tuple[float, float] | None = None
    origin: Transform = IDENTITY

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("a joint must have a non-empty name")

        if self.type is not JointType.FIXED:
            norm_sq = sum(component * component for component in self.axis)
            if abs(norm_sq - 1.0) > _AXIS_NORM_TOLERANCE:
                # Deliberately not normalised: the length of the vector would
                # otherwise scale the movement unnoticed, and that is very hard to find.
                raise ConfigError(
                    f"joint {self.name!r}: the axis {self.axis} is not a unit vector "
                    f"(length {norm_sq**0.5:.6f}). Normalise it in machines/*.yaml."
                )

        if self.limits is not None:
            low, high = self.limits
            if low > high:
                raise ConfigError(
                    f"joint {self.name!r}: the lower limit {low} is greater than the upper {high}"
                )


@dataclass(frozen=True, slots=True)
class Machine:
    """The whole machine: a kinematic tree of joints over the nodes of a CAD assembly.

    Validation of the tree (cycles, multiple parents, duplicate names) happens at
    construction — at run time the model is assumed to be valid.
    """

    name: str
    joints: tuple[Joint, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("a machine must have a non-empty name")
        self._check_unique_names()
        self._check_single_parent()
        self._check_acyclic()

    def _check_unique_names(self) -> None:
        seen: set[str] = set()
        for joint in self.joints:
            if joint.name in seen:
                raise ConfigError(f"duplicate joint name {joint.name!r}")
            seen.add(joint.name)

    def _check_single_parent(self) -> None:
        """Every node may be the `child` of at most one joint — otherwise it is not a tree."""
        owner: dict[str, str] = {}
        for joint in self.joints:
            if joint.child in owner:
                raise ConfigError(
                    f"the node {joint.child!r} is the child of two joints: "
                    f"{owner[joint.child]!r} and {joint.name!r}. The kinematics must be a tree."
                )
            owner[joint.child] = joint.name

    def _check_acyclic(self) -> None:
        parent_of: dict[str, str] = {j.child: j.parent for j in self.joints}
        for start in parent_of:
            visited: set[str] = {start}
            node = parent_of[start]
            while node in parent_of:
                if node in visited:
                    raise ConfigError(
                        f"a cycle in the kinematic chain at the node {node!r}. "
                        f"Check parent/child in machines/*.yaml."
                    )
                visited.add(node)
                node = parent_of[node]

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(joint.name for joint in self.joints)

    @property
    def moving_joints(self) -> tuple[Joint, ...]:
        """The joints that actually move — that is, the ones that need a signal."""
        return tuple(j for j in self.joints if j.type is not JointType.FIXED)

    def joint(self, name: str) -> Joint:
        for candidate in self.joints:
            if candidate.name == name:
                return candidate
        known = ", ".join(self.joint_names) or "(none)"
        raise ConfigError(
            f"the joint {name!r} does not exist in the machine {self.name!r}; available: {known}"
        )

    def chain_to_root(self, node: str) -> tuple[Joint, ...]:
        """The joints from `node` up to the root, ordered from the one nearest the node.

        Used for diagnosis ("why is this part somewhere else than I expect") — the
        scene itself composes the transformations through the NodePath hierarchy,
        not through this function.
        """
        by_child = {joint.child: joint for joint in self.joints}
        chain: list[Joint] = []
        current = node
        while current in by_child:
            joint = by_child[current]
            chain.append(joint)
            current = joint.parent
        return tuple(chain)

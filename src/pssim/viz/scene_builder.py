"""Building the scene from a machine definition and the imported geometry.

The key decision of this layer: splitting the geometry into **static** and **moving**. The
static part can be merged (`flattenStrong`) into a single Geom; the moving part has to stay
in separate `NodePath`s. A real-world assembly has hundreds to thousands of parts and
without this split the number of draw calls is unaffordable. See docs/architecture.md, the
Performance section.
"""

from __future__ import annotations

from dataclasses import dataclass

from pssim.cad.model import CadAssembly
from pssim.domain.errors import ConfigError
from pssim.domain.machine import Machine


@dataclass(frozen=True, slots=True)
class ScenePlan:
    """The scene plan: which nodes are moving and which can be merged.

    A pure data structure with no Panda3D — which is why the whole plan can be tested in
    `tests/unit/` without opening a window.
    """

    moving_nodes: tuple[str, ...]
    """Nodes driven by a joint. Each gets its own NodePath."""

    static_nodes: tuple[str, ...]
    """Nodes with no movement and no moving ancestor. These can be flattened."""

    joint_to_node: dict[str, str]
    """The mapping from a joint name to the path of the node that should move (`joint.child`)."""

    @property
    def flattenable_count(self) -> int:
        return len(self.static_nodes)


def plan_scene(machine: Machine, assembly: CadAssembly) -> ScenePlan:
    """Decide which nodes are moving.

    A node is moving when it is the `child` of some non-fixed joint, **and so are all its
    descendants** — they move together with it, so they must not be flattened into the
    static geometry.

    Raises `ConfigError` if a node from `machines/*.yaml` does not exist in the assembly.
    The error message contains the similar available paths — without them it is useless,
    because an assembly has a thousand nodes.
    """
    known = assembly.by_path
    joint_to_node: dict[str, str] = {}

    for joint in machine.joints:
        for role, path in (("parent", joint.parent), ("child", joint.child)):
            if path not in known:
                similar = assembly.similar_paths(path)
                hint = f" Podobné cesty: {', '.join(similar)}" if similar else ""
                raise ConfigError(
                    f"kĺb {joint.name!r}: uzol {path!r} ({role}) v assembly neexistuje.{hint}"
                )
        joint_to_node[joint.name] = joint.child

    moving_roots = {joint.child for joint in machine.moving_joints}
    moving = {
        path
        for path in known
        if any(path == root or path.startswith(f"{root}/") for root in moving_roots)
    }
    static = set(known) - moving

    return ScenePlan(
        moving_nodes=tuple(sorted(moving)),
        static_nodes=tuple(sorted(static)),
        joint_to_node=joint_to_node,
    )

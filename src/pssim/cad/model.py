"""The data model of imported geometry.

Depends on neither OCP nor Panda3D — it is only a description of what came out of an
import, and it is fully serialisable into `meta.json`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pssim.domain.machine import Transform, Vec3

#: Grey for parts that have no colour in the STEP file. A part without a colour is common.
DEFAULT_COLOR: tuple[float, float, float, float] = (0.6, 0.6, 0.62, 1.0)


@dataclass(frozen=True, slots=True)
class CadNode:
    """A node of the CAD assembly tree.

    `path` is the **stable path** (`base/portal/Carriage[2]`) — it is exactly what
    `machines/*.yaml` refers to. The index `[n]` is added only when the node has
    siblings of the same name; without it the path would be ambiguous.

    `mesh` is the file name in the cache directory, or `None` for purely organisational
    nodes (an assembly with no geometry of its own).
    """

    path: str
    transform: Transform = Transform()
    mesh: str | None = None
    color: tuple[float, float, float, float] = DEFAULT_COLOR
    children: tuple[str, ...] = ()
    triangle_count: int = 0

    @property
    def name(self) -> str:
        """The last segment of the path, including any index."""
        return self.path.rsplit("/", 1)[-1]

    @property
    def depth(self) -> int:
        return self.path.count("/")

    @property
    def has_geometry(self) -> bool:
        return self.mesh is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mesh": self.mesh,
            "transform": {"xyz": list(self.transform.xyz), "rpy": list(self.transform.rpy)},
            "color": list(self.color),
            "children": list(self.children),
            "triangle_count": self.triangle_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CadNode:
        transform = data.get("transform") or {}
        return cls(
            path=str(data["path"]),
            transform=Transform(
                xyz=_vec3(transform.get("xyz")),
                rpy=_vec3(transform.get("rpy")),
            ),
            mesh=data.get("mesh"),
            color=_rgba(data.get("color")),
            children=tuple(str(child) for child in data.get("children", ())),
            triangle_count=int(data.get("triangle_count", 0)),
        )


@dataclass(frozen=True, slots=True)
class CadAssembly:
    """The whole imported assembly as a flat list of nodes plus the roots."""

    nodes: tuple[CadNode, ...]
    roots: tuple[str, ...] = field(default=())

    @property
    def by_path(self) -> dict[str, CadNode]:
        return {node.path: node for node in self.nodes}

    @property
    def nodes_parents_first(self) -> tuple[CadNode, ...]:
        """The nodes ordered so that a parent always comes before its children.

        `nodes` on its own does **not** have this property — it comes from a recursive
        walk that appends a node after its children. Anyone building a hierarchy
        (`viz/`, for instance) must use this order, or the parent does not exist yet at
        the moment a child is attached and the tree comes out flat.
        """
        return tuple(sorted(self.nodes, key=lambda node: (node.depth, node.path)))

    @property
    def triangle_count(self) -> int:
        return sum(node.triangle_count for node in self.nodes)

    def node(self, path: str) -> CadNode | None:
        for node in self.nodes:
            if node.path == path:
                return node
        return None

    def similar_paths(self, path: str, limit: int = 5) -> tuple[str, ...]:
        """Paths similar to the given one — for the error message when a node from the YAML does not exist.

        Without this, "node not found" is a useless error: an assembly has a thousand
        nodes and the user has no way of finding out what theirs is really called.
        """
        needle = path.rsplit("/", 1)[-1].lower()
        matches = [node.path for node in self.nodes if needle in node.path.lower()]
        if not matches:
            matches = [node.path for node in self.nodes if node.name.lower().startswith(needle[:3])]
        return tuple(sorted(matches)[:limit])


def _vec3(value: Any) -> Vec3:
    if not value:
        return (0.0, 0.0, 0.0)
    x, y, z = (float(component) for component in value)
    return (x, y, z)


def _rgba(value: Any) -> tuple[float, float, float, float]:
    if not value:
        return DEFAULT_COLOR
    r, g, b, a = (float(component) for component in value)
    return (r, g, b, a)

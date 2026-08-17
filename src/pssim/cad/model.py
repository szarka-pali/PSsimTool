"""Dátový model importovanej geometrie.

Nezávisí od OCP ani od Panda3D — je to len popis toho, čo z importu vyšlo,
a je plne serializovateľný do `meta.json`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pssim.domain.machine import Transform, Vec3

#: Šedá pre diely, ktoré v STEP súbore nemajú farbu. Diel bez farby je bežný.
DEFAULT_COLOR: tuple[float, float, float, float] = (0.6, 0.6, 0.62, 1.0)


@dataclass(frozen=True, slots=True)
class CadNode:
    """Uzol CAD assembly tree.

    `path` je **stabilná cesta** (`base/portal/Carriage[2]`) — práve na ňu sa
    odkazuje `machines/*.yaml`. Index `[n]` sa pridáva len keď má uzol
    rovnomenných siblingov; bez toho by bola cesta nejednoznačná.

    `mesh` je názov súboru v adresári cache, alebo `None` pre čisto organizačné
    uzly (assembly bez vlastnej geometrie).
    """

    path: str
    transform: Transform = Transform()
    mesh: str | None = None
    color: tuple[float, float, float, float] = DEFAULT_COLOR
    children: tuple[str, ...] = ()
    triangle_count: int = 0

    @property
    def name(self) -> str:
        """Posledný segment cesty, vrátane prípadného indexu."""
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
    """Celý importovaný assembly ako plochý zoznam uzlov + korene."""

    nodes: tuple[CadNode, ...]
    roots: tuple[str, ...] = field(default=())

    @property
    def by_path(self) -> dict[str, CadNode]:
        return {node.path: node for node in self.nodes}

    @property
    def nodes_parents_first(self) -> tuple[CadNode, ...]:
        """Uzly zoradené tak, že rodič je vždy pred svojimi potomkami.

        `nodes` samo o sebe túto vlastnosť **nemá** — vzniká rekurzívnym
        prechodom, ktorý zapisuje uzol až po jeho deťoch. Kto stavia hierarchiu
        (napr. `viz/`), musí použiť toto poradie, inak rodič v čase pripájania
        potomka ešte neexistuje a strom vyjde plochý.
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
        """Cesty podobné zadanej — pre chybovú správu, keď uzol z YAML neexistuje.

        Bez tohto je „uzol sa nenašiel" nepoužiteľná chyba: assembly má tisíc uzlov
        a používateľ nemá ako zistiť, ako sa ten jeho naozaj menuje.
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

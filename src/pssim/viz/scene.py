"""Stavba hierarchie `NodePath` z cache.

Vyčlenené z `viz/app.py`, lebo scénu treba postaviť aj **bez definície stroja** —
pri obyčajnom otvorení STEP súboru v UI žiadne `machines/*.yaml` neexistuje
a žiadne kĺby nie sú.

Nepotrebuje okno ani `ShowBase`: vracia odpojený koreň, ktorý si volajúci
pripojí, kam chce. Vďaka tomu sa dá testovať headless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pssim.cad.model import CadAssembly
from pssim.domain.machine import Transform, Vec3
from pssim.observability import get_logger
from pssim.viz.mesh_loader import load_geom_node
from pssim.viz.transforms import Quaternion, rpy_to_quat

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BuiltScene:
    """Postavená scéna a to, čo o nej treba vedieť ďalej."""

    root: Any
    """Koreňový `NodePath`. Nie je pripojený na `render` — spraví to volajúci."""

    node_paths: dict[str, Any] = field(default_factory=dict)
    """Mapovanie stabilná cesta uzla → `NodePath`."""

    base_transforms: dict[str, tuple[Vec3, Quaternion]] = field(default_factory=dict)
    """Poloha uzla podľa CAD. Pohyb kĺbu sa k nej pripočítava, viď R2c."""

    missing_meshes: int = 0
    """Počet uzlov, ktorých geometria v cache chýbala."""

    @property
    def is_empty(self) -> bool:
        return not self.node_paths


def build_scene(
    assembly: CadAssembly,
    cache_dir: Path,
    name: str = "model",
    flatten: frozenset[str] = frozenset(),
) -> BuiltScene:
    """Poskladá scénu podľa assembly a načíta geometriu z cache.

    `flatten` sú cesty uzlov, ktoré sa smú spojiť do jedného `Geom` — typicky
    statické diely. Pohyblivé uzly sa flattenovať nesmú, prišli by o vlastnú
    transformáciu.
    """
    from panda3d.core import NodePath

    root = NodePath(name)
    node_paths: dict[str, Any] = {}
    base_transforms: dict[str, tuple[Vec3, Quaternion]] = {}
    missing_meshes = 0

    # Poradie rodič-pred-potomkom je POVINNÉ: rodiča hľadáme medzi už
    # vytvorenými uzlami a pri opačnom poradí by strom vyšiel plochý.
    for node in assembly.nodes_parents_first:
        parent = _parent_of(node.path, node_paths, root)
        node_path = parent.attachNewNode(node.name)
        _apply_transform(node_path, node.transform)
        node_path.setColor(*node.color)
        base_transforms[node.path] = (node.transform.xyz, rpy_to_quat(node.transform.rpy))

        if node.mesh is not None:
            geom_node = load_geom_node(cache_dir / node.mesh, node.path)
            if geom_node is None:
                missing_meshes += 1
            else:
                node_path.attachNewNode(geom_node)

        node_paths[node.path] = node_path

    if missing_meshes:
        logger.warning(
            "časť geometrie chýba — spusti `pssim import-step`",
            missing=missing_meshes,
            total=len(assembly.nodes),
        )

    for path in flatten:
        node_path = node_paths.get(path)
        if node_path is not None and not node_path.getChildren():
            node_path.flattenStrong()

    return BuiltScene(
        root=root,
        node_paths=node_paths,
        base_transforms=base_transforms,
        missing_meshes=missing_meshes,
    )


def _parent_of(path: str, node_paths: dict[str, Any], root: Any) -> Any:
    parent_path = path.rsplit("/", 1)[0] if "/" in path else None
    if parent_path is None:
        return root
    return node_paths.get(parent_path, root)


def _apply_transform(node_path: Any, transform: Transform) -> None:
    """Nastaví pevnú transformáciu uzla.

    Rotácia ide cez kvaternión, nie cez HPR: prevod rpy → HPR by znamenal hádať
    konvenciu poradia osí Panda3D, kým `rpy_to_quat` je overený proti rotačnej
    matici v `tests/unit/viz/test_transforms.py`.
    """
    from panda3d.core import LQuaternion

    node_path.setPos(*transform.xyz)
    node_path.setQuat(LQuaternion(*rpy_to_quat(transform.rpy)))

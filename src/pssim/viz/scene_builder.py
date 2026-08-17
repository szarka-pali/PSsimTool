"""Stavba scény z definície stroja a naimportovanej geometrie.

Kľúčové rozhodnutie tejto vrstvy: rozdelenie geometrie na **statickú** a **pohyblivú**.
Statickú možno spojiť (`flattenStrong`) do jedného Geomu, pohyblivá musí zostať
v samostatných `NodePath`. Assembly z praxe má stovky až tisíce dielov a bez tohto
rozdelenia je počet draw callov neúnosný. Viď docs/architecture.md, sekcia Výkon.
"""

from __future__ import annotations

from dataclasses import dataclass

from pssim.cad.model import CadAssembly
from pssim.domain.errors import ConfigError
from pssim.domain.machine import Machine


@dataclass(frozen=True, slots=True)
class ScenePlan:
    """Plán scény: ktoré uzly sú pohyblivé a ktoré sa dajú spojiť.

    Čistá dátová štruktúra bez Panda3D — preto sa dá celý plán otestovať
    v `tests/unit/` bez otvorenia okna.
    """

    moving_nodes: tuple[str, ...]
    """Uzly riadené kĺbom. Každý dostane vlastný NodePath."""

    static_nodes: tuple[str, ...]
    """Uzly bez pohybu a bez pohyblivého predka. Dajú sa flattenovať."""

    joint_to_node: dict[str, str]
    """Mapovanie názov kĺbu → cesta uzla, ktorý sa má hýbať (`joint.child`)."""

    @property
    def flattenable_count(self) -> int:
        return len(self.static_nodes)


def plan_scene(machine: Machine, assembly: CadAssembly) -> ScenePlan:
    """Rozhodne, ktoré uzly sú pohyblivé.

    Pohyblivý je uzol, ktorý je `child` niektorého nefixovaného kĺbu, **a všetci
    jeho potomkovia** — tí sa hýbu spolu s ním, takže sa nesmú flattenovať
    do statickej geometrie.

    Vyhadzuje `ConfigError`, ak uzol z `machines/*.yaml` v assembly neexistuje.
    Chybová správa obsahuje podobné dostupné cesty — bez toho je nepoužiteľná,
    lebo assembly má tisíc uzlov.
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

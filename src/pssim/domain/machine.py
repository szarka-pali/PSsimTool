"""Runtime model stroja — kinematický strom bez akejkoľvek znalosti CAD či PLC.

Toto je to, čo dostane `viz/` a `domain/kinematics.py`. Väzby na OPC UA nody
tu **nie sú** — tie drží `config.binding.JointBinding`, aby doména o PLC nevedela.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pssim.domain.errors import ConfigError

#: Trojica (x, y, z) v metroch, alebo (roll, pitch, yaw) v radiánoch.
Vec3 = tuple[float, float, float]

_AXIS_NORM_TOLERANCE: Final = 1e-6


class JointType(StrEnum):
    """Typ stupňa voľnosti."""

    PRISMATIC = "prismatic"
    """Translácia po osi. Hodnota signálu je posun v metroch."""

    REVOLUTE = "revolute"
    """Rotácia okolo osi. Hodnota signálu je uhol v radiánoch."""

    FIXED = "fixed"
    """Bez pohybu. Slúži len na pevný offset v hierarchii; hodnota sa ignoruje."""


@dataclass(frozen=True, slots=True)
class Transform:
    """Pevná transformácia: posun v metroch + rotácia ako roll/pitch/yaw v radiánoch."""

    xyz: Vec3 = (0.0, 0.0, 0.0)
    rpy: Vec3 = (0.0, 0.0, 0.0)


IDENTITY: Final = Transform()


@dataclass(frozen=True, slots=True)
class Joint:
    """Jeden stupeň voľnosti medzi dvoma uzlami CAD assembly.

    `parent` a `child` sú stabilné cesty uzlov (`base/portal/Carriage[2]`),
    nie názvy dielov. `axis` je jednotkový vektor v súradnicovom systéme rodiča.
    `limits` sú v metroch (prismatic) alebo radiánoch (revolute); `None` znamená
    neomedzený pohyb.
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
            raise ConfigError("kĺb musí mať neprázdny názov")

        if self.type is not JointType.FIXED:
            norm_sq = sum(component * component for component in self.axis)
            if abs(norm_sq - 1.0) > _AXIS_NORM_TOLERANCE:
                # Zámerne nenormalizujeme: dĺžka vektora by inak nenápadne
                # škálovala pohyb a hľadalo by sa to veľmi ťažko.
                raise ConfigError(
                    f"kĺb {self.name!r}: os {self.axis} nie je jednotkový vektor "
                    f"(dĺžka {norm_sq**0.5:.6f}). Znormalizuj ju v machines/*.yaml."
                )

        if self.limits is not None:
            low, high = self.limits
            if low > high:
                raise ConfigError(f"kĺb {self.name!r}: dolný limit {low} je väčší ako horný {high}")

    @property
    def has_limits(self) -> bool:
        return self.limits is not None


@dataclass(frozen=True, slots=True)
class Machine:
    """Celý stroj: kinematický strom kĺbov nad uzlami CAD assembly.

    Validácia stromu (cykly, viacnásobný rodič, duplicitné názvy) sa deje pri
    konštrukcii — za behu sa už predpokladá, že model je platný.
    """

    name: str
    joints: tuple[Joint, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("stroj musí mať neprázdny názov")
        self._check_unique_names()
        self._check_single_parent()
        self._check_acyclic()

    def _check_unique_names(self) -> None:
        seen: set[str] = set()
        for joint in self.joints:
            if joint.name in seen:
                raise ConfigError(f"duplicitný názov kĺbu {joint.name!r}")
            seen.add(joint.name)

    def _check_single_parent(self) -> None:
        """Každý uzol smie byť `child` najviac jedného kĺbu — inak to nie je strom."""
        owner: dict[str, str] = {}
        for joint in self.joints:
            if joint.child in owner:
                raise ConfigError(
                    f"uzol {joint.child!r} je potomkom dvoch kĺbov: "
                    f"{owner[joint.child]!r} a {joint.name!r}. Kinematika musí byť strom."
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
                        f"cyklus v kinematickom reťazci pri uzle {node!r}. "
                        f"Skontroluj parent/child v machines/*.yaml."
                    )
                visited.add(node)
                node = parent_of[node]

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(joint.name for joint in self.joints)

    @property
    def moving_joints(self) -> tuple[Joint, ...]:
        """Kĺby, ktoré sa naozaj hýbu — teda tie, ktoré potrebujú signál."""
        return tuple(j for j in self.joints if j.type is not JointType.FIXED)

    def joint(self, name: str) -> Joint:
        for candidate in self.joints:
            if candidate.name == name:
                return candidate
        known = ", ".join(self.joint_names) or "(žiadne)"
        raise ConfigError(f"kĺb {name!r} v stroji {self.name!r} neexistuje; dostupné: {known}")

    def chain_to_root(self, node: str) -> tuple[Joint, ...]:
        """Kĺby od `node` po koreň, v poradí od najbližšieho k uzlu.

        Slúži na diagnostiku („prečo je tento diel inde, než čakám") — scéna sama
        skladá transformácie hierarchiou NodePath, nie touto funkciou.
        """
        by_child = {joint.child: joint for joint in self.joints}
        chain: list[Joint] = []
        current = node
        while current in by_child:
            joint = by_child[current]
            chain.append(joint)
            current = joint.parent
        return tuple(chain)

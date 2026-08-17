"""Kinematika: hodnota signálu → poloha kĺbu.

Referenčný modul projektu. Čistá funkcia, žiadny stav, žiadna pamäť predchádzajúceho
snímku, žiadna závislosť mimo stdlib. Ak píšeš novú doménovú logiku, napodobni toto.

Vedome sa tu **nepočítajú matice**. Výsledkom je `JointPose` (posun + os a uhol),
ktorý si `viz/` preloží na `NodePath.setPos()` / `setQuat()`. Vďaka tomu doména
nepotrebuje ani numpy, ani lineárnu algebru, a testuje sa porovnaním čísel.
"""

from __future__ import annotations

from dataclasses import dataclass

from pssim.domain.machine import Joint, JointType, Vec3

_ZERO: Vec3 = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class JointPose:
    """Poloha kĺbu voči jeho rodičovi, vrátane pevného offsetu `joint.origin`.

    `rotation_angle_rad` je 0 pre translačné kĺby; `translation` je nulový vektor
    pre rotačné. `is_clamped` hovorí, že vstupná hodnota bola mimo limitov —
    scéna to má zobraziť, nie ignorovať.
    """

    translation: Vec3
    rotation_axis: Vec3
    rotation_angle_rad: float
    is_clamped: bool = False


def clamp_to_limits(joint: Joint, value: float) -> tuple[float, bool]:
    """Obmedzí hodnotu na limity kĺbu. Vracia `(hodnota, bola_obmedzená)`.

    Hodnota mimo limitov nie je chyba — PLC môže poslať čokoľvek a scéna nesmie
    kvôli tomu spadnúť. Ale je to informácia, ktorú chce používateľ vidieť.
    """
    if joint.limits is None:
        return value, False

    low, high = joint.limits
    if value < low:
        return low, True
    if value > high:
        return high, True
    return value, False


def joint_pose(joint: Joint, value: float) -> JointPose:
    """Preloží hodnotu signálu na polohu kĺbu voči rodičovi.

    `value` je už v interných jednotkách (metre / radiány) — prevod zo jednotiek
    PLC sa deje v `config.binding`, nie tu.
    """
    if joint.type is JointType.FIXED:
        return JointPose(
            translation=joint.origin.xyz,
            rotation_axis=joint.axis,
            rotation_angle_rad=0.0,
        )

    clamped, is_clamped = clamp_to_limits(joint, value)

    if joint.type is JointType.PRISMATIC:
        offset_x, offset_y, offset_z = joint.origin.xyz
        axis_x, axis_y, axis_z = joint.axis
        return JointPose(
            translation=(
                offset_x + axis_x * clamped,
                offset_y + axis_y * clamped,
                offset_z + axis_z * clamped,
            ),
            rotation_axis=joint.axis,
            rotation_angle_rad=0.0,
            is_clamped=is_clamped,
        )

    return JointPose(
        translation=joint.origin.xyz,
        rotation_axis=joint.axis,
        rotation_angle_rad=clamped,
        is_clamped=is_clamped,
    )


def rest_pose(joint: Joint) -> JointPose:
    """Poloha kĺbu, keď preň ešte neprišla žiadna hodnota.

    Nie je to nula: ak má kĺb limity, ktoré nulu neobsahujú, nula by diel
    umiestnila mimo fyzicky možný rozsah. Použije sa najbližšia hodnota v limitoch.
    """
    if joint.type is JointType.FIXED or joint.limits is None:
        return joint_pose(joint, 0.0)

    low, high = joint.limits
    initial = min(max(0.0, low), high)
    return joint_pose(joint, initial)


def identity_pose() -> JointPose:
    """Neutrálna poloha — použiteľná ako fallback, keď kĺb nie je známy."""
    return JointPose(translation=_ZERO, rotation_axis=(0.0, 0.0, 1.0), rotation_angle_rad=0.0)

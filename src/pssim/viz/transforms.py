"""Prevod doménových transformácií na to, čo chce Panda3D.

Modul je zámerne **čistý** (numpy, žiadny Panda3D), aby sa konvencia otočení
dala overiť proti rotačnej matici v `tests/unit/`. Konvencie sú tá časť 3D kódu,
kde sa chyba prejaví ako „diel je otočený nejako divne" a ladí sa najhoršie.

Konvencia `domain.machine.Transform.rpy`: **intrinsic XYZ** — najprv otočenie
okolo X, potom okolo nového Y, nakoniec okolo nového Z. To je to, čo dáva
`gp_Trsf.GetRotation().GetEulerAngles(gp_Intrinsic_XYZ)` pri importe STEP.
"""

from __future__ import annotations

import numpy as np

from pssim.domain.machine import Vec3

#: Kvaternión ako (w, x, y, z) — rovnaké poradie ako konštruktor `LQuaternion`.
Quaternion = tuple[float, float, float, float]

IDENTITY_QUAT: Quaternion = (1.0, 0.0, 0.0, 0.0)


def axis_angle_to_quat(axis: Vec3, angle_rad: float) -> Quaternion:
    """Kvaternión otočenia okolo osi. Os sa normalizuje.

    Nulová os dá identitu — kĺb s nulovou osou je síce `ConfigError` pri načítaní,
    ale scéna nesmie spadnúť ani keď sa sem taká hodnota nejako dostane.
    """
    vector = np.asarray(axis, dtype=np.float64)
    length = float(np.linalg.norm(vector))
    if length == 0.0:
        return IDENTITY_QUAT

    unit = vector / length
    half = angle_rad / 2.0
    sin_half = float(np.sin(half))
    return (
        float(np.cos(half)),
        float(unit[0] * sin_half),
        float(unit[1] * sin_half),
        float(unit[2] * sin_half),
    )


def multiply_quat(first: Quaternion, second: Quaternion) -> Quaternion:
    """Hamiltonov súčin. Výsledok zodpovedá matici `R(first) @ R(second)`."""
    w1, x1, y1, z1 = first
    w2, x2, y2, z2 = second
    return (
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    )


def rpy_to_quat(rpy: Vec3) -> Quaternion:
    """Prevedie intrinsic XYZ uhly na kvaternión.

    Zodpovedá matici ``Rx(roll) @ Ry(pitch) @ Rz(yaw)``.
    """
    roll, pitch, yaw = rpy
    return multiply_quat(
        multiply_quat(
            axis_angle_to_quat((1.0, 0.0, 0.0), roll),
            axis_angle_to_quat((0.0, 1.0, 0.0), pitch),
        ),
        axis_angle_to_quat((0.0, 0.0, 1.0), yaw),
    )


def quat_to_matrix(quat: Quaternion) -> np.ndarray:
    """Rotačná matica `3x3` pre konvenciu stĺpcových vektorov (`v' = R @ v`).

    Slúži na overenie konvencií v testoch a na diagnostiku.
    """
    w, x, y, z = quat
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def rotate_point(quat: Quaternion, point: Vec3) -> Vec3:
    """Otočí bod kvaterniónom. Referenčná implementácia pre testy."""
    rotated = quat_to_matrix(quat) @ np.asarray(point, dtype=np.float64)
    return (float(rotated[0]), float(rotated[1]), float(rotated[2]))

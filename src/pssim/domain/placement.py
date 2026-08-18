"""Placing a model in the scene — translation and rotation relative to the origin.

Its purpose is to let a model be "seated" where it belongs: a CAD file has its origin
wherever the designer left it, and that need not be the point you want to measure
against.

**The unit conversion is here, not in the UI.** The user enters millimetres and
degrees (as they are used to from CAD), the scene runs in metres and radians. The
conversion happens once, in one place, and has tests — six fields times two
directions is plenty of opportunity for a typo.

The module is pure (stdlib only), so it can be tested without Qt and without Panda3D.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from pssim.domain.machine import Transform
from pssim.domain.units import DEG_TO_RAD, MM_TO_M

#: A model with no translation and no rotation — that is, as it came from CAD.
IDENTITY_PLACEMENT: Final = Transform()

#: Below this value we consider a placement to be zero. It corresponds to a
#: thousandth of a millimetre and a thousandth of a degree — finer than anything
#: worth entering when setting up a machine.
EPSILON: Final = 1e-9


@dataclass(frozen=True, slots=True)
class PlacementDisplay:
    """A placement in the units the user sees: **millimetres and degrees**.

    Deliberately separate from `Transform` (metres, radians) so the two cannot be
    confused. The type checker then catches anyone passing mm where metres belong.
    """

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    rotate_x_deg: float = 0.0
    rotate_y_deg: float = 0.0
    rotate_z_deg: float = 0.0

    @property
    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (
            self.x_mm,
            self.y_mm,
            self.z_mm,
            self.rotate_x_deg,
            self.rotate_y_deg,
            self.rotate_z_deg,
        )


def to_transform(display: PlacementDisplay) -> Transform:
    """Convert the entered values into an internal transformation (metres, radians)."""
    return Transform(
        xyz=(
            display.x_mm * MM_TO_M,
            display.y_mm * MM_TO_M,
            display.z_mm * MM_TO_M,
        ),
        rpy=(
            display.rotate_x_deg * DEG_TO_RAD,
            display.rotate_y_deg * DEG_TO_RAD,
            display.rotate_z_deg * DEG_TO_RAD,
        ),
    )


def from_transform(transform: Transform) -> PlacementDisplay:
    """Convert an internal transformation back into what the dialog shows."""
    return PlacementDisplay(
        x_mm=transform.xyz[0] / MM_TO_M,
        y_mm=transform.xyz[1] / MM_TO_M,
        z_mm=transform.xyz[2] / MM_TO_M,
        rotate_x_deg=transform.rpy[0] / DEG_TO_RAD,
        rotate_y_deg=transform.rpy[1] / DEG_TO_RAD,
        rotate_z_deg=transform.rpy[2] / DEG_TO_RAD,
    )


def is_identity(transform: Transform) -> bool:
    """Whether the placement does nothing. Used to decide what to report to the user."""
    return all(abs(value) < EPSILON for value in (*transform.xyz, *transform.rpy))


def normalize_degrees(angle_deg: float) -> float:
    """Fold an angle into the range (-180, 180].

    Without it, repeated rotating in the dialog would accumulate numbers like 720°,
    which work but tell nobody how the model is actually oriented.
    """
    wrapped = math.fmod(angle_deg + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0

"""The orbit camera — a pure model with no Panda3D.

The camera state is described spherically: the point it turns around (`target`), the
distance from it, and two angles. That makes orbiting trivial and "up" is never lost —
which is the problem a free camera with a quaternion has.

The module deliberately has no Panda3D, so the whole of the control maths can be tested in
`tests/unit/` without a window. The Panda3D part (reading the mouse, applying it to the
camera) is in `viz/orbit_control.py`.

The coordinates follow Panda3D: **Z is up, +Y is forward**. At `azimuth = 0` the camera is
at `-Y` looking towards `+Y` — that is, the front view.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from pssim.domain.machine import Vec3

#: The elevation must never reach exactly the pole — at the zenith `lookAt` loses its
#: "up" reference and the image flips. We leave a small margin.
MAX_ELEVATION_RAD: Final = math.pi / 2 - 1e-3

#: The zoom limits as a multiple of the scene radius. Without them you can zoom inside a
#: part, or fly so far out that the model disappears into a single pixel.
MIN_DISTANCE_FACTOR: Final = 0.05
MAX_DISTANCE_FACTOR: Final = 50.0

DEFAULT_FOV_DEG: Final = 40.0

#: The default three-quarter view.
DEFAULT_AZIMUTH_RAD: Final = math.radians(-35.0)
DEFAULT_ELEVATION_RAD: Final = math.radians(25.0)

#: The standard views as `(azimuth, elevation)` in radians. The **single source of truth** —
#: `viz/camera.py` derives the direction vector from them, and the UI turns them into menu
#: entries.
#:
#: At `azimuth = 0` the camera is at `-Y` looking towards `+Y`, which is the front view.
#: `top` and `bottom` use the clamped elevation, not exactly `±pi/2` — at the zenith
#: `lookAt` loses its "up" reference and the image flips.
STANDARD_VIEWS: Final[dict[str, tuple[float, float]]] = {
    "iso": (DEFAULT_AZIMUTH_RAD, DEFAULT_ELEVATION_RAD),
    "front": (0.0, 0.0),
    "back": (math.pi, 0.0),
    "right": (math.pi / 2.0, 0.0),
    "left": (-math.pi / 2.0, 0.0),
    "top": (0.0, MAX_ELEVATION_RAD),
    "bottom": (0.0, -MAX_ELEVATION_RAD),
}

DEFAULT_VIEW: Final = "iso"


def standard_view(name: str) -> tuple[float, float]:
    """The angles of a standard view. An unknown name is a `ValueError`."""
    try:
        return STANDARD_VIEWS[name]
    except KeyError:
        known = ", ".join(STANDARD_VIEWS)
        raise ValueError(f"unknown view {name!r}; supported: {known}") from None


class DragAction(StrEnum):
    """What dragging the mouse does."""

    NONE = "none"
    ORBIT = "orbit"
    PAN = "pan"


def drag_action(button: int, *, shift: bool = False) -> DragAction:
    """The mapping from a mouse button to an action.

    The convention is taken from CAD tools (SolidWorks, Fusion, Inventor):

    - **the middle button** — orbit
    - **Shift + middle** — pan
    - **the right button** — pan (a shortcut, so it works without the keyboard)
    - **the left button** — orbit (common in viewers; part selection does not exist yet)

    The whole convention is in this single function — changing the bindings means
    changing it here.
    """
    if button == 2:  # middle
        return DragAction.PAN if shift else DragAction.ORBIT
    if button == 3:  # right
        return DragAction.PAN
    if button == 1:  # left
        return DragAction.ORBIT
    return DragAction.NONE


@dataclass(frozen=True, slots=True)
class OrbitCamera:
    """The position of the camera around the point of interest.

    Immutable — every operation returns a new state. That makes "before and after"
    comparable and makes it impossible to change the state by accident mid-computation.
    """

    target: Vec3 = (0.0, 0.0, 0.0)
    distance_m: float = 1.0
    azimuth_rad: float = DEFAULT_AZIMUTH_RAD
    elevation_rad: float = DEFAULT_ELEVATION_RAD
    min_distance_m: float = 0.01
    max_distance_m: float = 1000.0

    def __post_init__(self) -> None:
        if self.distance_m <= 0.0:
            raise ValueError("distance_m must be > 0")
        if self.min_distance_m > self.max_distance_m:
            raise ValueError("min_distance_m must not be greater than max_distance_m")

    # -- derived vectors ----------------------------------------------------

    @property
    def eye(self) -> Vec3:
        """The position of the camera in the scene."""
        horizontal = self.distance_m * math.cos(self.elevation_rad)
        return (
            self.target[0] + horizontal * math.sin(self.azimuth_rad),
            self.target[1] - horizontal * math.cos(self.azimuth_rad),
            self.target[2] + self.distance_m * math.sin(self.elevation_rad),
        )

    @property
    def forward(self) -> Vec3:
        """The unit vector from the camera to the target."""
        eye = self.eye
        direction = (
            self.target[0] - eye[0],
            self.target[1] - eye[1],
            self.target[2] - eye[2],
        )
        return _normalized(direction)

    @property
    def right(self) -> Vec3:
        """The unit vector pointing right across the screen.

        Always lies in the XY plane — the camera never rolls sideways, which is what a
        user expects when inspecting machines.
        """
        return (math.cos(self.azimuth_rad), math.sin(self.azimuth_rad), 0.0)

    @property
    def up(self) -> Vec3:
        """The unit vector pointing up across the screen."""
        return _normalized(_cross(self.right, self.forward))

    # -- operations ---------------------------------------------------------

    def orbit(self, delta_azimuth_rad: float, delta_elevation_rad: float) -> OrbitCamera:
        """Orbit the camera around the target. The elevation is clamped short of the poles."""
        return replace(
            self,
            azimuth_rad=_wrap_angle(self.azimuth_rad + delta_azimuth_rad),
            elevation_rad=_clamp(
                self.elevation_rad + delta_elevation_rad,
                -MAX_ELEVATION_RAD,
                MAX_ELEVATION_RAD,
            ),
        )

    def zoom(self, factor: float) -> OrbitCamera:
        """Zoom in (`factor < 1`) or out (`factor > 1`).

        Multiplicative, not additive: in a zoomed-out view a wheel step should move the
        camera a long way, in a zoomed-in one only a little. Adding would jump across the
        whole model when looking at a detail.
        """
        if factor <= 0.0:
            raise ValueError("factor must be > 0")
        return replace(
            self,
            distance_m=_clamp(self.distance_m * factor, self.min_distance_m, self.max_distance_m),
        )

    def pan(self, right_m: float, up_m: float) -> OrbitCamera:
        """Move the target (and the camera with it) in the plane of the screen."""
        right = self.right
        up = self.up
        return replace(
            self,
            target=(
                self.target[0] + right[0] * right_m + up[0] * up_m,
                self.target[1] + right[1] * right_m + up[1] * up_m,
                self.target[2] + right[2] * right_m + up[2] * up_m,
            ),
        )

    def pan_pixels(
        self,
        delta_x_px: float,
        delta_y_px: float,
        viewport_height_px: int,
        fov_deg: float = DEFAULT_FOV_DEG,
    ) -> OrbitCamera:
        """Pan by a mouse movement in pixels.

        The scale depends on the distance, so the point under the cursor stays under it
        regardless of the zoom. Without that, panning would feel slow in a zoomed-out
        view and would fly across the whole model in a zoomed-in one.
        """
        if viewport_height_px <= 0:
            return self
        world_per_pixel = (
            2.0 * self.distance_m * math.tan(math.radians(fov_deg) / 2.0) / viewport_height_px
        )
        # Dragging right moves the model right, which means moving the target left.
        return self.pan(-delta_x_px * world_per_pixel, delta_y_px * world_per_pixel)

    def with_view(self, name: str) -> OrbitCamera:
        """Switch to a standard view.

        Neither the target **nor the distance changes** — the user wants to change the
        viewing angle, not lose the zoom they set.
        """
        azimuth, elevation = standard_view(name)
        return replace(self, azimuth_rad=azimuth, elevation_rad=elevation)

    def project(self, vector: Vec3) -> tuple[float, float]:
        """Project a world vector into the plane of the screen as `(right, up)`.

        Used for drawing the orientation icons in the UI: an axis pointing into the
        screen comes out close to `(0, 0)`.
        """
        right, up = self.right, self.up
        return (
            sum(a * b for a, b in zip(vector, right, strict=True)),
            sum(a * b for a, b in zip(vector, up, strict=True)),
        )

    # -- framing ------------------------------------------------------------

    @classmethod
    def framing(
        cls,
        center: Vec3,
        radius_m: float,
        fov_deg: float = DEFAULT_FOV_DEG,
        margin: float = 1.3,
    ) -> OrbitCamera:
        """A camera that has the whole sphere `(center, radius)` in view.

        This is "centre on the model" — called after a file is loaded.
        """
        safe_radius = radius_m if radius_m > 0.0 else 1.0
        distance = safe_radius * margin / math.sin(math.radians(max(fov_deg, 1.0)) / 2.0)
        return cls(
            target=center,
            distance_m=distance,
            min_distance_m=safe_radius * MIN_DISTANCE_FACTOR,
            max_distance_m=safe_radius * MAX_DISTANCE_FACTOR,
        )

    def clip_planes(self) -> tuple[float, float]:
        """The near and far planes for the current distance.

        Derived from the distance rather than fixed: Panda3D's default near of 1.0 clips a
        whole 0.2 m part, while 0.001 on a 20 m line destroys the depth buffer.
        """
        near = max(self.distance_m * 0.001, 1e-4)
        far = self.distance_m * 100.0 + self.max_distance_m
        return near, far


#: How far the camera turns per pixel of dragging. At 0.4°/px a 180° turn takes roughly
#: half the screen, which is the usual pace in CAD tools.
ORBIT_RAD_PER_PIXEL: Final = math.radians(0.4)

#: The distance multiplier per notch of the wheel.
ZOOM_STEP: Final = 1.15


def apply_drag(
    camera: OrbitCamera,
    action: DragAction,
    delta_x_px: float,
    delta_y_px: float,
    viewport_height_px: int,
    fov_deg: float = DEFAULT_FOV_DEG,
) -> OrbitCamera:
    """Project a mouse drag onto a new camera state.

    A pure function — the Panda3D part (`viz/orbit_control.py`) only supplies the numbers.
    The signs are chosen so that it feels like "grabbing the model and turning it":
    dragging right turns the model right, dragging down tilts it towards the viewer.
    """
    if action is DragAction.ORBIT:
        return camera.orbit(
            -delta_x_px * ORBIT_RAD_PER_PIXEL,
            -delta_y_px * ORBIT_RAD_PER_PIXEL,
        )
    if action is DragAction.PAN:
        return camera.pan_pixels(delta_x_px, delta_y_px, viewport_height_px, fov_deg)
    return camera


def apply_wheel(camera: OrbitCamera, steps: int) -> OrbitCamera:
    """Zoom in or out by the given number of wheel notches.

    Positive `steps` zoom in — that is the direction most users expect ("wheel away from
    me = closer to the model").
    """
    if steps == 0:
        return camera
    return camera.zoom(ZOOM_STEP ** (-steps))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _wrap_angle(angle_rad: float) -> float:
    """Fold an angle into (-pi, pi], so it does not grow without bound after many turns."""
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _cross(first: Vec3, second: Vec3) -> Vec3:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _normalized(vector: Vec3) -> Vec3:
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    if length == 0.0:
        return (0.0, 1.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)

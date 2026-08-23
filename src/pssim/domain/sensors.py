"""Sensors: pure geometry, no PLC, no physics.

A sensor reacts to where other objects currently are, using only their
axis-aligned bounding boxes. No physics engine and no Panda3D collision system:
see docs/architecture.md R14 for why, and R16 for what a sensor is.

The box itself and the box-vs-box test live in `domain/collision.py`: a proximity
zone test *is* a collision test, and collision detection is the second consumer of
the same geometry. What stays here is the ray maths, which only a sensor needs.

Seven kinds, in two families:

* **Ray kinds** — `BEAM`, `INDUCTIVE`, `TOF`, `LASER_DISTANCE`. A point, a
  direction and a range. The first two report presence as 0/1; the other two
  report the distance to whatever they hit.
* **`PROXIMITY`** — a box zone round a point, reporting 0/1.
* **Encoders** — `ENCODER_INC`, `ENCODER_ABS`. These sense nothing geometric at
  all: they are bolted to a rotation axis and report its angle as counts.

`BEAM`/`INDUCTIVE` and `TOF`/`LASER_DISTANCE` are pairs whose maths is
**identical**. They are separate kinds because the kind is how the machine is
documented — a photoelectric sensor is not an inductive one on the drawing, even
where the simulation cannot tell them apart.

The module is pure (stdlib only), so all of it is testable without a window, the
same treatment `domain/kinematics.py` gets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pssim.domain.collision import AABB, boxes_overlap
from pssim.domain.errors import ConfigError
from pssim.domain.machine import Vec3
from pssim.domain.units import MM_TO_M

_ZERO: Final[Vec3] = (0.0, 0.0, 0.0)

#: A full turn, for the encoders. Spelled once so counts and angles agree.
_TURN_RAD: Final = 2.0 * math.pi

#: What an encoder reports per revolution when nobody says. 3600 gives a tenth of
#: a degree, which is a common enough resolution to be an unsurprising default.
DEFAULT_COUNTS_PER_REVOLUTION: Final = 3600


class SensorKind(StrEnum):
    """The kind of reading a sensor produces."""

    BEAM = "beam"
    """Photoelectric: 0/1, blocked when something crosses the ray."""

    INDUCTIVE = "inductive"
    """Presence: 0/1, the same maths as `BEAM` with a different label."""

    TOF = "tof"
    """Time of flight: the distance to the nearest object along the ray."""

    LASER_DISTANCE = "laser_distance"
    """Laser rangefinder: the same maths as `TOF` with a different label."""

    ENCODER_INC = "encoder_inc"
    """Incremental encoder on a rotation axis: counts, accumulating past a turn."""

    ENCODER_ABS = "encoder_abs"
    """Absolute encoder on a rotation axis: counts within one turn."""

    PROXIMITY = "proximity"
    """A box zone round `origin`: 0/1 when anything enters it."""


#: The kinds that cast a ray — a point, a direction and a range.
RAY_KINDS: Final = frozenset(
    {SensorKind.BEAM, SensorKind.INDUCTIVE, SensorKind.TOF, SensorKind.LASER_DISTANCE}
)

#: The ray kinds that report a distance rather than 0/1.
DISTANCE_KINDS: Final = frozenset({SensorKind.TOF, SensorKind.LASER_DISTANCE})

#: The kinds bolted to a rotation axis, which sense no geometry at all.
ENCODER_KINDS: Final = frozenset({SensorKind.ENCODER_INC, SensorKind.ENCODER_ABS})


@dataclass(frozen=True, slots=True)
class SensorReading:
    """What a sensor currently reports.

    One type for every kind, so the caller never has to know which it is holding.
    A 0/1 sensor sets `value` to 0.0 or 1.0 and is always valid.

    `is_valid` exists for the distance kinds: with nothing in range they report
    the range itself, flagged invalid. A single number could not carry that —
    zero would mean both "touching the sensor" and "nothing there", which are
    opposite situations.
    """

    value: float
    is_valid: bool = True


@dataclass(frozen=True, slots=True)
class Sensor:
    """One sensor, in the frame of whatever it is mounted on.

    `origin` and `direction` are **mount-local**: a sensor bolted to a carriage
    rides it, which is the only way a moving machine's sensors make sense. What it
    is mounted on is not stored here — that is a scene relationship, held by
    `ui.sensor_registry` the same way a model's binding to a joint is.

    Fields are read per kind, the shape `domain.machine.Joint` and
    `domain.model_joints.ModelJoint` already use:

    | field | kinds |
    |---|---|
    | `direction`, `range_m` | the ray kinds |
    | `half_extent_m` | `PROXIMITY` |
    | `counts_per_revolution` | the encoders |

    `variable` is a forward-looking label only — nothing drives it yet; it exists
    so a future communication link (OPC UA/TCP-IP) has a name to bind to, exactly
    as `ModelJoint.variable` does.
    """

    name: str
    kind: SensorKind
    variable: str = ""
    origin: Vec3 = _ZERO
    direction: Vec3 = (0.0, 0.0, 1.0)
    """Which way the ray points, magnitude irrelevant — it is normalised on use,
    so `(0,0,1)` and `(0,0,100)` are the same ray. Same reasoning as
    `ModelJoint.direction`: a direction has no length."""

    range_m: float = 1.0
    """How far a ray sensor sees. A distance kind with nothing inside this
    reports it, flagged invalid."""

    half_extent_m: float = 0.1
    counts_per_revolution: int = DEFAULT_COUNTS_PER_REVOLUTION

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("a sensor must have a non-empty name")

        if self.kind in RAY_KINDS:
            if not any(self.direction):
                raise ConfigError(
                    f"sensor {self.name!r}: the direction is zero "
                    "(a ray sensor needs a direction to look along)"
                )
            if self.range_m <= 0.0:
                raise ConfigError(f"sensor {self.name!r}: range_m must be > 0, got {self.range_m}")

        if self.kind is SensorKind.PROXIMITY and self.half_extent_m <= 0.0:
            raise ConfigError(
                f"sensor {self.name!r}: half_extent_m must be > 0, got {self.half_extent_m}"
            )

        if self.kind in ENCODER_KINDS and self.counts_per_revolution <= 0:
            raise ConfigError(
                f"sensor {self.name!r}: counts_per_revolution must be > 0, "
                f"got {self.counts_per_revolution}"
            )


def unit_direction(sensor: Sensor) -> Vec3:
    """The sensor's direction, normalised. Safe: `__post_init__` rejects zero."""
    dx, dy, dz = sensor.direction
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    return (dx / length, dy / length, dz / length)


def ray_end(sensor: Sensor) -> Vec3:
    """Where the ray stops — `origin` plus one range along the direction.

    Used for drawing the marker and for the segment tests, so the line that is
    drawn and the line that is tested are the same line.
    """
    ox, oy, oz = sensor.origin
    dx, dy, dz = unit_direction(sensor)
    return (ox + dx * sensor.range_m, oy + dy * sensor.range_m, oz + dz * sensor.range_m)


def ray_distance_to(origin: Vec3, direction: Vec3, box: AABB, range_m: float) -> float | None:
    """How far along a **unit** `direction` the ray first enters `box`, or `None`.

    The slab method: clip the ray's parameter range against each pair of box
    planes in turn. A ray parallel to an axis never narrows the range on that
    axis, so it is handled separately — dividing by a zero component would
    otherwise raise.

    The parameter is a real distance because `direction` is a unit vector, which
    is what lets a distance sensor report the answer directly. A ray starting
    *inside* the box reads zero, which is the honest answer for a sensor buried
    in something.
    """
    t_min, t_max = 0.0, range_m
    for axis in range(3):
        start = origin[axis]
        component = direction[axis]
        low, high = box.low[axis], box.high[axis]

        if component == 0.0:
            if start < low or start > high:
                return None
            continue

        first = (low - start) / component
        second = (high - start) / component
        if first > second:
            first, second = second, first
        t_min = max(t_min, first)
        t_max = min(t_max, second)
        if t_min > t_max:
            return None
    return t_min


def nearest_along_ray(sensor: Sensor, others: tuple[AABB, ...]) -> float | None:
    """The distance to the closest box the ray meets, or `None` if it meets none."""
    direction = unit_direction(sensor)
    hits = [
        distance
        for box in others
        if (distance := ray_distance_to(sensor.origin, direction, box, sensor.range_m)) is not None
    ]
    return min(hits) if hits else None


def zone_of(sensor: Sensor) -> AABB:
    """The box a proximity sensor watches: `origin` plus/minus `half_extent_m`
    on every axis."""
    ox, oy, oz = sensor.origin
    half = sensor.half_extent_m
    return AABB(low=(ox - half, oy - half, oz - half), high=(ox + half, oy + half, oz + half))


def is_blocked(sensor: Sensor, others: tuple[AABB, ...]) -> bool:
    """Whether a ray sensor currently meets anything within its range."""
    return nearest_along_ray(sensor, others) is not None


def is_triggered(sensor: Sensor, others: tuple[AABB, ...]) -> bool:
    """Whether a `PROXIMITY` sensor's zone currently overlaps anything."""
    zone = zone_of(sensor)
    return any(boxes_overlap(zone, box) for box in others)


def counts_for(sensor: Sensor, angle_rad: float) -> float:
    """The angle as encoder counts.

    `ENCODER_ABS` wraps into one turn, so it always reads inside
    `[0, counts_per_revolution)` — that is what "absolute" means on a
    single-turn encoder. `ENCODER_INC` keeps counting, so it can exceed a turn
    and go negative, which is what an incremental one does.

    Counts rather than degrees because that is what the hardware reports, and a
    resolution is a per-encoder property rather than something to assume.
    """
    turns = angle_rad / _TURN_RAD
    if sensor.kind is SensorKind.ENCODER_ABS:
        turns -= math.floor(turns)
    return turns * sensor.counts_per_revolution


def read_sensor(
    sensor: Sensor, others: tuple[AABB, ...] = (), angle_rad: float = 0.0
) -> SensorReading:
    """What the sensor reports right now. Dispatches on `sensor.kind`, so the
    caller — the per-frame evaluation in `viz/` — never has to.

    `others` is the geometry it can see; `angle_rad` is the value of the joint an
    encoder is bolted to. Each kind ignores the one it has no use for.
    """
    if sensor.kind in ENCODER_KINDS:
        return SensorReading(value=counts_for(sensor, angle_rad))

    if sensor.kind in DISTANCE_KINDS:
        distance = nearest_along_ray(sensor, others)
        if distance is None:
            # Nothing in range: report the range, flagged invalid. See
            # `SensorReading.is_valid` for why this is not zero.
            return SensorReading(value=sensor.range_m, is_valid=False)
        return SensorReading(value=distance)

    if sensor.kind is SensorKind.PROXIMITY:
        return SensorReading(value=1.0 if is_triggered(sensor, others) else 0.0)

    return SensorReading(value=1.0 if is_blocked(sensor, others) else 0.0)


def is_active(sensor: Sensor, others: tuple[AABB, ...] = (), angle_rad: float = 0.0) -> bool:
    """Whether the sensor is *seeing* something, for the marker's colour.

    A 0/1 kind is active when it reads 1; a distance kind when its reading is
    valid, which is the same question. An encoder is never "active" — it always
    has a reading, so colouring it by one would say nothing.
    """
    if sensor.kind in ENCODER_KINDS:
        return False
    reading = read_sensor(sensor, others, angle_rad)
    if sensor.kind in DISTANCE_KINDS:
        return reading.is_valid
    return reading.value != 0.0


@dataclass(frozen=True, slots=True)
class SensorDisplay:
    """A sensor in the units the user sees: **millimetres** and counts.

    `direction` stays unitless — it is a direction, and scaling it would mean
    nothing. Same exception `AnchorDisplay.direction` and `ModelJointDisplay`
    already make.
    """

    name: str = ""
    kind: SensorKind = SensorKind.BEAM
    variable: str = ""
    origin_mm: Vec3 = _ZERO
    direction: Vec3 = (0.0, 0.0, 1.0)
    range_mm: float = 1000.0
    half_extent_mm: float = 100.0
    counts_per_revolution: int = DEFAULT_COUNTS_PER_REVOLUTION


def to_sensor(display: SensorDisplay) -> Sensor:
    """Convert the entered values into an internal sensor (metres)."""
    return Sensor(
        name=display.name,
        kind=display.kind,
        variable=display.variable,
        origin=(
            display.origin_mm[0] * MM_TO_M,
            display.origin_mm[1] * MM_TO_M,
            display.origin_mm[2] * MM_TO_M,
        ),
        direction=display.direction,
        range_m=display.range_mm * MM_TO_M,
        half_extent_m=display.half_extent_mm * MM_TO_M,
        counts_per_revolution=display.counts_per_revolution,
    )


def from_sensor(sensor: Sensor) -> SensorDisplay:
    """Convert an internal sensor back into what a dialog shows."""
    return SensorDisplay(
        name=sensor.name,
        kind=sensor.kind,
        variable=sensor.variable,
        origin_mm=(
            sensor.origin[0] / MM_TO_M,
            sensor.origin[1] / MM_TO_M,
            sensor.origin[2] / MM_TO_M,
        ),
        direction=sensor.direction,
        range_mm=sensor.range_m / MM_TO_M,
        half_extent_mm=sensor.half_extent_m / MM_TO_M,
        counts_per_revolution=sensor.counts_per_revolution,
    )

"""User-facing text that is assembled from numbers.

Formatting messages is a **UI matter, not a domain one** — it needs translation, and the
domain has no way of knowing what language the application is currently running in. Hence
here, not in `domain/`.

All the text goes through `QCoreApplication.translate()` so it can be extracted into the
`.ts` file. See `ui/translations/README.md`.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor

from pssim.cad.model import CadAssembly
from pssim.domain.machine import Transform
from pssim.domain.placement import from_transform, is_identity
from pssim.domain.sensors import DISTANCE_KINDS, ENCODER_KINDS, SensorKind
from pssim.domain.units import MM_TO_M
from pssim.ui.sensor_registry import SensorEntry
from pssim.viz.sensor_markers import ACTIVE_COLOR

#: The context for `lupdate`. It must be constant, or the translations fall apart.
CONTEXT: Final = "labels"

#: Shown wherever a field has nothing to report. One spelling, so a dash in the
#: tree and a dash in the properties panel are recognisably the same statement.
NOT_APPLICABLE: Final = "\u2014"


def _tr(text: str) -> str:
    return QCoreApplication.translate(CONTEXT, text)


def describe_placement(transform: Transform) -> str:
    """A one-line statement about a model's placement, for the status bar.

    It states the units the user entered (mm, degrees), not the internal ones — otherwise
    after typing "100 mm" they would see "0.1" and go looking for where it went.
    """
    if is_identity(transform):
        return _tr("Model at origin, no rotation")

    display = from_transform(transform)
    # Placeholders, not sentences glued together — another language may need a different order.
    return _tr("Moved {0}, {1}, {2} mm; rotated {3}, {4}, {5}°").format(
        f"{display.x_mm:g}",
        f"{display.y_mm:g}",
        f"{display.z_mm:g}",
        f"{display.rotate_x_deg:g}",
        f"{display.rotate_y_deg:g}",
        f"{display.rotate_z_deg:g}",
    )


def describe_assembly(assembly: CadAssembly | None) -> str:
    """A one-line statement about an imported model, for the status bar."""
    if assembly is None:
        return _tr("Model loaded")
    return _tr("{0} parts, {1} triangles").format(len(assembly.nodes), assembly.triangle_count)


def missing_geometry_suffix(missing: int) -> str:
    """The addition to the message when part of the model has no geometry in the cache."""
    return _tr(" — geometry missing for {0} part(s)").format(missing)


def has_detection_state(kind: SensorKind) -> bool:
    """Whether "is something there" is a question this kind of sensor answers.

    False for the encoders alone. They are bolted to an axis and report its
    angle; they look for nothing, so `is_active` is `False` for them forever and
    any word put in a State column would be describing a failure to detect that
    was never attempted. See docs/architecture.md R16.
    """
    return kind not in ENCODER_KINDS


def live_reading_color() -> QColor:
    """The background a live reading gets, in the dock and in the panel alike.

    Exactly the RGBA `viz/sensor_markers` draws a sensor that sees something in,
    so a green cell and a green marker cannot come to mean different things.
    Only the green half of that pair is used: the red one is for the scene, and
    in a table a red cell reads as an error, which "not seeing anything" is not.
    """
    red, green, blue, _alpha = ACTIVE_COLOR
    return QColor.fromRgbF(red, green, blue)


def is_reading_live(entry: SensorEntry) -> bool:
    """Whether the sensor's number is something it is actually measuring now.

    True for a 0/1 kind that is detecting and for a rangefinder within range;
    false for either idling, and false for an encoder always — an encoder reports
    an angle rather than a detection, so there is no "live" for it to be.

    This is what earns the green background in the dock and in the properties
    panel. `is_active` already means exactly this per family (`value != 0` for
    the 0/1 kinds, `reading.is_valid` for the distance ones), so the two cannot
    disagree with the word in the State column.
    """
    return has_detection_state(entry.sensor.kind) and entry.is_active


def describe_state(entry: SensorEntry) -> str:
    """The State column: what the sensor is currently saying, in one word.

    Three phrasings rather than one pair, because the three families are
    answering different questions. "Clear" fits a beam with nothing crossing it;
    a rangefinder in the same condition is **out of range**, which is a statement
    about the sensor's reach rather than about an empty space in front of it.
    """
    kind = entry.sensor.kind
    if not has_detection_state(kind):
        return NOT_APPLICABLE
    if kind in DISTANCE_KINDS:
        return _tr("In range") if entry.reading.is_valid else _tr("Out of range")
    return _tr("Detected") if entry.is_active else _tr("Clear")


def describe_state_tooltip(entry: SensorEntry) -> str:
    """The sentence behind the one word, for the cell's tooltip.

    The word alone cannot say what the sensor was looking for, and that is
    exactly what a reader wants when a row says something unexpected.
    """
    kind = entry.sensor.kind
    if not has_detection_state(kind):
        return _tr("An encoder detects nothing — it reports the angle of its axis")
    if kind in DISTANCE_KINDS:
        if entry.reading.is_valid:
            return _tr("Measuring {0} mm").format(f"{entry.reading.value / MM_TO_M:g}")
        return _tr("Nothing within the {0} mm range").format(f"{entry.sensor.range_m / MM_TO_M:g}")
    if kind is SensorKind.PROXIMITY:
        return _tr("Something is inside the zone") if entry.is_active else _tr("The zone is empty")
    if entry.is_active:
        return _tr("Something is crossing the beam")
    return _tr("Nothing is crossing the beam")


def describe_reading(entry: SensorEntry) -> str:
    """The number the sensor reports, in the units the user thinks in.

    Millimetres for a distance, counts for an encoder, 0/1 for the rest — the
    same boundary rule as everywhere: the display converts, the domain does not.
    An invalid distance reads as a dash rather than a number, because the number
    would be the range and would look like a measurement.
    """
    if entry.sensor.kind in DISTANCE_KINDS:
        if not entry.reading.is_valid:
            return NOT_APPLICABLE
        return f"{entry.reading.value / MM_TO_M:.1f} mm"
    return f"{entry.reading.value:.0f}"

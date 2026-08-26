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
from pssim.config.binding import BindingDirection
from pssim.domain.errors import ConfigError
from pssim.domain.machine import Transform
from pssim.domain.placement import from_transform, is_identity
from pssim.domain.sensors import DISTANCE_KINDS, ENCODER_KINDS, SensorKind
from pssim.domain.units import MM_TO_M
from pssim.io.base import SourceStatus
from pssim.ui.sensor_registry import SensorEntry
from pssim.ui.variable_registry import VariableEntry, VariableState
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


#: What each variable state is called in the Variables tab. Every state is
#: listed: a missing one would raise on a row rather than degrade, and a table
#: with one unlabelled variable is not worth crashing over.
_VARIABLE_STATES: Final[dict[VariableState, str]] = {
    VariableState.UNBOUND: "No tag",
    VariableState.OFFLINE: "Disconnected",
    VariableState.WAITING: "Waiting",
    VariableState.LIVE: "Online",
    VariableState.STALE: "Stale",
}


def out_of_range_color() -> QColor:
    """The colour a value the joint could not reach is written in.

    Stated rather than taken from the palette: a palette has no "this number is
    wrong" role, and this has to read as a fault on a light theme and a dark one
    alike. Darker than a pure red so it stays legible on a white row.
    """
    return QColor(200, 40, 40)


def describe_applied(is_applied: bool) -> str:
    """The Apply column's tooltip: what the checkbox does, both ways round."""
    if is_applied:
        return _tr("Values from the server move the model. Clear this to set it by hand.")
    return _tr("Values still arrive but do not move the model - set it by hand instead.")


def describe_tag_conversion(decimals: int, offset: float, unit: str) -> str:
    """One worked example of what a tag does, for the assign dialog.

    Arithmetic rather than a rule: a wrong decimal place is invisible in the
    fields and obvious in a line that says what the PLC's 652 would become. This
    is the one setting in the application that silently puts a model elsewhere.
    """
    result = 652.0 / (10.0**decimals) + offset
    return _tr("The PLC's 652 becomes {0} {1}").format(f"{result:g}", unit)


def describe_access(can_read: bool, can_write: bool) -> str:
    """What the node's `UserAccessLevel` said, in the R/W/RW a PLC person reads.

    Shown beside the direction radios so a greyed-out one is explained rather
    than merely unavailable.
    """
    if can_read and can_write:
        return _tr("the node is RW")
    if can_write:
        return _tr("the node is write-only")
    if can_read:
        return _tr("the node is read-only")
    return _tr("the node allows neither")


def describe_variable_state(entry: VariableEntry) -> str:
    """The Status column: where this variable stands with the server."""
    return _tr(_VARIABLE_STATES.get(entry.state, entry.state.value))


def describe_variable_state_tooltip(entry: VariableEntry) -> str:
    """The sentence behind the word.

    "Stale" in particular needs one: the scene is still drawing the last value
    it had (R10), which is deliberate and looks like nothing being wrong.
    """
    if entry.state is VariableState.UNBOUND:
        return _tr("No OPC UA tag assigned - this variable reads from nothing")
    if entry.state is VariableState.OFFLINE:
        return _tr("Not connected to the server")
    if entry.state is VariableState.WAITING:
        return _tr("Subscribed, but the server has not sent a value yet")
    if entry.state is VariableState.STALE:
        return _tr("The last value is old - the scene is still showing it")
    return _tr("Receiving values")


#: The four source states, as the status bar says them. `DEGRADED` is not
#: "Connected" with a footnote: the connection is alive and at least one signal
#: is old, and the scene is drawing that old value (R10) — which looks like
#: nothing being wrong, so the word has to say it.
_SOURCE_STATES: Final[dict[SourceStatus, str]] = {
    SourceStatus.DISCONNECTED: "Disconnected",
    SourceStatus.CONNECTING: "Connecting",
    SourceStatus.CONNECTED: "Connected",
    SourceStatus.DEGRADED: "Stale data",
}


def describe_source_status(status: SourceStatus) -> str:
    """Where the connection to the server stands, in one or two words."""
    return _tr(_SOURCE_STATES.get(status, status.value))


def source_status_color(status: SourceStatus) -> QColor:
    """The indicator's colour.

    Stated rather than taken from the palette: a palette's "highlight" means
    "selected", not "connected", and the three states have to be told apart at a
    glance on a light theme and a dark one alike. Green is the same green a live
    sensor reading gets, so one green never comes to mean two things.
    """
    if status is SourceStatus.CONNECTED:
        return live_reading_color()
    if status is SourceStatus.DISCONNECTED:
        return QColor(150, 150, 150)
    # Connecting and degraded share an amber: both are "not settled yet", and
    # inventing a fourth colour for a state that lasts a second would only make
    # the indicator harder to read.
    return QColor(230, 160, 30)


def describe_source_status_tooltip(status: SourceStatus, endpoint: str, reason: str) -> str:
    """The sentence behind the word, with the endpoint and — when there is one —
    why it is not connected.

    The reason is here rather than in the label: it is a status code, it is long,
    and the status bar is one line. R20 put it in `Communication → Diagnostics…`
    for reading; this is the shortest path to it.
    """
    if status is SourceStatus.CONNECTED:
        return _tr("Connected to {0}").format(endpoint)
    if status is SourceStatus.DEGRADED:
        return _tr("Connected to {0}, but a signal has stopped arriving").format(endpoint)
    if status is SourceStatus.CONNECTING:
        return _tr("Trying to reach {0}").format(endpoint)
    if reason:
        return _tr("Not connected to {0}: {1}").format(endpoint, reason)
    return _tr("Not connected to {0}").format(endpoint)


def describe_direction(direction: BindingDirection) -> str:
    """Which way a variable travels, as the tab shows it."""
    if direction is BindingDirection.WRITE:
        return _tr("Write")
    return _tr("Read")


def describe_direction_tooltip(direction: BindingDirection) -> str:
    """Why the direction matters, since one of the two can leave this process."""
    if direction is BindingDirection.WRITE:
        return _tr("Published to the server, not read from it - and only when writing is allowed")
    return _tr("Read from the server; the PLC decides the value")


def describe_variable_value(entry: VariableEntry) -> str:
    """The value the **tag** holds, which is what this table is about.

    Converted back out of the scene's units, because a row saying `1.25` for a
    tag holding `1250` invites a hunt for a bug that is not there. `to_plc` is
    the exact inverse of the conversion on the way in (R8), so the number shown
    is the number on the server.
    """
    if entry.value is None:
        return NOT_APPLICABLE
    return f"{_plc_value(entry):.6g}"


def describe_variable_value_tooltip(entry: VariableEntry) -> str:
    """Both numbers, so the conversion is visible rather than mysterious.

    And, when the joint could not reach it, that it did not: the number in the
    cell is the one that arrived, so without this the row would show a value the
    model is not actually at.
    """
    if entry.value is None:
        return _tr("No value yet")
    both = _tr("{0} on the server, {1} in the scene (metres / radians)").format(
        f"{_plc_value(entry):.6g}", f"{entry.value:.6g}"
    )
    if not entry.is_out_of_range:
        return both
    return both + _tr(" - outside the joint's limits, which is where it is instead")


def _plc_value(entry: VariableEntry) -> float:
    """The value in the PLC's own units, or the internal one when there is no
    way back — a binding with scale 0 has no inverse (see `config.binding`)."""
    binding = entry.binding()
    if binding is None:
        return entry.value if entry.value is not None else 0.0
    try:
        return binding.to_plc(entry.value if entry.value is not None else 0.0)
    except ConfigError:
        return entry.value if entry.value is not None else 0.0

"""Turning a number into what one OPC UA node will actually accept.

Everything inside this application is a `float` — a joint's value, a sensor's
reading, whatever the interpolation produced. A server is not so relaxed: a
`Boolean` node wants `True`, an `Int32` wants an `int`, and handing either a
`3.0` is at best silently rounded and at worst refused.

The write path used to state `VariantType.Double` for every node, with a comment
saying it had been verified against a `Double` node — which it had. A 0/1 sensor
bound to a `Boolean`, which is what a PLC programmer would naturally use for one,
was refused by the server.

**Pure — stdlib only, keyed by the type's name** rather than by
`ua.VariantType`, so the rounding rules are testable in milliseconds without
asyncua. The source maps a name onto the real enum.
"""

from __future__ import annotations

from typing import Final

#: Variant types that hold a truth value.
BOOLEAN_TYPES: Final = frozenset({"Boolean"})

#: Variant types that hold a whole number. A PLC's `INT`/`DINT`/`UINT` land on
#: these; a fractional position is carried as implied decimals (R19), so what
#: arrives here is already scaled and only needs rounding.
INTEGER_TYPES: Final = frozenset(
    {
        "SByte",
        "Byte",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
    }
)

#: Variant types that hold a real number, and need nothing done to them.
FLOAT_TYPES: Final = frozenset({"Float", "Double"})

#: What a node whose type could not be read is written as. `Double` because it is
#: what every existing setup writes to and what the path did unconditionally
#: before this existed — an unknown type must not change the behaviour of a
#: configuration that already worked.
FALLBACK_TYPE: Final = "Double"


def coerce_for(value: float, type_name: str) -> bool | int | float:
    """One number, as the named variant type wants it.

    Rounded rather than truncated for an integer: `int(0.9)` is `0`, and a sensor
    reading 0.9999 of a count would then write a zero. `round` is what a PLC
    programmer means by "the nearest".

    A truth value is `!= 0` rather than `bool(value)` only in spirit — they agree
    — but the intent is that any non-zero reading is a detection, which is how
    `SensorReading.is_active` already reads it (R16).
    """
    if type_name in BOOLEAN_TYPES:
        return value != 0.0
    if type_name in INTEGER_TYPES:
        return int(round(value))
    return float(value)


def is_writable_type(type_name: str) -> bool:
    """Whether a number can be written to a node of this type at all.

    A `String` or a `DateTime` node is perfectly real and nothing here can drive
    it. Answered separately from `coerce_for` so a caller may refuse the binding
    rather than write a number that means nothing.
    """
    return type_name in BOOLEAN_TYPES | INTEGER_TYPES | FLOAT_TYPES

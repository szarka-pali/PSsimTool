"""Addressing one field of a struct or one element of an array.

**A field is not a node.** A server's address space has a node for
`Struct.AxisState` and nothing for `Position.X` inside it — the value arrives as
one `ExtensionObject` and the field is pulled out of it here. Two facts, both
verified in a REPL against a live server, force that shape:

- `subscribe_data_change` in asyncua leaves `IndexRange` null on purpose ("then
  the entire array is returned"), so there is no subscribing to one element.
- asyncua's own server ignores `IndexRange` on a read as well: asking for `'1'`
  of a four-element array returned all four with a good status.

So a tag is a node id **plus a path**, and the path is applied where the scaling
already happens — at the boundary, once (R8).

The path is text (`Position.X`, `Limits[1]`, `Drive.Axes[2].Actual`) because it
goes into a settings file and a machine definition: one spelling, readable by
whoever opens the file, and no new structure in a versioned format.

This module is **pure** — stdlib only, no asyncua — so walking a path is testable
in milliseconds without a server, which is what the rest of this cannot be.
"""

from __future__ import annotations

import re
from typing import Any, Final

from pssim.domain.errors import DataSourceError

#: One segment: a field name, then any number of `[index]` subscripts.
_SEGMENT: Final = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\[\d+\])*)$")

#: A leading subscript with no name, for a path into a bare array (`[2]`).
_BARE_INDEX: Final = re.compile(r"^((?:\[\d+\])+)$")

_INDEX: Final = re.compile(r"\[(\d+)\]")

#: A path segment beyond this depth is a sign of a loop, not of a data structure.
#: A real PLC's nesting is single digits.
MAX_DEPTH: Final = 16

#: How many elements of an array are worth listing. A tree is not the place to
#: show a hundred thousand of anything, and a PLC array that large is a buffer
#: rather than something a joint reads from.
MAX_ELEMENTS: Final = 512

#: What a path segment is, once parsed: a field name or an array index.
type PathStep = str | int

#: A parsed path. Empty means the node's own value, which is what every tag
#: written before this existed means.
type ValuePath = tuple[PathStep, ...]


def parse_path(path: str) -> ValuePath:
    """`"Limits[1]"` -> `("Limits", 1)`. An empty string is the empty path.

    Raises `DataSourceError` on anything else: a path arrives from a settings
    file or a machine definition, which are outside data, and a silent
    reinterpretation of a typo would bind a joint to the wrong number.
    """
    text = path.strip()
    if not text:
        return ()

    steps: list[PathStep] = []
    for segment in text.split("."):
        steps.extend(_parse_segment(segment, path))
    if len(steps) > MAX_DEPTH:
        raise DataSourceError(f"path {path!r} is nested deeper than {MAX_DEPTH}")
    return tuple(steps)


def _parse_segment(segment: str, whole: str) -> list[PathStep]:
    bare = _BARE_INDEX.match(segment)
    if bare is not None:
        return [int(found) for found in _INDEX.findall(bare.group(1))]

    match = _SEGMENT.match(segment)
    if match is None:
        raise DataSourceError(f"{segment!r} is not a field name or an index, in path {whole!r}")
    steps: list[PathStep] = [match.group(1)]
    steps.extend(int(found) for found in _INDEX.findall(match.group(2)))
    return steps


def format_path(path: ValuePath) -> str:
    """`("Limits", 1)` -> `"Limits[1]"`. The inverse of `parse_path`."""
    text = ""
    for step in path:
        if isinstance(step, int):
            text += f"[{step}]"
        elif text:
            text += f".{step}"
        else:
            text = step
    return text


def child_path(parent: str, step: PathStep) -> str:
    """The path of one field or element inside `parent`.

    Assembled through the parsed form rather than by pasting text together, so
    `""` + `"Position"` and `"Limits"` + `1` come out as `Position` and
    `Limits[1]` without the caller having to know which separator applies.
    """
    return format_path((*parse_path(parent), step))


def resolve_value(value: Any, path: ValuePath) -> Any:
    """Follow a path into a decoded value.

    A struct arrives as a dataclass (asyncua's `load_data_type_definitions`
    generates the classes) and an array as a list, so the walk is `getattr` and
    indexing. An empty path is the value itself.

    Raises `DataSourceError` when the path does not fit the value — a field that
    the server renamed, an index past the end. The caller marks that signal bad;
    it must never take the subscription down with it.
    """
    current = value
    for depth, step in enumerate(path):
        current = _step_into(current, step, path, depth)
    return current


def _step_into(current: Any, step: PathStep, path: ValuePath, depth: int) -> Any:
    where = format_path(path[: depth + 1])
    if isinstance(step, int):
        if not isinstance(current, (list, tuple)):
            raise DataSourceError(f"{where} is not an array in the value received")
        if step >= len(current):
            raise DataSourceError(f"{where} is past the end of an array of {len(current)}")
        return current[step]

    if not hasattr(current, step):
        raise DataSourceError(f"the value received has no field {where}")
    return getattr(current, step)


def is_numeric_value(value: Any) -> bool:
    """Whether a resolved value is something a joint can be driven by.

    `bool` counts, and it counts because it is an `int` subclass rather than by
    special case: a PLC's `Enabled` flag is a perfectly ordinary thing to show,
    and it arrives as one. What does not count is a string, a struct or a list —
    the last of which is the whole reason an array's parent row cannot be bound.
    """
    return isinstance(value, (int, float))

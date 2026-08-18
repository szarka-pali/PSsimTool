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

from pssim.cad.model import CadAssembly
from pssim.domain.machine import Transform
from pssim.domain.placement import from_transform, is_identity

#: The context for `lupdate`. It must be constant, or the translations fall apart.
CONTEXT: Final = "labels"


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

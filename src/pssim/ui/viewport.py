"""The 3D area of the window — a Qt wrapper around `viz.embed.EmbeddedRenderer`.

Deliberately **does not import Panda3D**. Everything it knows about it sits
behind `EmbeddedRenderer`; this file handles only the Qt side: when the window is
created, when it redraws, and what happens on a resize.

Two things that surprise:

- The render loop is ticked by a `QTimer`. `base.run()` would take over and Qt
  would freeze.
- Mouse and keyboard go to the **Panda3D window**, not to this widget. Camera
  control is therefore in `viz/orbit_control.py`, not in `mousePressEvent()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget

from pssim.cad.model import CadAssembly
from pssim.domain.machine import Transform
from pssim.domain.placement import IDENTITY_PLACEMENT
from pssim.observability import get_logger

logger = get_logger(__name__)

#: ~60 fps. A Qt timer is not precise, but it is enough for looking at a model
#: and it does not load the CPU the way a continuous loop would.
FRAME_INTERVAL_MS: Final = 16


class Panda3DViewport(QWidget):
    """The widget Panda3D draws into.

    The renderer is created on first show — before that there is no `winId()`
    and nothing to attach to.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._renderer: Any = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

        # Qt must not paint into this widget: it would cover the Panda3D window.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(320, 240)

    # -- lifecycle ----------------------------------------------------------

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt convention
        super().showEvent(event)
        if self._renderer is None:
            self._create_renderer()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt convention
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt convention
        super().resizeEvent(event)
        if self._renderer is not None:
            self._renderer.resize(*self._device_size())

    def shutdown(self) -> None:
        """Stop redrawing. Idempotent."""
        self._timer.stop()
        if self._renderer is not None:
            self._renderer.shutdown()

    def _device_size(self) -> tuple[int, int]:
        """The widget size in **physical** pixels.

        Qt counts in logical pixels, the native Panda3D window in physical ones.
        At 125 % Windows scaling the difference is 1.25x and shows up as a black
        band on the right and bottom: the Panda3D window is smaller than the area
        it is meant to fill.
        """
        ratio = self.devicePixelRatioF()
        return (max(int(self.width() * ratio), 1), max(int(self.height() * ratio), 1))

    def _create_renderer(self) -> None:
        from pssim.viz.embed import EmbeddedRenderer

        width, height = self._device_size()
        try:
            self._renderer = EmbeddedRenderer(
                parent_handle=int(self.winId()),
                width=width,
                height=height,
            )
        except Exception:
            # The application is still usable without a 3D area (menu, file
            # dialog), so it must not die: log it and leave the viewport empty.
            logger.exception("3D viewport could not be created")
            return

        self._timer.start(FRAME_INTERVAL_MS)

    def _step(self) -> None:
        """One frame. Must never raise — that would stop the timer."""
        if self._renderer is None:
            return
        try:
            self._renderer.step()
        except Exception:
            logger.exception("render loop failed, stopping redraws")
            self._timer.stop()

    # -- scene contents -----------------------------------------------------

    def add_model(self, model_id: str, assembly: CadAssembly, cache_dir: Path) -> int:
        """Add a model. Returns the number of nodes with a missing mesh."""
        if self._renderer is None:
            logger.warning("viewport not ready, model not shown", model=model_id)
            return len(assembly.nodes)
        return int(self._renderer.add_model(model_id, assembly, cache_dir))

    def remove_model(self, model_id: str) -> None:
        if self._renderer is not None:
            self._renderer.remove_model(model_id)

    def set_view(self, name: str) -> None:
        """Switch to a standard view (`front`, `top`, …)."""
        if self._renderer is None:
            logger.debug("viewport not ready, view unchanged", view=name)
            return
        self._renderer.set_view(name)

    def fit_view(self, model_id: str | None = None) -> None:
        """Frame one model, or everything when `model_id` is `None`."""
        if self._renderer is not None:
            self._renderer.fit_view(model_id)

    @property
    def camera_state(self) -> Any:
        """The orbit camera, or `None` when there is no renderer."""
        return None if self._renderer is None else self._renderer.camera_state

    def set_camera_state(self, camera: Any) -> None:
        """Restore a saved camera."""
        if self._renderer is not None:
            self._renderer.set_camera_state(camera)

    def set_highlight(self, model_id: str | None) -> None:
        """Outline the selected model, or clear the outline."""
        if self._renderer is not None:
            self._renderer.set_highlight(model_id)

    def placement(self, model_id: str) -> Transform:
        """Where a model sits. Identity when there is no renderer."""
        if self._renderer is None:
            return IDENTITY_PLACEMENT
        placement: Transform = self._renderer.placement(model_id)
        return placement

    def set_placement(self, model_id: str, placement: Transform) -> None:
        """Move and rotate one model."""
        if self._renderer is None:
            logger.debug("viewport not ready, placement unchanged", model=model_id)
            return
        self._renderer.set_placement(model_id, placement)

    def clear(self) -> None:
        if self._renderer is not None:
            self._renderer.clear()

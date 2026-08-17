"""3D plocha okna — Qt obal nad `viz.embed.EmbeddedRenderer`.

Zámerne **neimportuje Panda3D**. Všetko, čo o ňom vie, je za `EmbeddedRenderer`;
tento súbor rieši len Qt stránku veci: kedy okno vzniká, kedy sa prekresľuje
a čo sa stane pri zmene veľkosti.

Dve veci, ktoré prekvapia:

- Render loop tiká `QTimer`. `base.run()` by prevzal riadenie a Qt by zamrzlo.
- Myš a klávesnicu dostáva **Panda3D okno**, nie tento widget. Ovládanie kamery
  je preto vo `viz/orbit_control.py`, nie v `mousePressEvent()`.
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

#: ~60 fps. Qt časovač nie je presný, ale na prehliadanie modelu to stačí
#: a nezaťažuje CPU tak ako nepretržitá slučka.
FRAME_INTERVAL_MS: Final = 16


class Panda3DViewport(QWidget):
    """Widget, do ktorého kreslí Panda3D.

    Renderer vzniká až pri prvom zobrazení — skôr `winId()` neexistuje
    a nebolo by sa na čo pripojiť.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._renderer: Any = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

        # Qt do widgetu kresliť nesmie — prekrylo by to obsah Panda3D okna.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(320, 240)

    # -- životný cyklus -----------------------------------------------------

    def showEvent(self, event: Any) -> None:  # noqa: N802 — Qt konvencia
        super().showEvent(event)
        if self._renderer is None:
            self._create_renderer()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 — Qt konvencia
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 — Qt konvencia
        super().resizeEvent(event)
        if self._renderer is not None:
            self._renderer.resize(*self._device_size())

    def shutdown(self) -> None:
        """Zastaví prekresľovanie. Idempotentné."""
        self._timer.stop()
        if self._renderer is not None:
            self._renderer.shutdown()

    def _device_size(self) -> tuple[int, int]:
        """Veľkosť widgetu vo **fyzických** pixeloch.

        Qt počíta v logických pixeloch, natívne okno Panda3D vo fyzických.
        Pri 125 % škálovaní Windows je rozdiel 1,25× a prejaví sa ako čierny
        pás vpravo a dole — Panda3D okno je menšie než plocha, ktorú má vyplniť.
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
            # Bez 3D plochy má appka stále zmysel (menu, výber súboru),
            # takže sa nezhodí — len sa to zaloguje a viewport zostane prázdny.
            logger.exception("3D viewport sa nepodarilo vytvoriť")
            return

        self._timer.start(FRAME_INTERVAL_MS)

    def _step(self) -> None:
        """Jeden snímok. Nikdy nesmie vyhodiť — zastavilo by to časovač."""
        if self._renderer is None:
            return
        try:
            self._renderer.step()
        except Exception:
            logger.exception("chyba v render loope, zastavujem prekresľovanie")
            self._timer.stop()

    # -- scene contents -----------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._renderer is not None

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

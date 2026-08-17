"""Napojenie orbitálnej kamery na myš v Panda3D.

Tenká vrstva: číta tlačidlá a pohyb myši, prepočet nechá na čistých funkciách
z `viz/orbit.py`. Vďaka tomu je tu len to, čo sa bez okna otestovať nedá.

Nahrádza vstavaný trackball (`base.disableMouse()`), ktorý má neintuitívne
ovládanie a nedá sa mu povedať, okolo čoho má orbitovať.
"""

from __future__ import annotations

from typing import Any, Final

from pssim.observability import get_logger
from pssim.viz.orbit import (
    DEFAULT_FOV_DEG,
    DragAction,
    OrbitCamera,
    apply_drag,
    apply_wheel,
    drag_action,
)

logger = get_logger(__name__)

_TASK_NAME: Final = "pssim-orbit"

#: Panda3D názvy tlačidiel myši → číslo, ktoré chápe `orbit.drag_action`.
_BUTTONS: Final[dict[str, int]] = {"mouse1": 1, "mouse2": 2, "mouse3": 3}


class OrbitController:
    """Ovláda kameru myšou: otáčanie, posun, priblíženie.

    Väzby tlačidiel sú v `viz.orbit.drag_action` — tam sa aj menia.
    """

    def __init__(
        self,
        base: Any,
        camera: OrbitCamera | None = None,
        fov_deg: float = DEFAULT_FOV_DEG,
    ) -> None:
        self._base = base
        self._camera = camera if camera is not None else OrbitCamera()
        self._fov_deg = fov_deg
        self._action = DragAction.NONE
        self._last_pixel: tuple[float, float] | None = None
        self._enabled = False

    # -- životný cyklus -----------------------------------------------------

    def enable(self) -> None:
        """Prevezme kameru od vstavaného trackballu a začne počúvať myš."""
        if self._enabled:
            return

        # Bez tohto by trackball prepisoval polohu kamery v každom snímku
        # a naše nastavenie by sa neprejavilo.
        self._base.disableMouse()

        for name, number in _BUTTONS.items():
            self._base.accept(name, self._on_press, [number])
            self._base.accept(f"{name}-up", self._on_release)
            # Shift sa v Panda3D hlási ako samostatná udalosť, nie ako príznak.
            self._base.accept(f"shift-{name}", self._on_press, [number, True])

        self._base.accept("wheel_up", self._on_wheel, [1])
        self._base.accept("wheel_down", self._on_wheel, [-1])

        self._base.taskMgr.add(self._update, _TASK_NAME)
        self._enabled = True
        self.apply()

    def disable(self) -> None:
        if not self._enabled:
            return
        self._base.taskMgr.remove(_TASK_NAME)
        for name in _BUTTONS:
            self._base.ignore(name)
            self._base.ignore(f"{name}-up")
            self._base.ignore(f"shift-{name}")
        self._base.ignore("wheel_up")
        self._base.ignore("wheel_down")
        self._enabled = False

    # -- stav kamery --------------------------------------------------------

    @property
    def camera(self) -> OrbitCamera:
        return self._camera

    @property
    def action(self) -> DragAction:
        """Čo sa práve deje s myšou. Užitočné pre HUD a pre testy."""
        return self._action

    def set_camera(self, camera: OrbitCamera) -> None:
        self._camera = camera
        self.apply()

    def frame(self, node_path: Any, margin: float = 1.3) -> None:
        """Vycentruje kameru na daný podstrom scény.

        Toto je „zobraz mi celý model" po načítaní súboru. Prázdny podstrom
        (chýbajúca geometria) dostane náhradný polomer, aby scéna nezmizla.
        """
        bounds = node_path.getBounds()
        if bounds.isEmpty() or bounds.getRadius() <= 0.0:
            logger.warning("scéna nemá rozmery — chýba geometria?")
            self.set_camera(OrbitCamera.framing((0.0, 0.0, 0.0), 1.0, self._fov_deg, margin))
            return

        center = bounds.getCenter()
        radius = float(bounds.getRadius())
        self.set_camera(
            OrbitCamera.framing((center[0], center[1], center[2]), radius, self._fov_deg, margin)
        )
        logger.info("kamera vycentrovaná", radius_m=round(radius, 4))

    def apply(self) -> None:
        """Prenesie stav kamery do Panda3D."""
        from panda3d.core import LPoint3

        near, far = self._camera.clip_planes()
        lens = self._base.camLens
        if lens is not None:
            lens.setFov(self._fov_deg)
            lens.setNearFar(near, far)

        self._base.camera.setPos(LPoint3(*self._camera.eye))
        self._base.camera.lookAt(LPoint3(*self._camera.target))

    # -- udalosti myši ------------------------------------------------------

    def _on_press(self, button: int, shift: bool = False) -> None:
        self._action = drag_action(button, shift=shift)
        self._last_pixel = self._mouse_pixel()

    def _on_release(self) -> None:
        self._action = DragAction.NONE
        self._last_pixel = None

    def _on_wheel(self, steps: int) -> None:
        self._camera = apply_wheel(self._camera, steps)
        self.apply()

    def _update(self, _task: Any) -> Any:
        """Jeden snímok ťahania. Nikdy nesmie vyhodiť — zabilo by to render loop."""
        from direct.task import Task

        try:
            self._drag_step()
        except Exception:
            logger.exception("chyba v ovládaní kamery, pokračujem")
        return Task.cont

    def _drag_step(self) -> None:
        if self._action is DragAction.NONE:
            return

        current = self._mouse_pixel()
        if current is None:
            # Kurzor opustil okno — ťahanie sa preruší, aby model neuskočil,
            # keď sa myš vráti inde.
            self._last_pixel = None
            return

        if self._last_pixel is None:
            self._last_pixel = current
            return

        delta_x = current[0] - self._last_pixel[0]
        delta_y = current[1] - self._last_pixel[1]
        self._last_pixel = current
        if delta_x == 0.0 and delta_y == 0.0:
            return

        self._camera = apply_drag(
            self._camera,
            self._action,
            delta_x,
            delta_y,
            viewport_height_px=self._viewport_height(),
            fov_deg=self._fov_deg,
        )
        self.apply()

    # -- pomocné ------------------------------------------------------------

    def _mouse_pixel(self) -> tuple[float, float] | None:
        """Poloha kurzora v pixeloch, alebo `None` ak je mimo okna.

        Panda3D dáva normalizované súradnice `-1..1`; prepočet na pixely
        potrebujeme preto, aby tempo otáčania nezáviselo od veľkosti okna.
        """
        watcher = self._base.mouseWatcherNode
        if watcher is None or not watcher.hasMouse():
            return None

        width, height = self._viewport_size()
        return (watcher.getMouseX() * width / 2.0, watcher.getMouseY() * height / 2.0)

    def _viewport_size(self) -> tuple[int, int]:
        window = self._base.win
        if window is None:
            return (1, 1)
        properties = window.getProperties()
        return (max(properties.getXSize(), 1), max(properties.getYSize(), 1))

    def _viewport_height(self) -> int:
        return self._viewport_size()[1]

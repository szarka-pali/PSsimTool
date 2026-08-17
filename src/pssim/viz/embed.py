"""Panda3D renderujúce do cudzieho okna.

Panda3D vie kresliť do okna, ktoré vytvoril niekto iný
(`WindowProperties.setParentWindow`) — vďaka tomu sa dá vložiť do `QWidget`.
Viď docs/architecture.md R9.

Prečo je to v `viz/` a nie v `ui/`: `ui/` nesmie importovať Panda3D. Táto trieda
je hranica — dovnútra Panda3D, von len čísla a `CadAssembly`. `ui/viewport.py`
ju len drží a preposiela jej Qt udalosti.

Render loop **nepatrí Panda3D**: `base.run()` by prevzal riadenie a hostiteľské
GUI by zamrzlo. Volajúci si sám tiká a volá `step()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pssim.cad.model import CadAssembly
from pssim.domain.machine import Transform
from pssim.domain.placement import IDENTITY_PLACEMENT
from pssim.observability import get_logger
from pssim.viz.axes import axis_length_for, make_axes_node, make_highlight_box
from pssim.viz.camera import scene_radius, setup_lights
from pssim.viz.orbit import OrbitCamera
from pssim.viz.orbit_control import OrbitController
from pssim.viz.scene import build_scene
from pssim.viz.transforms import rpy_to_quat

logger = get_logger(__name__)

BACKGROUND: Final = (0.12, 0.13, 0.15, 1.0)


def offscreen_showbase(size: tuple[int, int]) -> Any:
    """Vráti `ShowBase` pre render bez okna.

    Panda3D dovolí **jedinú `ShowBase` na proces** — druhý pokus o vytvorenie
    skončí výnimkou. Pri jednom renderi z CLI to nevadí, ale testy renderujú
    viackrát za sebou, takže sa existujúca inštancia znovupoužije.
    """
    import builtins

    from direct.showbase.ShowBase import ShowBase
    from panda3d.core import loadPrcFileData

    existing = getattr(builtins, "base", None)
    if existing is not None:
        return existing

    loadPrcFileData("", f"window-type offscreen\nwin-size {size[0]} {size[1]}")
    return ShowBase()


class EmbeddedRenderer:
    """Panda3D kresliaci do okna, ktoré patrí niekomu inému.

    `ShowBase` smie v procese existovať **len raz**, takže aj tento renderer
    je efektívne singleton — druhá inštancia by spadla.
    """

    def __init__(
        self,
        parent_handle: int,
        width: int,
        height: int,
        background: tuple[float, float, float, float] = BACKGROUND,
    ) -> None:
        from direct.showbase.ShowBase import ShowBase
        from panda3d.core import WindowProperties, loadPrcFileData

        # `window-type none` odloží vytvorenie okna — otvoriť ho treba až
        # s odkazom na rodiča, inak by Panda3D otvorilo vlastné samostatné okno.
        loadPrcFileData("", "window-type none")
        self._base: Any = ShowBase()

        properties = WindowProperties()
        properties.setParentWindow(parent_handle)
        properties.setOrigin(0, 0)
        properties.setSize(max(width, 1), max(height, 1))
        self._base.openDefaultWindow(props=properties)
        self._base.setBackgroundColor(*background)

        self._models: dict[str, Any] = {}
        """Model id -> its root `NodePath`. Insertion order matters for the tree."""
        self._placements: dict[str, Transform] = {}
        self._axes_root: Any = None
        self._highlight_root: Any = None
        self._highlighted_id: str | None = None
        self._controller = OrbitController(self._base)
        self._controller.enable()
        logger.info("vložený renderer pripravený", size=(width, height))

    # -- životný cyklus -----------------------------------------------------

    def step(self) -> None:
        """Vykreslí jeden snímok."""
        self._base.taskMgr.step()

    def resize(self, width: int, height: int) -> None:
        """Prispôsobí okno novej veľkosti hostiteľského widgetu.

        Origin sa **zámerne nenastavuje**: pri zmene veľkosti ho Windows
        u vloženého okna prepočíta voči obrazovke, nie voči rodičovi, a okno
        by ušlo mimo widget (namerané: origin skočil na -640).
        """
        from panda3d.core import WindowProperties

        if self._base.win is None:
            return
        properties = WindowProperties()
        properties.setOrigin(0, 0)
        properties.setSize(max(width, 1), max(height, 1))
        self._base.win.requestProperties(properties)

    def shutdown(self) -> None:
        """Uvoľní ovládanie. Idempotentné."""
        self._controller.disable()

    # -- scene contents -----------------------------------------------------

    @property
    def controller(self) -> OrbitController:
        return self._controller

    @property
    def model_ids(self) -> tuple[str, ...]:
        """Models currently in the scene, in insertion order."""
        return tuple(self._models)

    def add_model(self, model_id: str, assembly: CadAssembly, cache_dir: Path) -> int:
        """Add a model to the scene under its own root and return the number of
        nodes whose mesh was missing from the cache.

        Adding does **not** move the camera. With several models loaded, jumping
        the view on every insert would fight the user; `fit_view()` is explicit.
        Only the very first model gets framed, so the window is not left staring
        at empty space.
        """
        self.remove_model(model_id)

        built = build_scene(assembly, cache_dir, name=model_id)
        built.root.reparentTo(self._base.render)
        # Lights hang on the model root, not on `render`: on `render` they would
        # accumulate with every model added and wash the picture out.
        setup_lights(built.root)
        self._models[model_id] = built.root
        self._placements.setdefault(model_id, IDENTITY_PLACEMENT)
        self._apply_placement(model_id)

        if len(self._models) == 1:
            self._controller.frame(built.root)
        self._refresh_axes()

        logger.info(
            "model added",
            model=model_id,
            nodes=len(built.node_paths),
            triangles=assembly.triangle_count,
            missing_meshes=built.missing_meshes,
        )
        return built.missing_meshes

    def remove_model(self, model_id: str) -> bool:
        """Remove one model. Returns `True` if it was there."""
        root = self._models.pop(model_id, None)
        self._placements.pop(model_id, None)
        if root is None:
            return False

        if self._highlighted_id == model_id:
            self.set_highlight(None)
        root.removeNode()
        self._refresh_axes()
        logger.info("model removed", model=model_id)
        return True

    def clear(self) -> None:
        """Remove every model, the highlight and the axes."""
        for root in self._models.values():
            root.removeNode()
        self._models.clear()
        self._placements.clear()
        self.set_highlight(None)
        self._remove_axes()

    # -- camera -------------------------------------------------------------

    def set_view(self, name: str) -> None:
        """Switch to a standard view (`front`, `top`, …).

        Zoom and point of interest stay; only the angle changes.
        """
        self._controller.set_camera(self._controller.camera.with_view(name))
        logger.info("view switched", view=name)

    @property
    def camera_state(self) -> OrbitCamera:
        """The current orbit camera, for saving into a project."""
        return self._controller.camera

    def set_camera_state(self, camera: OrbitCamera) -> None:
        """Restore a saved camera."""
        self._controller.set_camera(camera)

    def fit_view(self, model_id: str | None = None) -> None:
        """Frame one model, or everything when `model_id` is `None`."""
        if model_id is not None:
            root = self._models.get(model_id)
            if root is not None:
                self._controller.frame(root)
            return

        if self._models:
            self._controller.frame(self._base.render)

    # -- placement ----------------------------------------------------------

    def placement(self, model_id: str) -> Transform:
        """Where a model sits relative to the scene origin."""
        return self._placements.get(model_id, IDENTITY_PLACEMENT)

    def set_placement(self, model_id: str, placement: Transform) -> None:
        """Move and rotate one model.

        The origin cross does **not** move — it is the reference the models are
        placed against. The camera stays too; `fit_view()` re-aims it.
        """
        self._placements[model_id] = placement
        self._apply_placement(model_id)
        self._refresh_axes()

    def _apply_placement(self, model_id: str) -> None:
        """Push a placement onto a model root.

        Rotation happens about the **model origin**, not its centre of mass —
        that is what people expect from "rotate 90° about Z".
        """
        from panda3d.core import LQuaternion

        root = self._models.get(model_id)
        if root is None:
            return
        placement = self._placements.get(model_id, IDENTITY_PLACEMENT)
        root.setPos(*placement.xyz)
        root.setQuat(LQuaternion(*rpy_to_quat(placement.rpy)))

    # -- selection ----------------------------------------------------------

    @property
    def highlighted_id(self) -> str | None:
        return self._highlighted_id

    def set_highlight(self, model_id: str | None) -> None:
        """Outline one model as selected, or clear the outline with `None`.

        A wireframe box rather than a colour change: models carry their own
        colours from the STEP file, so tinting them would be invisible on some
        and misleading on others.
        """
        if self._highlight_root is not None:
            self._highlight_root.removeNode()
            self._highlight_root = None
        self._highlighted_id = None

        if model_id is None:
            return
        root = self._models.get(model_id)
        if root is None:
            return

        self._highlighted_id = model_id
        box = make_highlight_box(root)
        if box is not None:
            box.reparentTo(root)
            self._highlight_root = box

    # -- axes ---------------------------------------------------------------

    def _refresh_axes(self) -> None:
        """Redraw the origin cross sized to everything currently loaded.

        Recomputed on every change because the cross scales with the scene: a
        second, much larger model would otherwise leave it invisibly small.
        """
        self._remove_axes()
        if not self._models:
            return
        node = make_axes_node(axis_length_for(scene_radius(self._base.render)[1]))
        node.reparentTo(self._base.render)
        self._axes_root = node

    def _remove_axes(self) -> None:
        if self._axes_root is not None:
            self._axes_root.removeNode()
            self._axes_root = None

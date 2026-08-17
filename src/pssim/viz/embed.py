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
from pssim.viz.axes import axis_length_for, make_axes_node
from pssim.viz.camera import scene_radius, setup_lights
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

        self._scene_root: Any = None
        self._axes_root: Any = None
        self._placement: Transform = IDENTITY_PLACEMENT
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

    # -- obsah scény --------------------------------------------------------

    @property
    def controller(self) -> OrbitController:
        return self._controller

    def show_assembly(self, assembly: CadAssembly, cache_dir: Path) -> int:
        """Zobrazí geometriu a vycentruje na ňu kameru.

        Predchádzajúci model sa zahodí. Vracia počet uzlov, ktorých mesh
        v cache chýbal — nula znamená, že sa načítalo všetko.
        """
        self.clear()

        built = build_scene(assembly, cache_dir, name="model")
        built.root.reparentTo(self._base.render)
        # Svetlá visia na koreni modelu, nie na `render` — inak by sa pri každom
        # ďalšom otvorení súboru hromadili a obraz by postupne vybielil.
        setup_lights(built.root)
        self._scene_root = built.root

        # Umiestnenie sa aplikuje PRED rámovaním, aby kamera zamierila tam,
        # kde model naozaj skončí, nie kde bol pred posunutím.
        self._apply_placement()
        self._controller.frame(built.root)
        # Kríž až po vycentrovaní — jeho veľkosť sa odvíja od rozmeru scény.
        self._show_axes(scene_radius(built.root)[1])

        logger.info(
            "model zobrazený",
            nodes=len(built.node_paths),
            triangles=assembly.triangle_count,
            missing_meshes=built.missing_meshes,
        )
        return built.missing_meshes

    def set_view(self, name: str) -> None:
        """Prepne na štandardný pohľad (`front`, `top`, …).

        Priblíženie a bod záujmu zostávajú — mení sa len uhol.
        """
        self._controller.set_camera(self._controller.camera.with_view(name))
        logger.info("pohľad prepnutý", view=name)

    def fit_view(self) -> None:
        """Vycentruje kameru tak, aby bol celý model v zábere."""
        if self._scene_root is None:
            return
        self._controller.frame(self._scene_root)

    # -- umiestnenie modelu -------------------------------------------------

    @property
    def placement(self) -> Transform:
        """Posun a natočenie modelu voči počiatku scény."""
        return self._placement

    def set_placement(self, placement: Transform) -> None:
        """Posadí model na zadané miesto.

        Kríž v počiatku sa **nehýbe** — je to referencia, voči ktorej sa model
        umiestňuje. Kamera tiež zostáva; ak sa má znovu zamerať, je na to
        `fit_view()`.
        """
        self._placement = placement
        self._apply_placement()

    def _apply_placement(self) -> None:
        """Prenesie umiestnenie na koreň modelu.

        Otočenie sa deje okolo **počiatku modelu**, nie okolo jeho ťažiska —
        to je to, čo človek čaká, keď zadáva „otoč o 90° okolo Z".
        """
        from panda3d.core import LQuaternion

        if self._scene_root is None:
            return
        self._scene_root.setPos(*self._placement.xyz)
        self._scene_root.setQuat(LQuaternion(*rpy_to_quat(self._placement.rpy)))

    def clear(self) -> None:
        """Odstráni zobrazený model aj kríž."""
        if self._scene_root is not None:
            self._scene_root.removeNode()
            self._scene_root = None
        if self._axes_root is not None:
            self._axes_root.removeNode()
            self._axes_root = None

    def _show_axes(self, scene_radius_m: float) -> None:
        """Vykreslí kartézsky kríž v počiatku súradníc modelu.

        Visí na `render`, nie na koreni modelu: keby visel na ňom, zdedil by
        jeho farbu z `setColor()` a všetky tri osi by boli rovnaké.
        """
        node = make_axes_node(axis_length_for(scene_radius_m))
        node.reparentTo(self._base.render)
        self._axes_root = node

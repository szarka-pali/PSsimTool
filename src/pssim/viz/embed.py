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
from pssim.observability import get_logger
from pssim.viz.camera import setup_lights
from pssim.viz.orbit_control import OrbitController
from pssim.viz.scene import build_scene

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

        self._controller.frame(built.root)

        logger.info(
            "model zobrazený",
            nodes=len(built.node_paths),
            triangles=assembly.triangle_count,
            missing_meshes=built.missing_meshes,
        )
        return built.missing_meshes

    def clear(self) -> None:
        """Odstráni zobrazený model."""
        if self._scene_root is not None:
            self._scene_root.removeNode()
            self._scene_root = None

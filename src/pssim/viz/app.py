"""Panda3D aplikácia — render loop a mapovanie kĺbov na scénu.

Toto je vlákno A. Číta zo `StateStore`, ktorý plní vlákno B (`io/`). Nikdy tu
nevolaj nič blokujúce ani nič z asyncua.

`viz/` musí fungovať aj bez `ui/` (`pssim run --no-ui`) — na debug a na testy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pssim.cad.model import CadAssembly
from pssim.config.loader import LoadedMachine
from pssim.domain.kinematics import JointPose, joint_pose, rest_pose
from pssim.domain.machine import Transform, Vec3
from pssim.io.base import DataSource, SourceStatus
from pssim.observability import get_logger
from pssim.viz.camera import DEFAULT_VIEW, setup_camera, setup_lights
from pssim.viz.mesh_loader import load_geom_node
from pssim.viz.scene_builder import ScenePlan, plan_scene
from pssim.viz.transforms import Quaternion, axis_angle_to_quat, multiply_quat, rpy_to_quat

logger = get_logger(__name__)


def _offscreen_showbase(size: tuple[int, int]) -> Any:
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


@dataclass(frozen=True, slots=True)
class ViewerConfig:
    """Nastavenia zobrazenia."""

    window_title: str = "PSsimTool"
    show_hud: bool = True
    background: tuple[float, float, float] = (0.12, 0.13, 0.15)


def compute_poses(
    loaded: LoadedMachine,
    values: dict[str, float],
    stale: frozenset[str],
) -> dict[str, JointPose]:
    """Prepočíta polohy všetkých kĺbov zo snapshotu hodnôt.

    Čistá funkcia bez Panda3D — preto sa dá otestovať v `tests/unit/`.

    Kĺb bez hodnoty dostane `rest_pose()`, nie nulu: ak má limity, ktoré nulu
    neobsahujú, nula by diel umiestnila mimo fyzicky možný rozsah.
    Zastaraný signál sa **použije** (posledná známa hodnota) — `stale` slúži
    len na vizuálne označenie, nie na zahodenie dát.
    """
    del stale  # zatiaľ len informatívne, spracuje HUD
    poses: dict[str, JointPose] = {}
    for joint in loaded.machine.joints:
        value = values.get(joint.name)
        poses[joint.name] = rest_pose(joint) if value is None else joint_pose(joint, value)
    return poses


class MachineViewer:
    """Panda3D `ShowBase` obalený tak, aby sa dal spustiť aj vložiť do Qt.

    Trieda je zámerne tenká: všetka logika, ktorá sa dá otestovať bez okna,
    je mimo nej (`compute_poses`, `plan_scene`).
    """

    def __init__(
        self,
        loaded: LoadedMachine,
        assembly: CadAssembly,
        source: DataSource,
        cache_dir: Path,
        config: ViewerConfig | None = None,
    ) -> None:
        self._loaded = loaded
        self._assembly = assembly
        self._source = source
        self._cache_dir = cache_dir
        self._config = config or ViewerConfig()
        self._plan: ScenePlan = plan_scene(loaded.machine, assembly)
        self._base: Any = None
        self._node_paths: dict[str, Any] = {}
        self._base_transforms: dict[str, tuple[Vec3, Quaternion]] = {}
        """Poloha uzla podľa CAD assembly. Pohyb kĺbu sa k nej pripočítava."""
        self._stale_signals: frozenset[str] = frozenset()
        self._scene_root: Any = None
        self._render_delay_s = loaded.source.effective_render_delay_s(
            getattr(source, "revised_interval_ms", None)
        )

        logger.info(
            "plán scény",
            moving=len(self._plan.moving_nodes),
            static=len(self._plan.static_nodes),
            render_delay_s=round(self._render_delay_s, 3),
        )

    def run(self) -> None:
        """Otvorí okno a spustí render loop. Blokuje do zatvorenia okna."""
        from direct.showbase.ShowBase import ShowBase
        from panda3d.core import WindowProperties

        self._base = ShowBase()
        properties = WindowProperties()
        properties.setTitle(self._config.window_title)
        self._base.win.requestProperties(properties)
        self._base.setBackgroundColor(*self._config.background, 1.0)

        root = self.build_scene()
        root.reparentTo(self._base.render)
        # Kamera a svetlá až PO pripojení scény — rámovanie potrebuje jej rozmery.
        setup_lights(root)
        setup_camera(self._base, root)

        self._source.start()
        self._base.taskMgr.add(self._update_task, "pssim-update")

        try:
            self._base.run()
        finally:
            self._source.stop()

    def render_screenshot(
        self,
        output: Path,
        size: tuple[int, int] = (1280, 800),
        view: str = DEFAULT_VIEW,
        values: dict[str, float] | None = None,
    ) -> Path:
        """Vyrenderuje scénu do PNG bez otvorenia okna.

        Slúži na overenie, že je stroj naozaj **vidieť** — polohy uzlov sa dajú
        otestovať headless, ale „prázdne okno" nie. Viď `pssim screenshot`.
        """
        from panda3d.core import Filename

        self._base = _offscreen_showbase(size)
        # Alfa musí byť 1.0: bez nej má pozadie alfa 0 a v PNG vyjde priehľadné,
        # čo väčšina prehliadačov zobrazí ako bielu plochu.
        self._base.setBackgroundColor(*self._config.background, 1.0)

        root = self.build_scene()
        root.reparentTo(self._base.render)
        try:
            # Hodnoty až po postavení scény — predtým ešte NodePathy neexistujú.
            self.apply_values(values or {})
            setup_lights(root)
            setup_camera(self._base, root, view=view)

            # Dva snímky: prvý alokuje buffery, druhý už kreslí hotovú scénu.
            self._base.graphicsEngine.renderFrame()
            self._base.graphicsEngine.renderFrame()

            output.parent.mkdir(parents=True, exist_ok=True)
            self._base.win.saveScreenshot(Filename.fromOsSpecific(str(output)))
        finally:
            # Scéna sa musí odpojiť, inak by sa pri ďalšom renderi v tom istom
            # procese kreslili oba stroje naraz.
            root.removeNode()
        return output

    def build_scene(self) -> Any:
        """Poskladá hierarchiu `NodePath` podľa assembly a vráti jej koreň.

        Zámerne **nepotrebuje okno** ani `ShowBase` — koreň si volajúci pripojí
        kam chce. Vďaka tomu sa dá celá reťaz (cache → scéna → kinematika →
        poloha dielu) otestovať headless; viď `tests/integration/test_viz_scene.py`.

        Statické uzly sa flattenujú, pohyblivé zostávajú samostatné.
        """
        from panda3d.core import NodePath

        root = NodePath("machine")
        self._scene_root = root

        missing_meshes = 0
        # Poradie rodič-pred-potomkom je tu POVINNÉ: `_parent_node_path` hľadá
        # rodiča medzi už vytvorenými uzlami a pri opačnom poradí by ho nenašiel,
        # všetko by skončilo na koreni a diely by sa nehýbali spolu.
        for node in self._assembly.nodes_parents_first:
            parent = self._parent_node_path(node.path, root)
            node_path = parent.attachNewNode(node.name)
            self._apply_transform(node_path, node.transform)
            node_path.setColor(*node.color)
            # Poloha z CAD sa musí zapamätať — pohyb kĺbu sa k nej pripočítava.
            self._base_transforms[node.path] = (
                node.transform.xyz,
                rpy_to_quat(node.transform.rpy),
            )

            if node.mesh is not None:
                geom_node = load_geom_node(self._cache_dir / node.mesh, node.path)
                if geom_node is None:
                    missing_meshes += 1
                else:
                    node_path.attachNewNode(geom_node)

            self._node_paths[node.path] = node_path

        if missing_meshes:
            logger.warning(
                "časť geometrie chýba — spusti `pssim import-step`",
                missing=missing_meshes,
                total=len(self._assembly.nodes),
            )

        # Statická geometria sa spojí do čo najmenšieho počtu Geomov.
        # Pohyblivé uzly sa flattenovať nesmú — prišli by o vlastnú transformáciu.
        for path in self._plan.static_nodes:
            node_path = self._node_paths.get(path)
            if node_path is not None and not node_path.getChildren():
                node_path.flattenStrong()

        return root

    def apply_values(self, values: dict[str, float]) -> None:
        """Nastaví polohy kĺbov podľa zadaných hodnôt.

        Verejné kvôli testom a budúcemu UI (ručné „posuň os"). Za behu to volá
        `_apply_snapshot()` s hodnotami zo `StateStore`.
        """
        from panda3d.core import LQuaternion

        poses = compute_poses(self._loaded, values, frozenset())
        for joint_name, pose in poses.items():
            node_path = self._node_paths.get(self._plan.joint_to_node[joint_name])
            if node_path is None:
                continue

            # Pohyb kĺbu sa pridáva NA VRCH polohy z CAD assembly. Bez toho by
            # diel pri prvej hodnote z PLC skočil do počiatku rodiča.
            base_xyz, base_quat = self._base_transforms[self._plan.joint_to_node[joint_name]]
            node_path.setPos(
                base_xyz[0] + pose.translation[0],
                base_xyz[1] + pose.translation[1],
                base_xyz[2] + pose.translation[2],
            )
            joint_quat = axis_angle_to_quat(pose.rotation_axis, pose.rotation_angle_rad)
            node_path.setQuat(LQuaternion(*multiply_quat(base_quat, joint_quat)))

    def _parent_node_path(self, path: str, root: Any) -> Any:
        parent_path = path.rsplit("/", 1)[0] if "/" in path else None
        if parent_path is None:
            return root
        return self._node_paths.get(parent_path, root)

    @staticmethod
    def _apply_transform(node_path: Any, transform: Transform) -> None:
        """Nastaví pevnú transformáciu uzla.

        Rotácia ide cez kvaternión, nie cez HPR: prevod rpy → HPR by znamenal
        hádať konvenciu poradia osí Panda3D, kým `rpy_to_quat` je overený
        proti rotačnej matici v `tests/unit/viz/test_transforms.py`.
        """
        from panda3d.core import LQuaternion

        node_path.setPos(*transform.xyz)
        node_path.setQuat(LQuaternion(*rpy_to_quat(transform.rpy)))

    def _update_task(self, _task: Any) -> Any:
        """Jeden frame: snapshot → kinematika → scéna.

        Nikdy nesmie vyhodiť výnimku — pád tejto úlohy znamená zamrznutú scénu.
        """
        from direct.task import Task

        try:
            self._apply_snapshot()
        except Exception:
            logger.exception("chyba v update tasku, pokračujem")
        return Task.cont

    def _apply_snapshot(self) -> None:
        store = self._source.store
        # Renderujeme voči času dát, nie voči lokálnym hodinám: pri replay
        # aj pri zaostávajúcom spojení je to jediná zmysluplná referencia.
        latest = store.latest_time()
        if latest is None:
            return

        at_time = max(latest - self._render_delay_s, 0.0)
        self._stale_signals = store.stale_signals(
            time.monotonic(), self._loaded.source.stale_after_s
        )
        self.apply_values(store.sample_all(at_time))

    @property
    def status(self) -> SourceStatus:
        return self._source.status

    @property
    def scene_root(self) -> Any:
        """Koreň scény stroja. `None`, kým nebehalo `build_scene()`."""
        return self._scene_root

    def node_path(self, path: str) -> Any | None:
        """`NodePath` uzla podľa jeho stabilnej cesty, alebo `None`.

        Slúži testom a budúcemu UI (výber dielu v strome → zvýraznenie v scéne).
        """
        return self._node_paths.get(path)

    @property
    def stale_signals(self) -> frozenset[str]:
        """Signály, ktoré prestali chodiť. Scéna ich zobrazuje poslednou známou hodnotou.

        Slúži HUD-u na vizuálne označenie — dáta sa nezahadzujú, len sa označia.
        """
        return self._stale_signals

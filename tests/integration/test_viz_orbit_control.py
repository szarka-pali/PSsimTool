"""Testy napojenia myši na kameru v Panda3D.

Matematiku ovládania pokrýva `tests/unit/viz/test_orbit.py`. Tu ide o to,
či sú udalosti naozaj **zapojené** — či `wheel_up` skutočne dorazí do kamery
a či stlačenie tlačidla prepne správnu akciu. To je časť, ktorú unit test
neoverí, lebo potrebuje živý Panda3D.

Udalosti sa posielajú cez `messenger`, takže netreba skutočnú myš. Okno sa
neotvára — beží to nad offscreen bufferom.

Vyžaduje `uv sync --extra viz`. Spustenie: ``uv run pytest -m viz``
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from pssim.viz.embed import offscreen_showbase
from pssim.viz.orbit import DragAction, OrbitCamera
from pssim.viz.orbit_control import OrbitController

pytestmark = pytest.mark.viz


@pytest.fixture(scope="module")
def base() -> Any:
    """Offscreen `ShowBase`. Panda3D dovolí len jednu na proces, preto modulový scope."""
    return offscreen_showbase((320, 240))


@pytest.fixture
def controller(base: Any) -> Iterator[OrbitController]:
    """Zapnutý ovládač s predvídateľnou počiatočnou kamerou."""
    instance = OrbitController(
        base,
        OrbitCamera(
            target=(0.0, 0.0, 0.0), distance_m=10.0, min_distance_m=0.1, max_distance_m=100.0
        ),
    )
    instance.enable()
    yield instance
    instance.disable()


def send(base: Any, event: str) -> None:
    """Pošle udalosť tak, ako by prišla z myši."""
    base.messenger.send(event)


class TestKoliesko:
    def test_wheel_up_priblizi(self, base: Any, controller: OrbitController) -> None:
        before = controller.camera.distance_m

        send(base, "wheel_up")

        assert controller.camera.distance_m < before

    def test_wheel_down_oddiali(self, base: Any, controller: OrbitController) -> None:
        before = controller.camera.distance_m

        send(base, "wheel_down")

        assert controller.camera.distance_m > before

    def test_koliesko_posunie_kameru_v_scene(self, base: Any, controller: OrbitController) -> None:
        # Nestačí zmeniť model — musí sa to preniesť aj do Panda3D kamery.
        before = base.camera.getPos().length()

        send(base, "wheel_up")

        assert base.camera.getPos().length() < before

    def test_koliesko_nemeni_ciel(self, base: Any, controller: OrbitController) -> None:
        send(base, "wheel_up")

        assert controller.camera.target == (0.0, 0.0, 0.0)


class TestTlacidla:
    def test_stredne_tlacidlo_zapne_otacanie(self, base: Any, controller: OrbitController) -> None:
        send(base, "mouse2")

        assert controller.action is DragAction.ORBIT

    def test_shift_so_strednym_zapne_posun(self, base: Any, controller: OrbitController) -> None:
        send(base, "shift-mouse2")

        assert controller.action is DragAction.PAN

    def test_prave_tlacidlo_zapne_posun(self, base: Any, controller: OrbitController) -> None:
        send(base, "mouse3")

        assert controller.action is DragAction.PAN

    def test_pustenie_ukonci_tahanie(self, base: Any, controller: OrbitController) -> None:
        send(base, "mouse2")

        send(base, "mouse2-up")

        assert controller.action is DragAction.NONE


class TestPrenosDoSceny:
    def test_kamera_stoji_v_spravnej_vzdialenosti(
        self, controller: OrbitController, base: Any
    ) -> None:
        controller.set_camera(
            OrbitCamera(target=(1.0, 2.0, 3.0), distance_m=5.0, azimuth_rad=0.0, elevation_rad=0.0)
        )

        position = base.camera.getPos()

        assert (position[0], position[1], position[2]) == pytest.approx((1.0, -3.0, 3.0), abs=1e-4)

    def test_orezove_roviny_sa_prisposobia_mierke(
        self, controller: OrbitController, base: Any
    ) -> None:
        # Malý model potrebuje malú near rovinu, inak sa celý oreže.
        controller.set_camera(OrbitCamera(target=(0.0, 0.0, 0.0), distance_m=0.2))

        assert base.camLens.getNear() < 0.2

    def test_vypnutie_odpoji_udalosti(self, base: Any, controller: OrbitController) -> None:
        controller.disable()
        before = controller.camera.distance_m

        send(base, "wheel_up")

        assert controller.camera.distance_m == before

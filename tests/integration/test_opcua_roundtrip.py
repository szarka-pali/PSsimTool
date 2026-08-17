"""Integračné testy proti mock OPC UA serveru.

Nikdy proti reálnemu stroju. Spustenie: ``uv run pytest -m integration``
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from pssim.config.binding import JointBinding
from pssim.io.base import SourceStatus
from pssim.io.mock_server import DEFAULT_AXES, MockAxis, run_mock_server
from pssim.io.opcua_source import OpcUaConfig, OpcUaSource

pytestmark = pytest.mark.integration

ENDPOINT = "opc.tcp://127.0.0.1:48400/pssim-test/"
NAMESPACE_INDEX = 2


class MockServerThread:
    """Mock server v samostatnom vlákne, aby test mohol paralelne pripájať klienta."""

    def __init__(self, duration_s: float = 15.0) -> None:
        self._duration_s = duration_s
        self._thread = threading.Thread(target=self._run, name="mock-server", daemon=True)

    def _run(self) -> None:
        asyncio.run(
            run_mock_server(
                ENDPOINT, DEFAULT_AXES, update_interval_s=0.05, duration_s=self._duration_s
            )
        )

    def __enter__(self) -> MockServerThread:
        self._thread.start()
        time.sleep(1.5)  # server potrebuje čas na otvorenie endpointu
        return self

    def __exit__(self, *_: object) -> None:
        self._thread.join(timeout=self._duration_s + 5.0)


def wait_until(predicate: object, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.1)
    return False


class TestMockAxis:
    """Generovanie hodnôt je čistá funkcia — testuje sa bez servera."""

    def test_v_case_nula_je_v_strede(self) -> None:
        axis = MockAxis(name="X", amplitude=100.0, center=50.0)

        assert axis.value_at(0.0) == pytest.approx(50.0)

    def test_v_stvrtine_periody_je_na_maxime(self) -> None:
        axis = MockAxis(name="X", amplitude=100.0, center=0.0, period_s=8.0)

        assert axis.value_at(2.0) == pytest.approx(100.0)

    def test_je_periodicka(self) -> None:
        axis = MockAxis(name="X", amplitude=100.0, period_s=8.0)

        assert axis.value_at(0.0) == pytest.approx(axis.value_at(8.0), abs=1e-9)


class TestSubscription:
    def test_prijme_data_z_mock_servera(self) -> None:
        bindings = (
            JointBinding(
                joint_name="os_x",
                node_id=f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos",
                scale=1e-3,
            ),
        )
        source = OpcUaSource(OpcUaConfig(endpoint=ENDPOINT, bindings=bindings))

        with MockServerThread():
            source.start()
            received = wait_until(lambda: len(source.store) > 0)
            status = source.status
            source.stop()

        assert received, "do 10 s neprišla ani jedna vzorka"
        assert status is SourceStatus.CONNECTED

    def test_hodnoty_su_prevedene_do_metrov(self) -> None:
        # Mock posiela mm (amplitude 1200, center 1250) → v metroch 0.05 až 2.45.
        bindings = (
            JointBinding(
                joint_name="os_x",
                node_id=f"ns={NAMESPACE_INDEX};s=Axes.X.ActPos",
                scale=1e-3,
            ),
        )
        source = OpcUaSource(OpcUaConfig(endpoint=ENDPOINT, bindings=bindings))

        with MockServerThread():
            source.start()
            wait_until(lambda: len(source.store) > 0)
            latest = source.store.latest_time()
            value = source.store.sample("os_x", at_time_s=latest) if latest else None
            source.stop()

        assert value is not None
        assert 0.0 <= value <= 2.6


class TestOdolnost:
    def test_bez_servera_zostane_v_reconnecte_a_nespadne(self) -> None:
        bindings = (JointBinding(joint_name="os_x", node_id="ns=2;s=Nic"),)
        source = OpcUaSource(
            OpcUaConfig(endpoint="opc.tcp://127.0.0.1:1/nikde/", bindings=bindings)
        )

        source.start()
        time.sleep(2.0)
        status = source.status
        source.stop()

        assert status in (SourceStatus.CONNECTING, SourceStatus.DISCONNECTED)

    def test_stop_je_idempotentny(self) -> None:
        bindings = (JointBinding(joint_name="os_x", node_id="ns=2;s=Nic"),)
        source = OpcUaSource(
            OpcUaConfig(endpoint="opc.tcp://127.0.0.1:1/nikde/", bindings=bindings)
        )

        source.start()
        source.stop()
        source.stop()

        assert source.status is SourceStatus.DISCONNECTED

"""OPC UA klient ako `DataSource`.

Beží vo **vlastnom vlákne s vlastným asyncio loopom**. Panda3D task manager
podporuje `async def` tasky, ale awaituje Panda3D futures, nie asyncio —
asyncua v ňom bežať nemôže. Viď docs/architecture.md R4.

Pravidlá pre túto vrstvu sú v `.claude/rules/io-opcua.md`.

**Stav: napísané, neoverené proti reálnemu PLC.** Overené je len to, čo pokrývajú
testy v `tests/integration/` proti `mock_server.py`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from pssim.config.binding import JointBinding, SourceSettings
from pssim.domain.errors import DataSourceError
from pssim.io.base import SourceStatus
from pssim.io.store import StateStore
from pssim.io.timebase import Timebase
from pssim.observability import get_logger

logger = get_logger(__name__)

_RECONNECT_INITIAL_S: Final = 0.5
_RECONNECT_MAX_S: Final = 30.0
_QUEUE_SIZE: Final = 4
"""queuesize > 1: pri zaostávaní dostaneme aj medziľahlé vzorky. S queuesize=1 by
sa pri rýchlom pohybe zachovali len koncové body a interpolácia by skratkovala."""


@dataclass(frozen=True, slots=True)
class OpcUaConfig:
    """Konfigurácia pripojenia.

    `endpoint` sa berie z prostredia alebo z CLI, nie z `machines/*.yaml` —
    ten je verzovaný a adresy PLC zákazníka do repozitára nepatria.
    """

    endpoint: str
    bindings: tuple[JointBinding, ...]
    publishing_interval_ms: int = 50
    session_timeout_ms: int = 10_000


class OpcUaSource:
    """Prívod dát z OPC UA servera cez subscription.

    Implementuje `pssim.io.base.DataSource`.
    """

    def __init__(self, config: OpcUaConfig, store: StateStore | None = None) -> None:
        if not config.endpoint:
            raise DataSourceError("endpoint nesmie byť prázdny")
        if not config.bindings:
            raise DataSourceError("žiadne väzby na signály — nie je čo odoberať")

        self._config = config
        self._store = store if store is not None else StateStore()
        self._status = SourceStatus.DISCONNECTED
        self._timebase = Timebase()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        # Vlákno A signalizuje cez threading.Event, vlákno B čaká na asyncio.Event.
        # Most medzi nimi je `call_soon_threadsafe` v `stop()`.
        self._async_stop: asyncio.Event | None = None
        self._node_to_joint = {b.node_id: b for b in config.bindings}
        self._revised_interval_ms: int | None = None

    # -- DataSource ---------------------------------------------------------

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def store(self) -> StateStore:
        return self._store

    @property
    def revised_interval_ms(self) -> int | None:
        """Interval, ktorý server naozaj priznal. `None`, kým nie je subscription.

        Použi ho na výpočet `render_delay` — nie ten, o ktorý sme žiadali.
        """
        return self._revised_interval_ms

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_thread,
            name="pssim-opcua",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        async_stop = self._async_stop
        if loop is not None and async_stop is not None and loop.is_running():
            loop.call_soon_threadsafe(async_stop.set)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning("opcua vlákno sa nezastavilo do 5 s, pokračujem")
        self._thread = None
        self._status = SourceStatus.DISCONNECTED

    # -- vlákno B -----------------------------------------------------------

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._connect_forever())
        except Exception:
            logger.exception("opcua vlákno spadlo")
            self._status = SourceStatus.DISCONNECTED

    async def _connect_forever(self) -> None:
        """Reconnect s exponenciálnym backoffom.

        Odpadnutie spojenia je normálny stav, nie výnimka. Logujeme prvý pokus
        a potom každý desiaty — inak log zaplní jedna nedostupná PLC.
        """
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        delay = _RECONNECT_INITIAL_S
        attempt = 0

        while not self._stop_event.is_set():
            attempt += 1
            if attempt == 1 or attempt % 10 == 0:
                logger.info("pripájam sa", endpoint=self._config.endpoint, attempt=attempt)

            self._status = SourceStatus.CONNECTING
            try:
                await self._session()
                delay = _RECONNECT_INITIAL_S  # úspešné spojenie resetuje backoff
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt == 1 or attempt % 10 == 0:
                    logger.warning("spojenie zlyhalo", error=str(exc), retry_in_s=delay)

            self._status = SourceStatus.DISCONNECTED
            if await self._wait_for_stop(timeout_s=delay):
                return
            delay = min(delay * 2.0, _RECONNECT_MAX_S)

    async def _wait_for_stop(self, timeout_s: float | None = None) -> bool:
        """Čaká na signál zastavenia. Vracia `True`, ak prišiel, `False` pri timeoute."""
        assert self._async_stop is not None
        if timeout_s is None:
            await self._async_stop.wait()
            return True
        try:
            await asyncio.wait_for(self._async_stop.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return True

    async def _session(self) -> None:
        """Jedna session: pripoj, odober, drž kým nespadne alebo nepríde stop."""
        from asyncua import Client  # ťažký import — až keď je naozaj potrebný

        client = Client(url=self._config.endpoint)
        client.session_timeout = self._config.session_timeout_ms

        async with client:
            subscription = await client.create_subscription(
                period=self._config.publishing_interval_ms,
                handler=_SubscriptionHandler(self),
            )
            nodes = [client.get_node(node_id) for node_id in self._node_to_joint]
            await subscription.subscribe_data_change(nodes, queuesize=_QUEUE_SIZE)

            # Server smie revidovať interval — napr. TwinCAT ho zviaže s task cycle.
            revised = getattr(subscription.parameters, "RequestedPublishingInterval", None)
            self._revised_interval_ms = int(revised) if revised else None

            self._status = SourceStatus.CONNECTED
            logger.info(
                "subscription aktívna",
                signals=len(nodes),
                revised_interval_ms=self._revised_interval_ms,
            )

            try:
                await self._wait_for_stop()
            finally:
                await subscription.delete()

    # -- callback zo subscription ------------------------------------------

    def handle_data_change(self, node_id: str, value: Any, source_time: datetime | None) -> None:
        """Spracuje jednu notifikáciu. **Nikdy nevyhadzuje** — výnimka tu zabije loop.

        Verejné zámerne: je to kontrakt medzi zdrojom a jeho subscription handlerom.
        """
        binding = self._node_to_joint.get(node_id)
        if binding is None:
            return

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            logger.warning(
                "signál nie je numerický, ignorujem",
                node=node_id,
                type=type(value).__name__,
            )
            return

        if source_time is None:
            # Codesys niektorých verzií SourceTimestamp neposiela vôbec.
            internal_time = time.monotonic()
        else:
            internal_time = self._timebase.to_internal(source_time, time.monotonic())

        self._store.put(
            signal=binding.joint_name,
            value=binding.to_internal(float(value)),
            source_time_s=internal_time,
        )


class _SubscriptionHandler:
    """Handler pre asyncua. Metóda `datachange_notification` je asyncua kontrakt."""

    __slots__ = ("_source",)

    def __init__(self, source: OpcUaSource) -> None:
        self._source = source

    def datachange_notification(self, node: Any, val: Any, data: Any) -> None:
        """Volá asyncua z vlákna B. Výnimka odtiaľto by zabila celý loop."""
        try:
            source_time = getattr(
                getattr(getattr(data, "monitored_item", None), "Value", None),
                "SourceTimestamp",
                None,
            )
            self._source.handle_data_change(str(node.nodeid.to_string()), val, source_time)
        except Exception:
            logger.exception("chyba v datachange handleri, pokračujem")


def build_source(
    settings: SourceSettings,
    bindings: tuple[JointBinding, ...],
    *,
    endpoint_override: str | None = None,
    store: StateStore | None = None,
) -> OpcUaSource:
    """Postaví zdroj z konfigurácie stroja.

    `endpoint_override` má prednosť pred `machines/*.yaml` — reálne endpointy
    sa zadávajú na CLI alebo prostredím, nie vo verzovanom súbore.
    """
    return OpcUaSource(
        OpcUaConfig(
            endpoint=endpoint_override or settings.endpoint,
            bindings=bindings,
            publishing_interval_ms=settings.publishing_interval_ms,
        ),
        store=store,
    )

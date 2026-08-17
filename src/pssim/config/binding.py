"""Väzba medzi kĺbom a OPC UA nodom.

Zámerne oddelené od `domain.machine.Joint`: doména o PLC nevie. Toto je jediné
miesto, kde sa raw hodnota z PLC prevádza na interné jednotky.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JointBinding:
    """Mapovanie `joint ↔ OPC UA node` vrátane prevodu jednotiek.

    `node_id` je OPC UA NodeId v textovom tvare (`"ns=2;s=Axes.X.ActPos"`).
    Prevod je vždy `raw * scale + offset` — toto poradie je zafixované,
    zmena by ticho rozbila existujúce `machines/*.yaml`.
    """

    joint_name: str
    node_id: str
    scale: float = 1.0
    offset: float = 0.0

    def to_internal(self, raw_value: float) -> float:
        """Prevedie hodnotu z jednotiek PLC na metre / radiány."""
        return raw_value * self.scale + self.offset


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """Nastavenia pripojenia a časovania zdroja dát.

    `endpoint` je tu prítomný len pre lokálny mock. Reálne endpointy patria
    do prostredia (`PSSIM_OPCUA_ENDPOINT`) — `machines/*.yaml` je verzovaný.
    """

    endpoint: str
    publishing_interval_ms: int = 50
    stale_after_s: float = 1.0
    render_delay_ms: int | None = None
    """Ak `None`, počíta sa ako 2× revidovaný publishing interval. Viď R5."""

    def effective_render_delay_s(self, revised_interval_ms: int | None = None) -> float:
        """Spozdenie vzorkovania v sekundách.

        Ak nie je zadané explicitne, použije sa 2× interval, ktorý server naozaj
        priznal (nie ten, o ktorý sme žiadali). Dvojnásobok preto, aby sa
        interpolovalo medzi dvoma známymi bodmi, nie extrapolovalo.
        """
        if self.render_delay_ms is not None:
            return self.render_delay_ms / 1000.0
        interval = revised_interval_ms or self.publishing_interval_ms
        return 2.0 * interval / 1000.0

"""Pydantic schéma `machines/*.yaml`.

Toto je **wire format** — to, čo píše človek. Runtime model je `domain.machine.Machine`;
preklad medzi nimi robí `loader.py`. Oddelenie je zámerné: schéma sa môže vyvíjať
(nové polia, defaulty, aliasy) bez toho, aby sa menila doména.

Pri nekompatibilnej zmene schémy musí zostať cesta, ako načítať staré súbory.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Vec3Spec = Annotated[tuple[float, float, float], Field(description="trojica (x, y, z)")]


class StrictModel(BaseModel):
    """Základ pre všetky schémy: neznáme polia sú chyba, nie ticho ignorované.

    Preklep v `machines/*.yaml` sa inak prejaví ako „nefunguje to a neviem prečo".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class TessellationSpec(StrictModel):
    """Parametre tesselácie. Vstupujú do cache kľúča."""

    linear_deflection_mm: float = Field(default=0.5, gt=0.0)
    angular_deflection_rad: float = Field(default=0.35, gt=0.0, le=math.pi)


class SourceSpec(StrictModel):
    """Pripojenie k zdroju dát a časovanie."""

    endpoint: str = Field(
        default="opc.tcp://localhost:4840/pssim/",
        description="Len pre lokálny mock. Reálny endpoint patrí do PSSIM_OPCUA_ENDPOINT.",
    )
    publishing_interval_ms: int = Field(default=50, ge=1, le=10_000)
    stale_after_s: float = Field(default=1.0, gt=0.0)
    render_delay_ms: int | None = Field(default=None, ge=0, le=5_000)


class SignalSpec(StrictModel):
    """Väzba kĺbu na OPC UA node vrátane prevodu jednotiek."""

    node: str = Field(min_length=1, description='NodeId, napr. "ns=2;s=Axes.X.ActPos"')
    scale: float = Field(default=1.0, description="raw * scale + offset → metre/radiány")
    offset: float = 0.0

    @field_validator("scale")
    @classmethod
    def _scale_must_not_be_zero(cls, value: float) -> float:
        # Nulový scale znamená, že kĺb sa nikdy nepohne. Vždy je to preklep.
        if value == 0.0:
            raise ValueError("scale nesmie byť 0 — kĺb by sa nikdy nepohol")
        return value


class OriginSpec(StrictModel):
    """Pevný offset kĺbu voči rodičovi. `xyz` v metroch, `rpy` v radiánoch."""

    xyz: Vec3Spec = (0.0, 0.0, 0.0)
    rpy: Vec3Spec = (0.0, 0.0, 0.0)


class JointSpec(StrictModel):
    """Jeden stupeň voľnosti."""

    name: str = Field(min_length=1)
    parent: str = Field(min_length=1, description="stabilná cesta uzla assembly")
    child: str = Field(min_length=1)
    type: Literal["prismatic", "revolute", "fixed"]
    axis: Vec3Spec = (0.0, 0.0, 1.0)
    limits: tuple[float, float] | None = None
    origin: OriginSpec = OriginSpec()
    signal: SignalSpec | None = None

    @field_validator("axis")
    @classmethod
    def _axis_must_be_nonzero(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if all(component == 0.0 for component in value):
            raise ValueError("axis nesmie byť nulový vektor")
        return value


class MachineSpec(StrictModel):
    """Koreň `machines/*.yaml`."""

    machine: str = Field(min_length=1)
    description: str = ""
    step_file: str = Field(min_length=1)
    units: Literal["m", "mm", "um", "in"] = "mm"
    tessellation: TessellationSpec = TessellationSpec()
    source: SourceSpec = SourceSpec()
    joints: tuple[JointSpec, ...] = Field(min_length=1)

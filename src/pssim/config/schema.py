"""The pydantic schema of `machines/*.yaml`.

This is the **wire format** — what a human writes. The runtime model is
`domain.machine.Machine`; `loader.py` translates between them. The separation is
deliberate: the schema may evolve (new fields, defaults, aliases) without the
domain changing.

An incompatible schema change must leave a way to load the old files.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Vec3Spec = Annotated[tuple[float, float, float], Field(description="trojica (x, y, z)")]


class StrictModel(BaseModel):
    """The base for every schema: unknown fields are an error, not silently ignored.

    A typo in `machines/*.yaml` would otherwise show up as "it does not work and I
    do not know why".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class TessellationSpec(StrictModel):
    """Tessellation parameters. They go into the cache key."""

    linear_deflection_mm: float = Field(default=0.5, gt=0.0)
    angular_deflection_rad: float = Field(default=0.35, gt=0.0, le=math.pi)


class SourceSpec(StrictModel):
    """Connection to the data source, and timing."""

    endpoint: str = Field(
        default="opc.tcp://localhost:4840/pssim/",
        description="For a local mock only. A real endpoint belongs in PSSIM_OPCUA_ENDPOINT.",
    )
    publishing_interval_ms: int = Field(default=50, ge=1, le=10_000)
    stale_after_s: float = Field(default=1.0, gt=0.0)
    render_delay_ms: int | None = Field(default=None, ge=0, le=5_000)


class SignalSpec(StrictModel):
    """Binding a joint to an OPC UA node, including the unit conversion."""

    node: str = Field(min_length=1, description='NodeId, napr. "ns=2;s=Axes.X.ActPos"')
    scale: float = Field(default=1.0, description="raw * scale + offset → metres/radians")
    offset: float = 0.0

    @field_validator("scale")
    @classmethod
    def _scale_must_not_be_zero(cls, value: float) -> float:
        # A zero scale means the joint would never move. It is always a typo.
        if value == 0.0:
            raise ValueError("scale must not be 0 — the joint would never move")
        return value


class OriginSpec(StrictModel):
    """A fixed offset of the joint from its parent. `xyz` in metres, `rpy` in radians."""

    xyz: Vec3Spec = (0.0, 0.0, 0.0)
    rpy: Vec3Spec = (0.0, 0.0, 0.0)


class JointSpec(StrictModel):
    """One degree of freedom."""

    name: str = Field(min_length=1)
    parent: str = Field(min_length=1, description="a stable assembly node path")
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
            raise ValueError("axis must not be a zero vector")
        return value


class MachineSpec(StrictModel):
    """The root of `machines/*.yaml`."""

    machine: str = Field(min_length=1)
    description: str = ""
    step_file: str = Field(min_length=1)
    units: Literal["m", "mm", "um", "in"] = "mm"
    tessellation: TessellationSpec = TessellationSpec()
    source: SourceSpec = SourceSpec()
    joints: tuple[JointSpec, ...] = Field(min_length=1)

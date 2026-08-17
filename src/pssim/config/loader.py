"""Načítanie `machines/*.yaml` a preklad do doménového modelu.

Hranica systému. Za týmto modulom sú dáta platné, v metroch a radiánoch,
a osi sú normalizované.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from pssim.config.binding import JointBinding, SourceSettings
from pssim.config.schema import JointSpec, MachineSpec
from pssim.domain.errors import ConfigError
from pssim.domain.machine import Joint, JointType, Machine, Transform, Vec3
from pssim.domain.units import length_scale_to_m


@dataclass(frozen=True, slots=True)
class LoadedMachine:
    """Všetko, čo vzniklo z jedného `machines/*.yaml`.

    `machine` je doménový model (nevie o PLC). `bindings` je mapovanie na OPC UA.
    `step_file` a `tessellation_*` idú do `cad/`.
    """

    machine: Machine
    bindings: tuple[JointBinding, ...]
    source: SourceSettings
    step_file: Path
    units: str
    scale_to_m: float
    linear_deflection_mm: float
    angular_deflection_rad: float
    description: str = ""

    @property
    def bindings_by_joint(self) -> dict[str, JointBinding]:
        return {binding.joint_name: binding for binding in self.bindings}

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(binding.node_id for binding in self.bindings)


def load_machine(path: str | Path, *, project_root: Path | None = None) -> LoadedMachine:
    """Načíta a zvaliduje definíciu stroja.

    `project_root` slúži na rozlíšenie relatívnych ciest v `step_file`; ak nie je
    zadaný, berie sa adresár nadradený adresáru YAML súboru (teda koreň repozitára
    pri štandardnom `machines/x.yaml`).
    """
    yaml_path = Path(path)
    raw = _read_yaml(yaml_path)
    spec = _validate(raw, yaml_path)

    root = project_root if project_root is not None else yaml_path.resolve().parent.parent
    scale_to_m = _length_scale(spec.units, yaml_path)

    joints = tuple(_to_joint(joint_spec, yaml_path) for joint_spec in spec.joints)
    machine = _build_machine(spec.machine, joints)
    bindings = tuple(_to_bindings(spec.joints))

    _check_moving_joints_have_signals(machine, bindings, yaml_path)

    return LoadedMachine(
        machine=machine,
        bindings=bindings,
        source=SourceSettings(
            endpoint=spec.source.endpoint,
            publishing_interval_ms=spec.source.publishing_interval_ms,
            stale_after_s=spec.source.stale_after_s,
            render_delay_ms=spec.source.render_delay_ms,
        ),
        step_file=(root / spec.step_file).resolve(),
        units=spec.units,
        scale_to_m=scale_to_m,
        linear_deflection_mm=spec.tessellation.linear_deflection_mm,
        angular_deflection_rad=spec.tessellation.angular_deflection_rad,
        description=spec.description,
    )


def _read_yaml(yaml_path: Path) -> dict[str, Any]:
    try:
        text = yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{yaml_path}: súbor sa nedá prečítať: {exc}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{yaml_path}: neplatný YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{yaml_path}: koreň musí byť mapovanie, nie {type(raw).__name__}")
    return raw


def _validate(raw: dict[str, Any], yaml_path: Path) -> MachineSpec:
    try:
        return MachineSpec.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"{yaml_path}: neplatná definícia stroja:\n{_format_errors(exc)}"
        ) from exc


def _format_errors(exc: ValidationError) -> str:
    """Pydantic chyby na tvar, ktorý sa dá čítať bez znalosti pydanticu."""
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(koreň)"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)


def _length_scale(units: str, yaml_path: Path) -> float:
    try:
        return length_scale_to_m(units)
    except ValueError as exc:
        raise ConfigError(f"{yaml_path}: {exc}") from exc


def _to_joint(spec: JointSpec, yaml_path: Path) -> Joint:
    try:
        return Joint(
            name=spec.name,
            parent=spec.parent,
            child=spec.child,
            type=JointType(spec.type),
            axis=_normalize(spec.axis),
            limits=spec.limits,
            origin=Transform(xyz=spec.origin.xyz, rpy=spec.origin.rpy),
        )
    except ConfigError as exc:
        raise ConfigError(f"{yaml_path}: kĺb {spec.name!r}: {exc}") from exc


def _normalize(axis: Vec3) -> Vec3:
    """Normalizuje os na jednotkovú dĺžku.

    Doména neznormalizovanú os odmieta (dĺžka by ticho škálovala pohyb), ale
    v YAML je `[0, 0, 1]` aj `[0, 0, 2]` rovnaký zámer. Normalizujeme tu, na hranici.
    """
    length = sum(component * component for component in axis) ** 0.5
    if length == 0.0:
        raise ConfigError("axis nesmie byť nulový vektor")
    return (axis[0] / length, axis[1] / length, axis[2] / length)


def _build_machine(name: str, joints: tuple[Joint, ...]) -> Machine:
    try:
        return Machine(name=name, joints=joints)
    except ConfigError:
        raise
    except Exception as exc:  # pragma: no cover — obrana proti neočakávanému
        raise ConfigError(f"stroj {name!r} sa nedá poskladať: {exc}") from exc


def _to_bindings(specs: tuple[JointSpec, ...]) -> list[JointBinding]:
    return [
        JointBinding(
            joint_name=spec.name,
            node_id=spec.signal.node,
            scale=spec.signal.scale,
            offset=spec.signal.offset,
        )
        for spec in specs
        if spec.signal is not None
    ]


def _check_moving_joints_have_signals(
    machine: Machine,
    bindings: tuple[JointBinding, ...],
    yaml_path: Path,
) -> None:
    """Pohyblivý kĺb bez signálu je vždy nedopatrenie — nikdy by sa nepohol."""
    bound = {binding.joint_name for binding in bindings}
    missing = [joint.name for joint in machine.moving_joints if joint.name not in bound]
    if missing:
        raise ConfigError(
            f"{yaml_path}: pohyblivé kĺby bez signálu: {', '.join(missing)}. "
            f"Doplň `signal:` alebo zmeň typ na `fixed`."
        )

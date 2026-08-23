"""Factories for test data.

The tests do not build models by hand inline — that way there is one place to change
when the schema grows, and the tests stay readable (only what matters to a given test
is visible).
"""

from __future__ import annotations

import math

from pssim.cad.model import CadAssembly, CadNode
from pssim.domain.interpolation import Sample, SignalBuffer
from pssim.domain.machine import Joint, JointType, Machine, Transform, Vec3
from pssim.domain.model_joints import ModelJoint, ModelJointKind
from pssim.domain.sensors import Sensor, SensorKind


def prismatic_joint(
    name: str = "axis_x",
    parent: str = "base",
    child: str = "portal",
    axis: tuple[float, float, float] = (1.0, 0.0, 0.0),
    limits: tuple[float, float] | None = (0.0, 2.5),
    origin: Transform | None = None,
) -> Joint:
    return Joint(
        name=name,
        parent=parent,
        child=child,
        type=JointType.PRISMATIC,
        axis=axis,
        limits=limits,
        origin=origin or Transform(),
    )


def revolute_joint(
    name: str = "axis_c",
    parent: str = "portal",
    child: str = "head",
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0),
    limits: tuple[float, float] | None = (-math.pi, math.pi),
) -> Joint:
    return Joint(
        name=name,
        parent=parent,
        child=child,
        type=JointType.REVOLUTE,
        axis=axis,
        limits=limits,
    )


def fixed_joint(name: str = "cover", parent: str = "base", child: str = "cover") -> Joint:
    return Joint(name=name, parent=parent, child=child, type=JointType.FIXED)


def beam_sensor(
    name: str = "beam-1",
    origin: Vec3 = (0.0, 0.0, 0.0),
    target: Vec3 | None = None,
    direction: Vec3 | None = None,
    range_m: float | None = None,
    kind: SensorKind = SensorKind.BEAM,
    variable: str = "",
) -> Sensor:
    """A ray sensor, from a direction and a range or from a second point.

    A ray is a point plus a direction plus a range now, but "a beam from here to
    there" is still how most tests read most naturally, so `target` is accepted
    and turned into the direction *and* the reach it implies — both, so a test
    that placed something just beyond the far end still finds it out of range.
    """
    if target is not None:
        offset = (target[0] - origin[0], target[1] - origin[1], target[2] - origin[2])
        if direction is None:
            direction = offset
        if range_m is None:
            range_m = math.sqrt(sum(component**2 for component in offset))
    # `is None` rather than `or`: a test asking for a zero direction or a zero
    # range wants exactly that, and `or` would quietly hand back the default.
    return Sensor(
        name=name,
        kind=kind,
        variable=variable,
        origin=origin,
        direction=(1.0, 0.0, 0.0) if direction is None else direction,
        range_m=1.0 if range_m is None else range_m,
    )


def proximity_sensor(
    name: str = "zone-1",
    origin: Vec3 = (0.0, 0.0, 0.0),
    half_extent_m: float = 0.1,
) -> Sensor:
    return Sensor(name=name, kind=SensorKind.PROXIMITY, origin=origin, half_extent_m=half_extent_m)


def axis_joint(
    name: str = "axis-1",
    variable: str = "axis-1",
    origin: Vec3 = (0.0, 0.0, 0.0),
    target: Vec3 | None = None,
    direction: Vec3 | None = None,
    initial_angle_rad: float = 0.0,
    limits: tuple[float, float] | None = None,
    alignment: Transform | None = None,
) -> ModelJoint:
    """An axis, from a direction or from a second point on it.

    An axis is a centre plus a direction now, but "the axis through these two
    points" is still how most tests read most naturally, so `target` is accepted
    and turned into the direction it implies. Passing neither gives `+Z`.
    """
    if direction is None:
        direction = (
            (0.0, 0.0, 1.0)
            if target is None
            else (target[0] - origin[0], target[1] - origin[1], target[2] - origin[2])
        )
    return ModelJoint(
        name=name,
        kind=ModelJointKind.AXIS,
        variable=variable,
        origin=origin,
        direction=direction,
        initial_angle_rad=initial_angle_rad,
        limits=limits,
        alignment=alignment or Transform(),
    )


def trajectory_joint(
    name: str = "trajectory-1",
    variable: str = "trajectory-1",
    origin: Vec3 = (0.0, 0.0, 0.0),
    target: Vec3 = (1.0, 0.0, 0.0),
    limits: tuple[float, float] | None = None,
    alignment: Transform | None = None,
) -> ModelJoint:
    return ModelJoint(
        name=name,
        kind=ModelJointKind.TRAJECTORY,
        variable=variable,
        origin=origin,
        target=target,
        limits=limits,
        alignment=alignment or Transform(),
    )


def machine(*joints: Joint, name: str = "test") -> Machine:
    return Machine(name=name, joints=joints or (prismatic_joint(),))


def buffer_with(*pairs: tuple[float, float], capacity: int = 32) -> SignalBuffer:
    """A buffer filled with `(source_time_s, value)` samples."""
    signal_buffer = SignalBuffer(capacity=capacity)
    for source_time_s, value in pairs:
        signal_buffer.put(Sample(source_time_s=source_time_s, value=value))
    return signal_buffer


def assembly(*paths: str) -> CadAssembly:
    """A flat assembly from a list of paths. Children are derived from the prefixes."""
    nodes = tuple(
        CadNode(
            path=path,
            children=tuple(
                other
                for other in paths
                if other.startswith(f"{path}/") and "/" not in other[len(path) + 1 :]
            ),
        )
        for path in paths
    )
    roots = tuple(path for path in paths if "/" not in path)
    return CadAssembly(nodes=nodes, roots=roots)


MACHINE_YAML = """
machine: test
step_file: models/test.step
units: mm
joints:
  - name: axis_x
    parent: base
    child: portal
    type: prismatic
    axis: [1, 0, 0]
    limits: [0.0, 2.5]
    signal:
      node: "ns=2;s=X"
      scale: 0.001
"""

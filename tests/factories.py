"""Factories for test data.

The tests do not build models by hand inline — that way there is one place to change
when the schema grows, and the tests stay readable (only what matters to a given test
is visible).
"""

from __future__ import annotations

import math

from pssim.cad.model import CadAssembly, CadNode
from pssim.domain.interpolation import Sample, SignalBuffer
from pssim.domain.machine import Joint, JointType, Machine, Transform


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

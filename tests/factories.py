"""Továrne na testovacie dáta.

Testy nevyrábajú modely ručne inline — mení sa tým jedno miesto, keď sa rozšíri
schéma, a testy zostanú čitateľné (vidno len to, čo je pre daný test podstatné).
"""

from __future__ import annotations

import math

from pssim.cad.model import CadAssembly, CadNode
from pssim.domain.interpolation import Sample, SignalBuffer
from pssim.domain.machine import Joint, JointType, Machine, Transform


def prismatic_joint(
    name: str = "os_x",
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
    name: str = "os_c",
    parent: str = "portal",
    child: str = "hlava",
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


def fixed_joint(name: str = "kryt", parent: str = "base", child: str = "kryt") -> Joint:
    return Joint(name=name, parent=parent, child=child, type=JointType.FIXED)


def machine(*joints: Joint, name: str = "test") -> Machine:
    return Machine(name=name, joints=joints or (prismatic_joint(),))


def buffer_with(*pairs: tuple[float, float], capacity: int = 32) -> SignalBuffer:
    """Buffer naplnený vzorkami `(source_time_s, value)`."""
    signal_buffer = SignalBuffer(capacity=capacity)
    for source_time_s, value in pairs:
        signal_buffer.put(Sample(source_time_s=source_time_s, value=value))
    return signal_buffer


def assembly(*paths: str) -> CadAssembly:
    """Plochý assembly zo zoznamu ciest. Deti sa odvodia z prefixov."""
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
  - name: os_x
    parent: base
    child: portal
    type: prismatic
    axis: [1, 0, 0]
    limits: [0.0, 2.5]
    signal:
      node: "ns=2;s=X"
      scale: 0.001
"""

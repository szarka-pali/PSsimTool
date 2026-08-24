"""A simulated OPC UA server — development and tests without a PLC.

Without it nothing can be developed until the hardware is on the desk, and the
integration tests would have nothing to run against. Writing to OPC UA is tested
**exclusively** against this server, never against a real machine.

The server generates values **in PLC units** (mm, degrees), not in internal ones —
otherwise the conversion in `JointBinding` would go untested.

Run it with: ``uv run pssim mock-server``
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Final

from pssim.observability import get_logger

logger = get_logger(__name__)

DEFAULT_ENDPOINT: Final = "opc.tcp://0.0.0.0:4840/pssim/"
DEFAULT_NAMESPACE: Final = "http://pssim.local/mock"


@dataclass(frozen=True, slots=True)
class MockAxis:
    """A simulated axis. `amplitude` and `center` are in PLC units, not in metres."""

    name: str
    amplitude: float
    center: float = 0.0
    period_s: float = 8.0
    phase_rad: float = 0.0

    def value_at(self, t_s: float) -> float:
        """Sinusoidal motion. A pure function — tested without the server."""
        angle = 2.0 * math.pi * t_s / self.period_s + self.phase_rad
        return self.center + self.amplitude * math.sin(angle)


#: Writable nodes the simulation may publish into — a sensor's reading on its way
#: back to the PLC. Separate from the axes because the direction is the opposite
#: one, and because these are the only nodes on any server this project is ever
#: allowed to write to (see `.claude/rules/io-opcua.md`).
DEFAULT_OUTPUTS: Final = ("Sim.Sensor1", "Sim.Sensor2")


#: Axes matching `machines/example.yaml`. Values in mm and in thousandths of a degree,
#: that is, exactly as a servo typically sends them.
DEFAULT_AXES: Final = (
    MockAxis(name="Axes.X.ActPos", amplitude=1200.0, center=1250.0, period_s=8.0),
    MockAxis(name="Axes.Z.ActPos", amplitude=350.0, center=400.0, period_s=5.0, phase_rad=1.2),
    MockAxis(name="Axes.C.ActPos", amplitude=90_000.0, center=0.0, period_s=11.0),
)


async def run_mock_server(
    endpoint: str = DEFAULT_ENDPOINT,
    axes: tuple[MockAxis, ...] = DEFAULT_AXES,
    *,
    update_interval_s: float = 0.05,
    duration_s: float | None = None,
    outputs: tuple[str, ...] = DEFAULT_OUTPUTS,
) -> None:
    """Run the mock server. `duration_s=None` means run until interrupted.

    `duration_s` is used by the integration tests so the server stops itself.

    The axis nodes are read-only, exactly as a servo's actual position is. The
    `outputs` are writable, and are the only nodes anywhere this project writes
    to — the write path is tested here and nowhere else.
    """
    from asyncua import Server, ua  # a heavy import - only when actually needed

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("PSsimTool Mock PLC")
    # No security: this is a local development tool. Never do this on a real server.
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    namespace_index = await server.register_namespace(DEFAULT_NAMESPACE)
    folder = await server.nodes.objects.add_folder(namespace_index, "Axes")

    variables = {}
    node_ids = [f"ns={namespace_index};s={axis.name}" for axis in axes]
    for axis, node_id in zip(axes, node_ids, strict=True):
        variable = await folder.add_variable(
            node_id,
            axis.name,
            axis.value_at(0.0),
            ua.VariantType.Double,
        )
        await variable.set_writable(False)
        variables[axis] = variable

    output_folder = await server.nodes.objects.add_folder(namespace_index, "Sim")
    output_ids = [f"ns={namespace_index};s={name}" for name in outputs]
    for name, node_id in zip(outputs, output_ids, strict=True):
        node = await output_folder.add_variable(node_id, name, 0.0, ua.VariantType.Double)
        await node.set_writable(True)

    logger.info("mock server running", endpoint=endpoint, nodes=node_ids, writable=output_ids)

    async with server:
        elapsed = 0.0
        while duration_s is None or elapsed < duration_s:
            for axis, variable in variables.items():
                await variable.write_value(axis.value_at(elapsed))
            await asyncio.sleep(update_interval_s)
            elapsed += update_interval_s


def main(endpoint: str = DEFAULT_ENDPOINT) -> None:
    """The entry point for `pssim mock-server`."""
    try:
        asyncio.run(run_mock_server(endpoint))
    except KeyboardInterrupt:
        logger.info("mock server stopped")

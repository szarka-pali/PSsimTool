"""Simulovaný OPC UA server — vývoj a testy bez PLC.

Bez tohto sa nedá vyvinúť nič, kým nie je hardware na stole, a integračné testy
by nemali proti čomu bežať. Zápis do OPC UA sa testuje **výhradne** proti tomuto
serveru, nikdy proti reálnemu stroju.

Server generuje hodnoty **v jednotkách PLC** (mm, stupne), nie v interných —
inak by sa neotestoval prevod v `JointBinding`.

Spustenie: ``uv run pssim mock-server``
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
    """Simulovaná os. `amplitude` a `center` sú v jednotkách PLC, nie v metroch."""

    name: str
    amplitude: float
    center: float = 0.0
    period_s: float = 8.0
    phase_rad: float = 0.0

    def value_at(self, t_s: float) -> float:
        """Sínusový pohyb. Čistá funkcia — testuje sa bez servera."""
        angle = 2.0 * math.pi * t_s / self.period_s + self.phase_rad
        return self.center + self.amplitude * math.sin(angle)


#: Osi, ktoré odpovedajú `machines/priklad.yaml`. Hodnoty v mm a v tisícinách stupňa,
#: teda presne tak, ako ich typicky posiela servo.
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
) -> None:
    """Spustí mock server. `duration_s=None` znamená bežať do prerušenia.

    `duration_s` používajú integračné testy, aby sa server sám ukončil.
    """
    from asyncua import Server, ua  # ťažký import — až keď je naozaj potrebný

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("PSsimTool Mock PLC")
    # Bez zabezpečenia: je to lokálny vývojový nástroj. Reálny server takto nikdy.
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

    logger.info("mock server beží", endpoint=endpoint, nodes=node_ids)

    async with server:
        elapsed = 0.0
        while duration_s is None or elapsed < duration_s:
            for axis, variable in variables.items():
                await variable.write_value(axis.value_at(elapsed))
            await asyncio.sleep(update_interval_s)
            elapsed += update_interval_s


def main(endpoint: str = DEFAULT_ENDPOINT) -> None:
    """Vstupný bod pre `pssim mock-server`."""
    try:
        asyncio.run(run_mock_server(endpoint))
    except KeyboardInterrupt:
        logger.info("mock server ukončený")

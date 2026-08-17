"""Konfiguračná vrstva — YAML schéma definície stroja a jej preklad do doménového modelu.

Toto je hranica systému: tu sa validuje vstup a tu (a len tu) sa prevádzajú jednotky
z PLC a CAD na interné metre a radiány. Doména za touto hranicou predpokladá,
že dáta sú platné.
"""

from pssim.config.binding import JointBinding, SourceSettings
from pssim.config.loader import LoadedMachine, load_machine

__all__ = ["JointBinding", "LoadedMachine", "SourceSettings", "load_machine"]

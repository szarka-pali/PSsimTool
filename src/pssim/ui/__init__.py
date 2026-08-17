"""Desktopový shell v PySide6.

Najvyššia vrstva — smie importovať všetko ostatné, ale nič ju importovať nesmie.
Viď docs/architecture.md R9: shell rieši okno, menu a panely, samotný 3D viewport
bude `viz/` vložené do `QWidget`.

`PySide6` je voliteľná závislosť (`uv sync --extra ui`), preto sa importuje
až vnútri funkcií — `pssim --help` ani unit testy ju ťahať nemusia.
"""

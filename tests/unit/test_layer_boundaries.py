"""Vynucuje hranice vrstiev z CLAUDE.md.

Pravidlo „domain/ importuje len stdlib" je ľahké porušiť z pohodlnosti a ťažké
si všimnúť v code review. Preto je to test, nie len veta v dokumentácii.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "pssim"

#: Balíky, ktoré `domain/` nesmie importovať za žiadnych okolností.
FORBIDDEN_IN_DOMAIN = frozenset(
    {"numpy", "pydantic", "yaml", "panda3d", "direct", "asyncua", "OCP", "trimesh", "typer"}
)

#: Vrstvy a to, čo z projektu smú importovať.
ALLOWED_PROJECT_IMPORTS = {
    "domain": frozenset({"domain"}),
    "config": frozenset({"domain", "config"}),
    "io": frozenset({"domain", "config", "io"}),
    "cad": frozenset({"domain", "cad"}),
    "viz": frozenset({"domain", "config", "io", "cad", "viz"}),
}


def python_files(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    """Korene všetkých importov v súbore, vrátane tých vnútri funkcií."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def imported_pssim_layers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    layers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("pssim."):
            parts = node.module.split(".")
            if len(parts) > 1:
                layers.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "pssim" and len(parts) > 1:
                    layers.add(parts[1])
    return layers


@pytest.mark.parametrize("path", python_files("domain"), ids=lambda p: p.name)
def test_domain_importuje_len_stdlib(path: Path) -> None:
    forbidden = imported_roots(path) & FORBIDDEN_IN_DOMAIN

    assert not forbidden, (
        f"{path.relative_to(SRC)} importuje {sorted(forbidden)}. "
        f"domain/ smie importovať výhradne stdlib — viď CLAUDE.md."
    )


@pytest.mark.parametrize("layer", sorted(ALLOWED_PROJECT_IMPORTS))
def test_vrstva_neimportuje_vyssie_vrstvy(layer: str) -> None:
    allowed = ALLOWED_PROJECT_IMPORTS[layer] | {"observability"}
    violations: list[str] = []

    for path in python_files(layer):
        for imported in imported_pssim_layers(path) - allowed:
            violations.append(f"{path.relative_to(SRC)} → pssim.{imported}")

    assert not violations, (
        f"vrstva {layer}/ importuje vyššie vrstvy: {violations}. "
        f"Závislosti idú len dovnútra — viď docs/architecture.md."
    )


def test_panda3d_nie_je_mimo_viz() -> None:
    violations: list[str] = []
    for layer in ("domain", "config", "io", "cad"):
        for path in python_files(layer):
            if {"panda3d", "direct"} & imported_roots(path):
                violations.append(str(path.relative_to(SRC)))

    assert not violations, f"Panda3D patrí len do viz/: {violations}"


def test_ocp_nie_je_mimo_cad() -> None:
    violations: list[str] = []
    for layer in ("domain", "config", "io", "viz"):
        for path in python_files(layer):
            if "OCP" in imported_roots(path):
                violations.append(str(path.relative_to(SRC)))

    assert not violations, f"OpenCASCADE patrí len do cad/: {violations}"

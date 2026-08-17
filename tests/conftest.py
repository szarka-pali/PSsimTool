"""Spoločná konfigurácia testov."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.factories import MACHINE_YAML


@pytest.fixture
def machine_yaml(tmp_path: Path) -> Path:
    """Minimálna platná definícia stroja na disku.

    Leží v `<tmp>/machines/`, aby sa správne odvodil koreň projektu pri
    rozlišovaní relatívnej cesty k `step_file`.
    """
    machines_dir = tmp_path / "machines"
    machines_dir.mkdir()
    path = machines_dir / "test.yaml"
    path.write_text(MACHINE_YAML, encoding="utf-8")
    return path

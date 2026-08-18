"""Configuration shared by the tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.factories import MACHINE_YAML


@pytest.fixture
def machine_yaml(tmp_path: Path) -> Path:
    """A minimal valid machine definition on disk.

    It sits in `<tmp>/machines/` so the project root is derived correctly when the
    relative path to `step_file` is resolved.
    """
    machines_dir = tmp_path / "machines"
    machines_dir.mkdir()
    path = machines_dir / "test.yaml"
    path.write_text(MACHINE_YAML, encoding="utf-8")
    return path

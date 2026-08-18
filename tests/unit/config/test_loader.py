"""Tests of loading a machine definition.

The unit conversions have a class of their own with concrete numbers — they are the
most likely silent bug in the project.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pssim.config.loader import load_machine
from pssim.domain.errors import ConfigError
from pssim.domain.machine import JointType


def write_machine(tmp_path: Path, body: str) -> Path:
    machines_dir = tmp_path / "machines"
    machines_dir.mkdir(exist_ok=True)
    path = machines_dir / "test.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
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


class TestLoading:
    def test_a_valid_definition_loads(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.machine.name == "test"

    def test_the_joints_load(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.machine.joint("axis_x").type is JointType.PRISMATIC

    def test_the_bindings_load(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.node_ids == ("ns=2;s=X",)

    def test_step_file_becomes_an_absolute_path(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.step_file.is_absolute()
        assert loaded.step_file.name == "test.step"


class TestUnits:
    def test_mm_da_scale_0_001(self, machine_yaml: Path) -> None:
        assert load_machine(machine_yaml).scale_to_m == pytest.approx(1e-3)

    def test_metre_daju_scale_1(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("units: mm", "units: m"))

        assert load_machine(path).scale_to_m == pytest.approx(1.0)

    def test_the_binding_converts_mm_to_metres(self, machine_yaml: Path) -> None:
        binding = load_machine(machine_yaml).bindings[0]

        assert binding.to_internal(1500.0) == pytest.approx(1.5)

    def test_the_binding_applies_the_offset_after_the_scale(self, tmp_path: Path) -> None:
        # The order is fixed: raw * scale + offset. Changing it would silently break YAML.
        body = BASE.replace("scale: 0.001", "scale: 0.001\n      offset: 1.25")
        path = write_machine(tmp_path, body)

        binding = load_machine(path).bindings[0]

        assert binding.to_internal(1000.0) == pytest.approx(2.25)

    def test_an_unknown_unit_is_an_error(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("units: mm", "units: stopa"))

        with pytest.raises(ConfigError):
            load_machine(path)


class TestAxisNormalisation:
    def test_an_unnormalised_axis_is_normalised(self, tmp_path: Path) -> None:
        # In YAML, [0,0,1] and [0,0,2] are the same intent — the domain rejects an
        # unnormalised axis, so it is normalised here, at the boundary.
        path = write_machine(tmp_path, BASE.replace("axis: [1, 0, 0]", "axis: [0, 0, 5]"))

        axis = load_machine(path).machine.joint("axis_x").axis

        assert axis == pytest.approx((0.0, 0.0, 1.0))

    def test_a_diagonal_axis_is_normalised(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("axis: [1, 0, 0]", "axis: [3, 4, 0]"))

        axis = load_machine(path).machine.joint("axis_x").axis

        assert axis == pytest.approx((0.6, 0.8, 0.0))

    def test_a_zero_axis_is_an_error(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("axis: [1, 0, 0]", "axis: [0, 0, 0]"))

        with pytest.raises(ConfigError):
            load_machine(path)


class TestValidation:
    def test_an_unknown_key_is_an_error(self, tmp_path: Path) -> None:
        # A typo would otherwise show up as "it does not work and I do not know why".
        path = write_machine(tmp_path, BASE + "\nunknown_key: 1\n")

        with pytest.raises(ConfigError, match="unknown_key"):
            load_machine(path)

    def test_a_moving_joint_without_a_signal_is_an_error(self, tmp_path: Path) -> None:
        body = BASE.split("    signal:")[0]
        path = write_machine(tmp_path, body)

        with pytest.raises(ConfigError, match="without a signal: axis_x"):
            load_machine(path)

    def test_a_fixed_joint_needs_no_signal(self, tmp_path: Path) -> None:
        body = (
            BASE
            + """
  - name: cover
    parent: base
    child: cover
    type: fixed
"""
        )
        path = write_machine(tmp_path, body)

        assert len(load_machine(path).machine.joints) == 2

    def test_a_zero_scale_is_an_error(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("scale: 0.001", "scale: 0.0"))

        with pytest.raises(ConfigError, match="scale"):
            load_machine(path)

    def test_invalid_yaml_is_an_error(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, "machine: [nedokoncene\n")

        with pytest.raises(ConfigError, match="invalid YAML"):
            load_machine(path)

    def test_a_missing_file_is_an_error_too(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="cannot be read"):
            load_machine(tmp_path / "nic.yaml")

    def test_the_error_message_names_the_file(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("type: prismatic", "type: nezmysel"))

        with pytest.raises(ConfigError, match="test.yaml"):
            load_machine(path)


class TestRenderDelay:
    def test_the_default_is_twice_the_interval(self, machine_yaml: Path) -> None:
        source = load_machine(machine_yaml).source

        assert source.effective_render_delay_s() == pytest.approx(0.1)

    def test_revidovany_interval_ma_prednost_pred_ziadanym(self, machine_yaml: Path) -> None:
        source = load_machine(machine_yaml).source

        assert source.effective_render_delay_s(revised_interval_ms=200) == pytest.approx(0.4)

    def test_explicitne_zadany_delay_vyhrava(self, tmp_path: Path) -> None:
        body = BASE.replace("joints:", "source:\n  render_delay_ms: 30\n\njoints:")
        path = write_machine(tmp_path, body)

        source = load_machine(path).source

        assert source.effective_render_delay_s(revised_interval_ms=500) == pytest.approx(0.03)


class TestExampleInTheRepository:
    def test_machines_example_yaml_is_valid(self) -> None:
        # The reference file in the repository must stay loadable — it doubles as a
        # regression test against incompatible schema changes.
        path = Path(__file__).resolve().parents[3] / "machines" / "example.yaml"

        loaded = load_machine(path)

        assert len(loaded.machine.moving_joints) == 3

    def test_example_axis_c_converts_thousandths_of_a_degree(self) -> None:
        path = Path(__file__).resolve().parents[3] / "machines" / "example.yaml"
        loaded = load_machine(path)

        binding = loaded.bindings_by_joint["axis_c"]

        # 90 000 thousandths of a degree = 90° = pi/2
        assert binding.to_internal(90_000.0) == pytest.approx(math.pi / 2, rel=1e-9)

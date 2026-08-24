"""Tests of the signal bindings and their unit conversion.

The conversion is the most likely silent bug in this project (R8), so every
direction is pinned with a concrete number rather than a round trip alone.
"""

from __future__ import annotations

import pytest

from pssim.config.binding import (
    BindingDirection,
    JointBinding,
    SignalBinding,
    VariableBinding,
)
from pssim.domain.errors import ConfigError


class TestJointBinding:
    def test_its_signal_is_the_joint_name(self) -> None:
        # A property rather than a renamed field: `joint_name` is what
        # machines/*.yaml already uses.
        binding = JointBinding(joint_name="axis_x", node_id="ns=2;s=X")

        assert binding.signal == "axis_x"

    def test_a_joint_is_always_read(self) -> None:
        # The PLC decides where the machine is; this application displays it.
        binding = JointBinding(joint_name="axis_x", node_id="ns=2;s=X")

        assert binding.direction is BindingDirection.READ

    def test_millimetres_become_metres(self) -> None:
        binding = JointBinding(joint_name="axis_x", node_id="ns=2;s=X", scale=0.001)

        assert binding.to_internal(1250.0) == pytest.approx(1.25, abs=1e-9)

    def test_the_offset_is_added_after_the_scale(self) -> None:
        # `raw * scale + offset`, in that order. Reversing it would silently
        # break every existing machine definition.
        binding = JointBinding(joint_name="axis_x", node_id="ns=2;s=X", scale=0.001, offset=0.5)

        assert binding.to_internal(1000.0) == pytest.approx(1.5, abs=1e-9)

    def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(JointBinding(joint_name="x", node_id="n"), SignalBinding)


class TestVariableBinding:
    def test_its_signal_is_the_variable(self) -> None:
        binding = VariableBinding(variable="X", node_id="ns=2;s=X")

        assert binding.signal == "X"

    def test_it_reads_by_default(self) -> None:
        assert VariableBinding(variable="X", node_id="n").direction is BindingDirection.READ

    def test_it_can_be_an_output(self) -> None:
        binding = VariableBinding(variable="I0.0", node_id="n", direction=BindingDirection.WRITE)

        assert binding.direction is BindingDirection.WRITE

    def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(VariableBinding(variable="X", node_id="n"), SignalBinding)


class TestTheInverse:
    def test_metres_become_millimetres(self) -> None:
        binding = VariableBinding(variable="X", node_id="n", scale=0.001)

        assert binding.to_plc(1.25) == pytest.approx(1250.0, abs=1e-6)

    def test_the_offset_is_removed_before_the_scale(self) -> None:
        binding = VariableBinding(variable="X", node_id="n", scale=0.001, offset=0.5)

        assert binding.to_plc(1.5) == pytest.approx(1000.0, abs=1e-6)

    @pytest.mark.parametrize("raw", [0.0, 1.0, -250.0, 1250.75])
    def test_a_value_that_goes_out_and_comes_back_is_itself(self, raw: float) -> None:
        binding = VariableBinding(variable="X", node_id="n", scale=0.001, offset=-0.25)

        assert binding.to_plc(binding.to_internal(raw)) == pytest.approx(raw, abs=1e-6)

    def test_a_zero_scale_cannot_be_written(self) -> None:
        # It maps everything onto the offset: a usable read, an impossible write.
        binding = VariableBinding(variable="X", node_id="n", scale=0.0)

        with pytest.raises(ConfigError):
            binding.to_plc(1.0)

    def test_a_zero_scale_still_reads(self) -> None:
        # Refused at the write rather than at construction, which would turn an
        # existing machine definition into an error.
        binding = VariableBinding(variable="X", node_id="n", scale=0.0, offset=2.0)

        assert binding.to_internal(999.0) == pytest.approx(2.0, abs=1e-9)

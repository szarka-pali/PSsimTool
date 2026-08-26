"""A tag's number is a PLC number with implied decimals, in the joint's own unit.

The complaint this answers: assigning a tag meant working out the whole unit
conversion by hand and typing it as a `scale`. `machines/example.yaml` shows where
that ends up — `scale: 1.7453292519943296e-05`, with a comment explaining it is
`pi/180/1000`.

So a `VariableBinding` now says two things instead:

- `decimals` — how many decimal places an integer value carries. `0` for a
  `REAL`/`FLOAT`, which is then 1:1.
- `unit_scale` — millimetres or degrees into metres or radians, taken from the
  joint's kind rather than typed.

Every conversion here has a concrete number in both directions, which
`.claude/rules/testing.md` asks for by name: this is the most likely silent bug
in the project.
"""

from __future__ import annotations

import math

import pytest

from pssim.config.binding import BindingDirection, VariableBinding
from pssim.domain.units import DEG_TO_RAD, MM_TO_M


def travel(decimals: int = 0, offset: float = 0.0) -> VariableBinding:
    """A trajectory's variable: the PLC's number is millimetres."""
    return VariableBinding(
        variable="rail",
        node_id="ns=2;s=X",
        decimals=decimals,
        offset=offset,
        unit_scale=MM_TO_M,
    )


def rotation(decimals: int = 0, offset: float = 0.0) -> VariableBinding:
    """An axis's variable: the PLC's number is degrees."""
    return VariableBinding(
        variable="head",
        node_id="ns=2;s=C",
        decimals=decimals,
        offset=offset,
        unit_scale=DEG_TO_RAD,
    )


class TestAFloatIsOneToOne:
    """`REAL`/`FLOAT` with no decimals: 354.21 means 354.21 mm or 354.21°."""

    def test_millimetres_arrive_as_millimetres(self) -> None:
        assert travel().to_internal(354.21) == pytest.approx(0.35421)

    def test_degrees_arrive_as_degrees(self) -> None:
        assert rotation().to_internal(354.21) == pytest.approx(354.21 * DEG_TO_RAD)

    def test_a_thousand_millimetres_is_a_metre(self) -> None:
        assert travel().to_internal(1000.0) == pytest.approx(1.0)

    def test_ninety_degrees_is_a_quarter_turn(self) -> None:
        assert rotation().to_internal(90.0) == pytest.approx(math.pi / 2.0)

    def test_zero_is_zero(self) -> None:
        assert travel().to_internal(0.0) == pytest.approx(0.0)

    def test_a_negative_value_travels_backwards(self) -> None:
        assert travel().to_internal(-250.0) == pytest.approx(-0.25)


class TestAnIntegerCarriesDecimals:
    """`INT`/`DINT`: with one decimal place, 652 means 65.2 mm or 65.2°."""

    def test_the_example_from_the_brief(self) -> None:
        assert travel(decimals=1).to_internal(652) == pytest.approx(0.0652)

    def test_and_the_same_for_an_angle(self) -> None:
        assert rotation(decimals=1).to_internal(652) == pytest.approx(65.2 * DEG_TO_RAD)

    def test_two_decimal_places(self) -> None:
        assert travel(decimals=2).to_internal(35421) == pytest.approx(0.35421)

    def test_three_decimal_places(self) -> None:
        # A servo sending micrometres as a DINT.
        assert travel(decimals=3).to_internal(354_210) == pytest.approx(0.35421)

    def test_millidegrees(self) -> None:
        # What `machines/example.yaml` needed `pi/180/1000` for.
        assert rotation(decimals=3).to_internal(90_000) == pytest.approx(math.pi / 2.0)

    def test_no_decimals_divides_by_one(self) -> None:
        assert travel(decimals=0).to_internal(652) == pytest.approx(0.652)


class TestTheOffsetIsInPlcUnits:
    """Millimetres or degrees, not metres or radians: under this model every
    number typed for a tag is in the PLC's own units, and an offset in metres
    would be the inconsistency being removed."""

    def test_it_shifts_by_millimetres(self) -> None:
        assert travel(offset=100.0).to_internal(250.0) == pytest.approx(0.35)

    def test_it_shifts_by_degrees(self) -> None:
        assert rotation(offset=90.0).to_internal(90.0) == pytest.approx(math.pi)

    def test_it_applies_after_the_decimals(self) -> None:
        # 652 -> 65.2, then +10 -> 75.2 mm. Applying it before would give 66.2.
        assert travel(decimals=1, offset=10.0).to_internal(652) == pytest.approx(0.0752)

    def test_a_negative_offset_is_a_zero_point(self) -> None:
        # The encoder reads 100 mm where the machine is at zero.
        assert travel(offset=-100.0).to_internal(100.0) == pytest.approx(0.0)


class TestTheInverse:
    """`to_plc` has to be the exact inverse, or a value that goes out and comes
    back is not the value that went out."""

    def test_metres_become_millimetres(self) -> None:
        assert travel().to_plc(0.35421) == pytest.approx(354.21)

    def test_radians_become_degrees(self) -> None:
        assert rotation().to_plc(math.pi / 2.0) == pytest.approx(90.0)

    def test_the_decimals_are_put_back(self) -> None:
        assert travel(decimals=1).to_plc(0.0652) == pytest.approx(652.0)

    def test_the_offset_is_removed_first(self) -> None:
        assert travel(decimals=1, offset=10.0).to_plc(0.0752) == pytest.approx(652.0)

    @pytest.mark.parametrize("raw", [0.0, 1.0, -250.0, 1250.75, 652.0])
    @pytest.mark.parametrize("decimals", [0, 1, 3])
    def test_a_round_trip_is_the_identity(self, raw: float, decimals: int) -> None:
        binding = travel(decimals=decimals, offset=7.5)

        assert binding.to_plc(binding.to_internal(raw)) == pytest.approx(raw, abs=1e-9)

    @pytest.mark.parametrize("raw", [0.0, 90.0, -45.5, 652.0])
    def test_the_same_for_an_angle(self, raw: float) -> None:
        binding = rotation(decimals=1, offset=-3.0)

        assert binding.to_plc(binding.to_internal(raw)) == pytest.approx(raw, abs=1e-9)


class TestASensorKeepsItsOwnUnits:
    """A sensor's variable travels the other way and has no joint kind to ask
    (R16), so it converts by nothing unless told to."""

    def test_it_passes_a_value_straight_through(self) -> None:
        binding = VariableBinding(variable="gate", node_id="n", direction=BindingDirection.WRITE)

        assert binding.to_internal(1.25) == pytest.approx(1.25)

    def test_and_back(self) -> None:
        binding = VariableBinding(variable="gate", node_id="n", direction=BindingDirection.WRITE)

        assert binding.to_plc(1.25) == pytest.approx(1.25)

    def test_decimals_still_apply_if_given(self) -> None:
        binding = VariableBinding(variable="gate", node_id="n", decimals=2)

        assert binding.to_internal(125) == pytest.approx(1.25)


class TestARefusedConversion:
    def test_a_negative_decimal_count_is_refused(self) -> None:
        # A multiplier rather than a divisor is a different feature, and one
        # nobody asked for; silently accepting it would scale by 10 instead.
        from pssim.domain.errors import ConfigError

        with pytest.raises(ConfigError):
            VariableBinding(variable="X", node_id="n", decimals=-1).to_internal(1.0)

    def test_a_zero_unit_scale_cannot_be_inverted(self) -> None:
        from pssim.domain.errors import ConfigError

        with pytest.raises(ConfigError):
            VariableBinding(variable="X", node_id="n", unit_scale=0.0).to_plc(1.0)

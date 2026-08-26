"""What a sensor's number is in, per kind.

The mirror of `model_joints.value_scale`, and it exists for the same reason: a
sensor's variable needs a unit, and only its kind knows which. The three families
are the ones R16 already names, so this adds no new taxonomy — it puts a
conversion on the one that had none.
"""

from __future__ import annotations

import pytest

from pssim.domain.sensors import (
    DISTANCE_KINDS,
    ENCODER_KINDS,
    PRESENCE_KINDS,
    SensorKind,
    reading_scale,
    reading_unit,
)
from pssim.domain.units import MM_TO_M


class TestTheFamilies:
    def test_every_kind_belongs_to_one(self) -> None:
        # A kind in none of them would convert by accident rather than by rule.
        assert set(SensorKind) == PRESENCE_KINDS | DISTANCE_KINDS | ENCODER_KINDS

    def test_and_to_only_one(self) -> None:
        assert len(PRESENCE_KINDS) + len(DISTANCE_KINDS) + len(ENCODER_KINDS) == len(SensorKind)

    def test_the_zero_one_kinds_are_the_three(self) -> None:
        assert {
            SensorKind.BEAM,
            SensorKind.INDUCTIVE,
            SensorKind.PROXIMITY,
        } == PRESENCE_KINDS


class TestAZeroOneSensor:
    """Already 0 or 1, on the wire and in here."""

    @pytest.mark.parametrize("kind", sorted(PRESENCE_KINDS))
    def test_it_converts_by_nothing(self, kind: SensorKind) -> None:
        assert reading_scale(kind) == pytest.approx(1.0)

    @pytest.mark.parametrize("kind", sorted(PRESENCE_KINDS))
    def test_and_says_so(self, kind: SensorKind) -> None:
        assert reading_unit(kind) == "0/1"


class TestARangefinder:
    """Metres internally, millimetres on the wire — exactly a trajectory."""

    @pytest.mark.parametrize("kind", sorted(DISTANCE_KINDS))
    def test_it_converts_millimetres(self, kind: SensorKind) -> None:
        assert reading_scale(kind) == pytest.approx(MM_TO_M)

    @pytest.mark.parametrize("kind", sorted(DISTANCE_KINDS))
    def test_and_says_so(self, kind: SensorKind) -> None:
        assert reading_unit(kind) == "mm"

    def test_fifty_millimetres_is_a_twentieth_of_a_metre(self) -> None:
        assert 50.0 * reading_scale(SensorKind.TOF) == pytest.approx(0.05)


class TestAnEncoder:
    """Counts both ways (R16), so there is nothing to convert — the count *is*
    the reading."""

    @pytest.mark.parametrize("kind", sorted(ENCODER_KINDS))
    def test_it_converts_by_nothing(self, kind: SensorKind) -> None:
        assert reading_scale(kind) == pytest.approx(1.0)

    @pytest.mark.parametrize("kind", sorted(ENCODER_KINDS))
    def test_and_says_so(self, kind: SensorKind) -> None:
        assert reading_unit(kind) == "counts"


class TestThroughABinding:
    """The conversion a sensor's variable actually gets, end to end."""

    def test_a_rangefinder_writes_millimetres(self) -> None:
        from pssim.config.binding import BindingDirection, VariableBinding

        binding = VariableBinding(
            variable="range",
            node_id="n",
            direction=BindingDirection.WRITE,
            unit_scale=reading_scale(SensorKind.TOF),
        )

        assert binding.to_plc(0.05) == pytest.approx(50.0)

    def test_a_beam_writes_one(self) -> None:
        from pssim.config.binding import BindingDirection, VariableBinding

        binding = VariableBinding(
            variable="gate",
            node_id="n",
            direction=BindingDirection.WRITE,
            unit_scale=reading_scale(SensorKind.BEAM),
        )

        assert binding.to_plc(1.0) == pytest.approx(1.0)

    def test_an_encoder_writes_its_counts(self) -> None:
        from pssim.config.binding import BindingDirection, VariableBinding

        binding = VariableBinding(
            variable="turns",
            node_id="n",
            direction=BindingDirection.WRITE,
            unit_scale=reading_scale(SensorKind.ENCODER_INC),
        )

        assert binding.to_plc(4096.0) == pytest.approx(4096.0)

    def test_a_rangefinder_with_a_decimal_place(self) -> None:
        # A PLC holding tenths of a millimetre as a DINT.
        from pssim.config.binding import BindingDirection, VariableBinding

        binding = VariableBinding(
            variable="range",
            node_id="n",
            direction=BindingDirection.WRITE,
            decimals=1,
            unit_scale=reading_scale(SensorKind.TOF),
        )

        assert binding.to_plc(0.05) == pytest.approx(500.0)

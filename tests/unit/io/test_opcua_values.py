"""Turning a number into what one node will accept.

Pure, and that is the point: the write path stated `Double` for every node, which
a 0/1 sensor bound to a `Boolean` — the type a PLC programmer would naturally use
for one — was refused for. The rounding rules are the half of that fix testable
without a server.
"""

from __future__ import annotations

import pytest

from pssim.io.opcua_values import FALLBACK_TYPE, coerce_for, is_writable_type


class TestABooleanNode:
    def test_one_is_true(self) -> None:
        assert coerce_for(1.0, "Boolean") is True

    def test_zero_is_false(self) -> None:
        assert coerce_for(0.0, "Boolean") is False

    def test_any_reading_at_all_is_a_detection(self) -> None:
        # Which is how `SensorReading.is_active` already reads it (R16).
        assert coerce_for(0.4, "Boolean") is True

    def test_a_negative_reading_too(self) -> None:
        assert coerce_for(-1.0, "Boolean") is True

    def test_it_is_a_bool_not_a_number(self) -> None:
        # `ua.Variant(1.0, VariantType.Boolean)` is not the same thing as
        # `ua.Variant(True, ...)` to every server.
        assert isinstance(coerce_for(1.0, "Boolean"), bool)


class TestAnIntegerNode:
    @pytest.mark.parametrize("type_name", ["SByte", "Byte", "Int16", "Int32", "Int64"])
    def test_it_becomes_an_int(self, type_name: str) -> None:
        assert isinstance(coerce_for(652.0, type_name), int)

    def test_it_is_rounded_not_truncated(self) -> None:
        # `int(0.9)` is `0`, and a sensor 0.9999 of a count along would write a
        # zero. Rounding is what "the nearest" means to a PLC programmer.
        assert coerce_for(0.9, "Int32") == 1

    def test_a_half_goes_the_way_python_rounds(self) -> None:
        # Banker's rounding, stated rather than discovered: 2.5 is 2, not 3.
        assert coerce_for(2.5, "Int32") == 2

    def test_a_negative_number_rounds_too(self) -> None:
        assert coerce_for(-1.6, "Int32") == -2

    def test_an_unsigned_type_is_not_clamped_here(self) -> None:
        # Out of range is the server's to refuse: silently making a negative
        # reading positive would be worse than a rejected write.
        assert coerce_for(-5.0, "UInt16") == -5


class TestAFloatNode:
    @pytest.mark.parametrize("type_name", ["Float", "Double"])
    def test_it_passes_through(self, type_name: str) -> None:
        assert coerce_for(354.21, type_name) == pytest.approx(354.21)

    def test_and_stays_a_float(self) -> None:
        assert isinstance(coerce_for(1, "Double"), float)


class TestAnUnknownType:
    def test_it_is_treated_as_a_float(self) -> None:
        # An unknown type must not change what a configuration that already
        # worked does, and `Double` is what the path did unconditionally.
        assert coerce_for(1.5, "Something") == pytest.approx(1.5)

    def test_the_fallback_is_named(self) -> None:
        assert FALLBACK_TYPE == "Double"

    def test_and_is_itself_writable(self) -> None:
        assert is_writable_type(FALLBACK_TYPE) is True


class TestWhatCanBeWrittenAtAll:
    @pytest.mark.parametrize("type_name", ["Boolean", "Int32", "Float", "Double", "Byte"])
    def test_a_number_can_go_here(self, type_name: str) -> None:
        assert is_writable_type(type_name) is True

    @pytest.mark.parametrize("type_name", ["String", "DateTime", "Guid", "ExtensionObject"])
    def test_a_number_cannot(self, type_name: str) -> None:
        # Perfectly real nodes that nothing here can drive.
        assert is_writable_type(type_name) is False

    def test_an_empty_name_cannot(self) -> None:
        assert is_writable_type("") is False

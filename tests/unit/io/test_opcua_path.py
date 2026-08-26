"""Addressing one field of a struct or one element of an array.

Pure: no server, no asyncua. That is the point of the module — a path is the one
half of this feature that can be tested in milliseconds, and it is also the half
where a mistake binds a joint to the wrong number in silence.

The decoded shapes are stood in for by a dataclass and a list, which is exactly
what asyncua delivers once `load_data_type_definitions` has run.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pssim.domain.errors import DataSourceError
from pssim.io.opcua_path import (
    MAX_DEPTH,
    child_path,
    format_path,
    is_numeric_value,
    parse_path,
    resolve_value,
)


@dataclass
class Point:
    X: float = 1.0  # noqa: N815 - the server's own spelling
    Y: float = 2.0  # noqa: N815
    Z: float = 3.0  # noqa: N815


@dataclass
class AxisState:
    Position: Point  # noqa: N815
    Enabled: bool = True  # noqa: N815
    Name: str = "X"  # noqa: N815
    Limits: tuple[float, ...] = (0.0, 2450.0)  # noqa: N815


def state() -> AxisState:
    return AxisState(Position=Point())


class TestParsingAPath:
    def test_nothing_is_the_empty_path(self) -> None:
        # What every tag written before paths existed means: the whole value.
        assert parse_path("") == ()

    def test_whitespace_is_nothing_too(self) -> None:
        assert parse_path("   ") == ()

    def test_one_field(self) -> None:
        assert parse_path("Position") == ("Position",)

    def test_a_nested_field(self) -> None:
        assert parse_path("Position.X") == ("Position", "X")

    def test_an_index(self) -> None:
        assert parse_path("Limits[1]") == ("Limits", 1)

    def test_an_index_into_a_bare_array(self) -> None:
        # A node that is itself an array has no field name to lead with.
        assert parse_path("[2]") == (2,)

    def test_a_field_after_an_index(self) -> None:
        assert parse_path("Axes[2].Actual") == ("Axes", 2, "Actual")

    def test_two_dimensions(self) -> None:
        assert parse_path("Grid[1][2]") == ("Grid", 1, 2)

    def test_an_underscore_is_a_name(self) -> None:
        assert parse_path("_raw.act_pos") == ("_raw", "act_pos")


class TestARejectedPath:
    """A path comes out of a settings file or a machine definition, both of which
    are outside data. A silent reinterpretation of a typo binds a joint to the
    wrong number."""

    @pytest.mark.parametrize(
        "path",
        [
            "Position..X",
            "1Position",
            "Position.",
            "Limits[]",
            "Limits[a]",
            "Limits[-1]",
            "Position X",
            "Position-X",
            "Position[1",
        ],
    )
    def test_it_is_refused(self, path: str) -> None:
        with pytest.raises(DataSourceError):
            parse_path(path)

    def test_the_message_names_the_whole_path(self) -> None:
        # The segment alone does not say where in the file to look.
        with pytest.raises(DataSourceError, match="Position.1Bad"):
            parse_path("Position.1Bad")

    def test_absurd_nesting_is_refused(self) -> None:
        # A sign of a loop, not of a data structure.
        with pytest.raises(DataSourceError, match="nested deeper"):
            parse_path(".".join(f"F{n}" for n in range(MAX_DEPTH + 2)))


class TestFormattingAPath:
    def test_the_empty_path_is_empty_text(self) -> None:
        assert format_path(()) == ""

    def test_a_field(self) -> None:
        assert format_path(("Position",)) == "Position"

    def test_a_nested_field(self) -> None:
        assert format_path(("Position", "X")) == "Position.X"

    def test_an_index_takes_no_dot(self) -> None:
        assert format_path(("Limits", 1)) == "Limits[1]"

    def test_a_leading_index(self) -> None:
        assert format_path((2,)) == "[2]"

    @pytest.mark.parametrize(
        "path", ["", "Position", "Position.X", "Limits[1]", "[2]", "Axes[2].Actual", "Grid[1][2]"]
    )
    def test_it_round_trips(self, path: str) -> None:
        assert format_path(parse_path(path)) == path


class TestBuildingAChildPath:
    def test_a_field_of_the_whole_value(self) -> None:
        assert child_path("", "Position") == "Position"

    def test_a_field_of_a_field(self) -> None:
        assert child_path("Position", "X") == "Position.X"

    def test_an_element_of_an_array_field(self) -> None:
        assert child_path("Limits", 1) == "Limits[1]"

    def test_an_element_of_a_bare_array(self) -> None:
        assert child_path("", 2) == "[2]"

    def test_a_field_of_an_element(self) -> None:
        assert child_path("Axes[2]", "Actual") == "Axes[2].Actual"


class TestResolvingAValue:
    def test_the_empty_path_is_the_value(self) -> None:
        assert resolve_value(42.0, ()) == pytest.approx(42.0)

    def test_a_field(self) -> None:
        assert resolve_value(state(), ("Enabled",)) is True

    def test_a_nested_field(self) -> None:
        assert resolve_value(state(), ("Position", "X")) == pytest.approx(1.0)

    def test_an_array_element(self) -> None:
        assert resolve_value(state(), ("Limits", 1)) == pytest.approx(2450.0)

    def test_an_element_of_a_bare_list(self) -> None:
        assert resolve_value([10.0, 20.0, 30.0], (2,)) == pytest.approx(30.0)

    def test_a_struct_can_be_resolved_whole(self) -> None:
        assert resolve_value(state(), ("Position",)) == Point()


class TestAPathThatDoesNotFit:
    """The caller marks the signal bad. It must never take the subscription down
    — an exception in a notification handler can end the whole loop."""

    def test_a_missing_field(self) -> None:
        with pytest.raises(DataSourceError, match="no field"):
            resolve_value(state(), ("Velocity",))

    def test_the_message_says_where(self) -> None:
        with pytest.raises(DataSourceError, match="Position.W"):
            resolve_value(state(), ("Position", "W"))

    def test_an_index_past_the_end(self) -> None:
        with pytest.raises(DataSourceError, match="past the end"):
            resolve_value(state(), ("Limits", 9))

    def test_an_index_into_something_that_is_not_an_array(self) -> None:
        with pytest.raises(DataSourceError, match="not an array"):
            resolve_value(state(), ("Position", 0))

    def test_a_field_of_a_plain_number(self) -> None:
        # What a tag with a path bound to a scalar node looks like.
        with pytest.raises(DataSourceError, match="no field"):
            resolve_value(42.0, ("Position",))


class TestWhatCanDriveAJoint:
    def test_a_float_can(self) -> None:
        assert is_numeric_value(1.25) is True

    def test_an_int_can(self) -> None:
        assert is_numeric_value(7) is True

    def test_a_bool_can(self) -> None:
        # A PLC's `Enabled` flag is an ordinary thing to show, and it arrives as
        # one.
        assert is_numeric_value(True) is True

    def test_a_string_cannot(self) -> None:
        assert is_numeric_value("X") is False

    def test_a_struct_cannot(self) -> None:
        assert is_numeric_value(state()) is False

    def test_a_list_cannot(self) -> None:
        # The whole reason an array's parent row cannot be bound.
        assert is_numeric_value([1.0, 2.0]) is False

    def test_nothing_cannot(self) -> None:
        assert is_numeric_value(None) is False

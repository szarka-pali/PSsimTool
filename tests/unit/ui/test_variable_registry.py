"""Tests of `VariableRegistry`.

Pure — no Qt, no server. What matters is that the variable list follows the
scene, that tag assignments survive a scene that has not caught up yet, and that
a dropped connection does not blank a value the viewport is still drawing.
"""

from __future__ import annotations

import pytest

from pssim.config.binding import BindingDirection
from pssim.ui.settings import VariableTag
from pssim.ui.variable_registry import (
    VariableRegistry,
    VariableSource,
    VariableState,
)


def source(
    name: str = "X",
    direction: BindingDirection = BindingDirection.READ,
    owner: str = "axis rail",
) -> VariableSource:
    return VariableSource(name=name, direction=direction, owner=owner)


def registry_with(*sources: VariableSource) -> VariableRegistry:
    registry = VariableRegistry()
    registry.set_sources(sources)
    return registry


class TestTheListFollowsTheScene:
    def test_it_starts_empty(self) -> None:
        assert VariableRegistry().is_empty

    def test_a_source_becomes_an_entry(self) -> None:
        assert registry_with(source("X")).names == ("X",)

    def test_the_order_is_the_order_given(self) -> None:
        registry = registry_with(source("X"), source("C"), source("I0.0"))

        assert registry.names == ("X", "C", "I0.0")

    def test_a_nameless_variable_is_skipped(self) -> None:
        # A joint or a sensor with no variable is normal; it just has no row.
        assert registry_with(source(""), source("X")).names == ("X",)

    def test_a_name_used_twice_is_one_variable(self) -> None:
        # An axis and the sensor watching it may legitimately share a name.
        registry = registry_with(source("X", owner="axis rail"), source("X", owner="sensor gate"))

        assert registry.names == ("X",)

    def test_replacing_the_sources_drops_what_is_gone(self) -> None:
        registry = registry_with(source("X"), source("C"))

        registry.set_sources([source("X")])

        assert registry.names == ("X",)

    def test_an_unchanged_list_reports_no_change(self) -> None:
        registry = registry_with(source("X"))

        assert registry.set_sources([source("X")]) is False

    def test_a_changed_list_reports_a_change(self) -> None:
        registry = registry_with(source("X"))

        assert registry.set_sources([source("X"), source("C")]) is True

    def test_a_value_survives_a_rebuild(self) -> None:
        # A variable must not go blank because a sensor elsewhere was renamed.
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        registry.set_connected(True)
        registry.set_value("X", 1.25)

        registry.set_sources([source("X"), source("C")])

        entry = registry.get("X")
        assert entry is not None
        assert entry.value == pytest.approx(1.25)


class TestTags:
    def test_a_variable_starts_unbound(self) -> None:
        entry = registry_with(source("X")).get("X")

        assert entry is not None
        assert entry.state is VariableState.UNBOUND

    def test_assigning_a_tag_binds_it(self) -> None:
        registry = registry_with(source("X"))

        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})

        entry = registry.get("X")
        assert entry is not None
        assert entry.is_bound

    def test_a_tag_for_an_unknown_variable_is_kept(self) -> None:
        # Settings load before a project does; a tag whose variable has not
        # arrived yet must not be thrown away.
        registry = VariableRegistry()
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})

        registry.set_sources([source("X")])

        entry = registry.get("X")
        assert entry is not None
        assert entry.is_bound

    def test_an_unbound_variable_has_no_binding(self) -> None:
        assert registry_with(source("X")).bindings() == ()

    def test_a_bound_variable_becomes_a_binding(self) -> None:
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X", scale=0.001)})

        binding = registry.bindings()[0]
        assert binding.node_id == "ns=2;s=X"
        assert binding.to_internal(1250.0) == pytest.approx(1.25)

    def test_the_binding_carries_the_direction(self) -> None:
        registry = registry_with(source("I0.0", direction=BindingDirection.WRITE))
        registry.set_tags({"I0.0": VariableTag(node_id="ns=2;s=Sim.Sensor1")})

        assert registry.bindings()[0].direction is BindingDirection.WRITE


class TestState:
    def test_a_bound_variable_offline_reads_offline(self) -> None:
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})

        entry = registry.get("X")
        assert entry is not None
        assert entry.state is VariableState.OFFLINE

    def test_connected_but_silent_reads_waiting(self) -> None:
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})

        registry.set_connected(True)

        entry = registry.get("X")
        assert entry is not None
        assert entry.state is VariableState.WAITING

    def test_a_value_makes_it_live(self) -> None:
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        registry.set_connected(True)

        registry.set_value("X", 1.0)

        entry = registry.get("X")
        assert entry is not None
        assert entry.state is VariableState.LIVE

    def test_an_old_value_reads_stale(self) -> None:
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        registry.set_connected(True)

        registry.set_value("X", 1.0, is_stale=True)

        entry = registry.get("X")
        assert entry is not None
        assert entry.state is VariableState.STALE

    def test_an_unchanged_value_reports_no_change(self) -> None:
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        registry.set_connected(True)
        registry.set_value("X", 1.0)

        assert registry.set_value("X", 1.0) is False

    def test_a_value_for_an_unknown_variable_is_ignored(self) -> None:
        assert VariableRegistry().set_value("nope", 1.0) is False

    def test_disconnecting_keeps_the_value(self) -> None:
        # The scene goes on showing the last known state (R10); a row that
        # blanked would say something different from what the viewport draws.
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        registry.set_connected(True)
        registry.set_value("X", 1.25)

        registry.set_connected(False)

        entry = registry.get("X")
        assert entry is not None
        assert entry.value == pytest.approx(1.25)
        assert entry.state is VariableState.OFFLINE

    def test_an_unbound_variable_stays_unbound_when_connected(self) -> None:
        registry = registry_with(source("X"))

        registry.set_connected(True)

        entry = registry.get("X")
        assert entry is not None
        assert entry.state is VariableState.UNBOUND


class TestClearing:
    def test_it_forgets_the_variables(self) -> None:
        registry = registry_with(source("X"))

        registry.clear()

        assert registry.is_empty

    def test_the_tags_survive(self) -> None:
        # They are settings, not scene content: closing a project does not
        # unassign them.
        registry = registry_with(source("X"))
        registry.set_tags({"X": VariableTag(node_id="ns=2;s=X")})

        registry.clear()
        registry.set_sources([source("X")])

        entry = registry.get("X")
        assert entry is not None
        assert entry.is_bound

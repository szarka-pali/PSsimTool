"""Tests of the settings dataclasses.

Pure: no Qt storage, no filesystem. `SettingsStore` is the part that touches
`QSettings` and is covered in `tests/integration/test_ui_settings_store.py`.

What matters here is that a settings file is **outside data** — anything at all
can be in it — and that a bad entry falls back to a default rather than raising
on startup.
"""

from __future__ import annotations

from pssim.ui.settings import (
    DEFAULT_ENDPOINT,
    DEFAULT_PUBLISHING_INTERVAL_MS,
    ConnectionSettings,
    VariableTag,
    ViewSettings,
)


class TestViewSettings:
    def test_an_unknown_table_has_no_widths(self) -> None:
        assert ViewSettings().widths_for("models") == ()

    def test_widths_are_kept_per_table(self) -> None:
        view = ViewSettings().with_widths("models", (220, 70))

        assert view.widths_for("models") == (220, 70)

    def test_setting_one_table_leaves_the_others(self) -> None:
        view = ViewSettings().with_widths("models", (220, 70)).with_widths("sensors", (150, 110))

        assert view.widths_for("models") == (220, 70)

    def test_the_original_is_not_mutated(self) -> None:
        original = ViewSettings().with_widths("models", (220, 70))

        original.with_widths("models", (400, 90))

        assert original.widths_for("models") == (220, 70)

    def test_it_round_trips(self) -> None:
        view = ViewSettings().with_widths("models", (220, 70)).with_widths("sensors", (150, 110))

        assert ViewSettings.from_dict(view.to_dict()) == view

    def test_nonsense_is_ignored(self) -> None:
        assert ViewSettings.from_dict("not a mapping") == ViewSettings()

    def test_a_zero_width_is_dropped(self) -> None:
        # A collapsed column has no drag handle left, so there would be no way
        # back from applying it.
        assert ViewSettings.from_dict({"models": [220, 0]}).widths_for("models") == ()

    def test_a_non_numeric_width_is_dropped(self) -> None:
        assert ViewSettings.from_dict({"models": [220, "wide"]}).widths_for("models") == ()


class TestVariableTag:
    def test_it_round_trips(self) -> None:
        tag = VariableTag(node_id="ns=2;s=Axes.X.ActPos", scale=0.001, offset=-0.5)

        assert VariableTag.from_dict(tag.to_dict()) == tag

    def test_a_tag_without_a_node_is_not_a_tag(self) -> None:
        assert VariableTag.from_dict({"scale": 2.0}) is None

    def test_an_empty_node_is_not_a_tag(self) -> None:
        assert VariableTag.from_dict({"node_id": ""}) is None

    def test_a_missing_scale_defaults_to_one(self) -> None:
        tag = VariableTag.from_dict({"node_id": "ns=2;s=X"})

        assert tag is not None
        assert tag.scale == 1.0


class TestConnectionSettings:
    def test_it_starts_on_the_mock_endpoint(self) -> None:
        assert ConnectionSettings().endpoint == DEFAULT_ENDPOINT

    def test_writing_is_off_by_default(self) -> None:
        assert ConnectionSettings().allow_writing is False

    def test_a_variable_starts_unbound(self) -> None:
        assert ConnectionSettings().tag_for("X") is None

    def test_a_tag_can_be_assigned(self) -> None:
        settings = ConnectionSettings().with_tag("X", VariableTag(node_id="ns=2;s=X"))

        tag = settings.tag_for("X")
        assert tag is not None
        assert tag.node_id == "ns=2;s=X"

    def test_a_tag_can_be_taken_away(self) -> None:
        settings = ConnectionSettings().with_tag("X", VariableTag(node_id="ns=2;s=X"))

        assert settings.with_tag("X", None).tag_for("X") is None

    def test_it_round_trips(self) -> None:
        settings = ConnectionSettings(
            endpoint="opc.tcp://plc:4840/",
            publishing_interval_ms=100,
            allow_writing=True,
        ).with_tag("X", VariableTag(node_id="ns=2;s=X", scale=0.001))

        assert ConnectionSettings.from_dict(settings.to_dict()) == settings

    def test_nonsense_is_ignored(self) -> None:
        assert ConnectionSettings.from_dict([1, 2, 3]) == ConnectionSettings()

    def test_an_unreadable_tag_does_not_lose_the_others(self) -> None:
        restored = ConnectionSettings.from_dict(
            {"tags": {"X": {"node_id": "ns=2;s=X"}, "Y": {"scale": 2.0}}}
        )

        assert set(restored.tags) == {"X"}

    def test_a_missing_endpoint_falls_back(self) -> None:
        assert ConnectionSettings.from_dict({}).endpoint == DEFAULT_ENDPOINT

    def test_a_silly_interval_falls_back(self) -> None:
        restored = ConnectionSettings.from_dict({"publishing_interval_ms": 0})

        assert restored.publishing_interval_ms == DEFAULT_PUBLISHING_INTERVAL_MS

    def test_only_a_stored_true_turns_writing_on(self) -> None:
        # A corrupted setting must never be what enables writing to a machine.
        for stored in ("yes", 1, "true", None, [1]):
            assert ConnectionSettings.from_dict({"allow_writing": stored}).allow_writing is False

    def test_a_stored_true_does_turn_it_on(self) -> None:
        assert ConnectionSettings.from_dict({"allow_writing": True}).allow_writing is True

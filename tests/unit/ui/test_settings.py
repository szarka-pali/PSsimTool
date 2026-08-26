"""Tests of the settings dataclasses.

Pure: no Qt storage, no filesystem. `SettingsStore` is the part that touches
`QSettings` and is covered in `tests/integration/test_ui_settings_store.py`.

What matters here is that a settings file is **outside data** — anything at all
can be in it — and that a bad entry falls back to a default rather than raising
on startup.
"""

from __future__ import annotations

import pytest

from pssim.io.opcua_security import POLICY_NONE, SecurityMode, TokenType
from pssim.ui.settings import (
    DEFAULT_ENDPOINT,
    DEFAULT_PUBLISHING_INTERVAL_MS,
    PASSWORD_ENV,
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
        tag = VariableTag(node_id="ns=2;s=Axes.X.ActPos", decimals=1, offset=-0.5)

        assert VariableTag.from_dict(tag.to_dict()) == tag

    def test_a_tag_without_a_node_is_not_a_tag(self) -> None:
        assert VariableTag.from_dict({"decimals": 2}) is None

    def test_an_empty_node_is_not_a_tag(self) -> None:
        assert VariableTag.from_dict({"node_id": ""}) is None

    def test_a_missing_decimal_count_is_none_of_them(self) -> None:
        # Which is 1:1 - what a REAL wants, and the safe reading of silence.
        tag = VariableTag.from_dict({"node_id": "ns=2;s=X"})

        assert tag is not None
        assert tag.decimals == 0

    def test_a_stored_scale_is_dropped(self) -> None:
        # It only ever existed to express the unit conversion by hand, which is
        # now automatic. Keeping it would apply the conversion twice and put a
        # model a thousand times off.
        tag = VariableTag.from_dict({"node_id": "ns=2;s=X", "scale": 0.001})

        assert tag is not None
        assert tag.decimals == 0
        assert not hasattr(tag, "scale")

    def test_a_stored_scale_beside_decimals_is_ignored_too(self) -> None:
        tag = VariableTag.from_dict({"node_id": "ns=2;s=X", "scale": 0.001, "decimals": 2})

        assert tag is not None
        assert tag.decimals == 2

    def test_a_negative_decimal_count_is_refused(self) -> None:
        # It would mean multiplying by ten, which is a different feature.
        tag = VariableTag.from_dict({"node_id": "ns=2;s=X", "decimals": -1})

        assert tag is not None
        assert tag.decimals == 0

    def test_an_absurd_decimal_count_is_refused(self) -> None:
        tag = VariableTag.from_dict({"node_id": "ns=2;s=X", "decimals": 40})

        assert tag is not None
        assert tag.decimals == 0

    def test_a_non_integer_decimal_count_is_refused(self) -> None:
        tag = VariableTag.from_dict({"node_id": "ns=2;s=X", "decimals": 1.5})

        assert tag is not None
        assert tag.decimals == 0

    def test_no_decimals_still_writes_the_field(self) -> None:
        # Unlike `path`, this one is always written: `0` is a real answer about
        # the tag rather than the absence of one, and a reader of the file should
        # see that the question was settled.
        assert VariableTag(node_id="ns=2;s=X").to_dict()["decimals"] == 0


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
        ).with_tag("X", VariableTag(node_id="ns=2;s=X", decimals=1))

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


class TestSecuritySettings:
    """What is remembered about how to get in — and the one thing that is not."""

    def test_security_is_off_until_chosen(self) -> None:
        assert ConnectionSettings().security_mode is SecurityMode.NONE

    def test_the_policy_matches_that(self) -> None:
        assert ConnectionSettings().policy_name == POLICY_NONE

    def test_a_session_starts_anonymous(self) -> None:
        assert ConnectionSettings().token_type is TokenType.ANONYMOUS

    def test_security_round_trips(self) -> None:
        settings = ConnectionSettings(
            policy_name="Basic256Sha256",
            security_mode=SecurityMode.SIGN_AND_ENCRYPT,
            token_type=TokenType.USERNAME,
            username="operator",
            certificate_path="C:/pki/own.der",
            key_path="C:/pki/own.pem",
        )

        assert ConnectionSettings.from_dict(settings.to_dict()) == settings

    def test_an_unknown_policy_falls_back_to_none(self) -> None:
        # A settings file is outside data: a policy this build does not implement
        # must not be carried into `set_security`.
        restored = ConnectionSettings.from_dict({"policy_name": "Basic128Rsa15Ex"})

        assert restored.policy_name == POLICY_NONE

    def test_an_unknown_mode_falls_back_to_none(self) -> None:
        assert (
            ConnectionSettings.from_dict({"security_mode": "Encrypted"}).security_mode
            is SecurityMode.NONE
        )

    def test_an_unknown_token_falls_back_to_anonymous(self) -> None:
        assert (
            ConnectionSettings.from_dict({"token_type": "Kerberos"}).token_type
            is TokenType.ANONYMOUS
        )


class TestThePasswordIsNeverStored:
    """R19: the username is remembered, the secret is not. `ui/settings.py` has
    no field for it, so there is nowhere for it to be written by accident."""

    def test_there_is_no_password_field(self) -> None:
        assert not hasattr(ConnectionSettings(), "password")

    def test_it_is_not_in_what_gets_written(self) -> None:
        settings = ConnectionSettings(token_type=TokenType.USERNAME, username="operator")

        assert "password" not in settings.to_dict()

    def test_credentials_take_it_as_an_argument(self) -> None:
        settings = ConnectionSettings(token_type=TokenType.USERNAME, username="operator")

        assert settings.credentials("s3cret").password == "s3cret"

    def test_the_environment_supplies_it_unattended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PASSWORD_ENV, "from-env")

        assert ConnectionSettings().credentials().password == "from-env"

    def test_what_is_typed_wins_over_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PASSWORD_ENV, "from-env")

        assert ConnectionSettings().credentials("typed").password == "typed"

    def test_a_user_token_with_no_password_anywhere_has_to_be_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(PASSWORD_ENV, raising=False)

        assert ConnectionSettings(token_type=TokenType.USERNAME).needs_password is True

    def test_an_anonymous_session_is_not_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PASSWORD_ENV, raising=False)

        assert ConnectionSettings().needs_password is False

    def test_the_environment_answers_for_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PASSWORD_ENV, "from-env")

        assert ConnectionSettings(token_type=TokenType.USERNAME).needs_password is False

    def test_the_description_does_not_carry_it(self) -> None:
        settings = ConnectionSettings(token_type=TokenType.USERNAME, username="operator")

        assert "s3cret" not in settings.describe()
        assert "operator" in settings.describe()

    def test_nor_does_the_repr_of_the_credentials(self) -> None:
        # A frozen dataclass prints every field, and this object ends up in log
        # lines and tracebacks.
        assert "s3cret" not in repr(ConnectionSettings().credentials("s3cret"))

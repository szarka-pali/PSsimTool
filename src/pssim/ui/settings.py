"""Settings that outlive a session but are not part of a scene.

Two kinds live here, and neither belongs in a `*.pssim`:

- **View settings** — how wide the columns of each table are. R7 already decided
  window geometry is not scene content, and a column width is the same kind of
  thing: it is about this user's screen, not about the machine being simulated.
- **Connection settings** — the OPC UA endpoint, the security policy and mode,
  the user name, the publishing interval, whether writing is allowed at all, and
  which tag each of the project's variables reads from. Deliberately outside the
  project file so a `.pssim` carries no addresses and can be handed to anyone.
  The price, which is real: the tag mapping does not travel with the scene, so
  opening a colleague's project means assigning the tags again here.

**There is no password field, anywhere in this module.** That is the enforcement,
not a convention: a value with nowhere to be written cannot be written by
accident. The password is typed once per session, or comes from
`PSSIM_OPCUA_PASSWORD` for an unattended run — `.claude/rules/io-opcua.md` says
credentials come from configuration or the environment, and a QSettings file is
plain-text INI on Windows.

The dataclasses are pure and are what the tests exercise. `SettingsStore` is the
only thing that touches `QSettings`, and it is deliberately thin: settings on
disk are outside data, so every read validates and falls back to a default rather
than trusting what it finds.

Qt is imported **inside** `SettingsStore`, not at module level, so the dataclasses
can be tested in `tests/unit/` — which is required to stay free of a window and to
run in seconds, and importing PySide6 costs more than the whole suite does.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final, TypeGuard

from pssim.config.binding import BindingDirection
from pssim.domain.errors import DataSourceError
from pssim.io.opcua_path import parse_path
from pssim.io.opcua_security import (
    POLICY_NONE,
    Credentials,
    SecurityMode,
    TokenType,
    policy_by_name,
)
from pssim.observability import get_logger

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation only
    from PySide6.QtCore import QSettings

logger = get_logger(__name__)

#: Where `pssim mock-server` listens. A default that connects to something real
#: on a developer's own machine and to nothing on anyone else's.
DEFAULT_ENDPOINT: Final = "opc.tcp://127.0.0.1:4840/pssim/"

DEFAULT_PUBLISHING_INTERVAL_MS: Final = 50

#: Where an unattended run finds the password. The only other way in is the
#: dialog, and neither writes it anywhere.
PASSWORD_ENV: Final = "PSSIM_OPCUA_PASSWORD"

#: The most decimal places a tag may claim. Past this the divisor is larger than
#: anything a PLC integer holds, so a bigger number is a corrupted setting.
MAX_DECIMALS: Final = 9

#: The QSettings keys. Spelled once — a typo in one of the two halves of a
#: read/write pair is silent, and looks exactly like "it did not save".
_VIEW_KEY: Final = "view/columns"
_CONNECTION_KEY: Final = "connection/opcua"


@dataclass(frozen=True, slots=True)
class VariableTag:
    """Which OPC UA node a project variable is bound to, and its unit conversion.

    The conversion is stated the way a PLC programmer knows the number, not as a
    factor to be worked out: **decimal places** and an **offset in the PLC's own
    unit**. Whether that unit is millimetres or degrees comes from the joint the
    variable drives, so the same tag reads `354.21` as a distance on a rail and
    as an angle on a rotary head (see `config.binding.from_plc`).
    """

    node_id: str
    decimals: int = 0
    """How many decimal places an integer value carries. `0` for a `REAL` or a
    `FLOAT`, which is then 1:1: `354.21` is 354.21 mm or 354.21 degrees. `1` makes
    `652` read `65.2`."""

    offset: float = 0.0
    """Added after the decimals, in millimetres or degrees - the same unit the
    value is in, never metres or radians."""

    direction: BindingDirection | None = None
    """Which way this variable travels, where the thing that named it allows a
    choice. `None` means "not chosen" - whatever named it decides.

    A joint's may be either: reading is the PLC saying where the machine is,
    writing is this application saying where its model is, which is what a
    hardware-in-the-loop setup wants. A sensor's is always a write and this is
    ignored for it (R19).

    `None` rather than a default of `READ`, and that is not a nicety: a source
    whose own default is `WRITE` would otherwise be flipped to reading by any tag
    that had never been asked the question - including every tag stored before
    this field existed.
    """
    path: str = ""
    """Where inside the node's value the number is: `Position.X`, `Limits[1]`.

    A **field is not a node** — a server has one node for a struct and nothing
    for a field inside it — so a tag on a field is this node id plus this path.
    Empty means the node's own value, which is what every tag stored before
    structures existed means, so nothing already saved changes meaning.
    """

    def to_dict(self) -> dict[str, Any]:
        stored: dict[str, Any] = {
            "node_id": self.node_id,
            "decimals": self.decimals,
            "offset": self.offset,
        }
        # Written only when it was actually chosen, so a settings file from a
        # scene where nobody touched it reads as it did before.
        if self.direction is not None:
            stored["direction"] = self.direction.value
        # Written only when there is one, so a settings file from a scene with no
        # structures in it looks exactly as it did before paths existed.
        if self.path:
            stored["path"] = self.path
        return stored

    @classmethod
    def from_dict(cls, data: Any) -> VariableTag | None:
        """Rebuild from stored data, or `None` when it is not a usable tag.

        `None` rather than an exception: a settings file is outside data, and a
        single unreadable entry must not stop the rest of them loading.
        """
        if not isinstance(data, dict):
            return None
        node_id = data.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            return None
        if "scale" in data and "decimals" not in data:
            # Written before the conversion was stated as decimal places. That
            # `scale` was the whole unit factor typed out by hand, which is now
            # automatic - keeping it would apply the conversion twice and put a
            # model a thousand times off. Dropped loudly rather than quietly.
            logger.warning(
                "ignoring a variable tag's old scale - check its decimal places",
                node_id=node_id,
                scale=data.get("scale"),
            )
        return cls(
            node_id=node_id,
            decimals=_as_decimals(data.get("decimals")),
            offset=_as_float(data.get("offset"), 0.0),
            path=_as_path(data.get("path")),
            direction=_as_direction(data.get("direction")),
        )


@dataclass(frozen=True, slots=True)
class ViewSettings:
    """How wide each table's columns are, keyed by the table's own name.

    Keyed by name rather than by position so adding a dock later cannot silently
    hand it another table's widths.
    """

    column_widths: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def widths_for(self, table: str) -> tuple[int, ...]:
        """The saved widths for one table, or `()` when it has none yet."""
        return self.column_widths.get(table, ())

    def with_widths(self, table: str, widths: tuple[int, ...]) -> ViewSettings:
        """A copy with one table's widths replaced. The instance is not mutated —
        the same habit `dataclasses.replace` gives everything else here."""
        updated = dict(self.column_widths)
        updated[table] = widths
        return replace(self, column_widths=updated)

    def to_dict(self) -> dict[str, Any]:
        return {table: list(widths) for table, widths in sorted(self.column_widths.items())}

    @classmethod
    def from_dict(cls, data: Any) -> ViewSettings:
        if not isinstance(data, dict):
            return cls()
        widths: dict[str, tuple[int, ...]] = {}
        for table, values in data.items():
            if not isinstance(table, str) or not isinstance(values, list):
                continue
            # A zero or negative width would collapse a column with no way back;
            # a stored one that silly is dropped rather than applied.
            usable = tuple(int(value) for value in values if _is_positive_number(value))
            if len(usable) == len(values):
                widths[table] = usable
        return cls(column_widths=widths)


@dataclass(frozen=True, slots=True)
class ConnectionSettings:
    """Everything needed to reach a server and to know what to read from it."""

    endpoint: str = DEFAULT_ENDPOINT
    publishing_interval_ms: int = DEFAULT_PUBLISHING_INTERVAL_MS

    allow_writing: bool = False
    """Whether the simulation may publish anything back. Off by default and
    stored off unless deliberately turned on: writing to a machine's server is
    the one thing here that can have consequences outside this window.
    See `.claude/rules/io-opcua.md`."""

    policy_name: str = POLICY_NONE
    security_mode: SecurityMode = SecurityMode.NONE
    """How the channel is protected. Both are remembered because a machine does
    not change its mind between sessions, and retyping them every morning is how
    a commissioning engineer ends up leaving security off."""

    token_type: TokenType = TokenType.ANONYMOUS
    username: str = ""
    """Which user, but never the secret. See the module docstring."""

    certificate_path: str = ""
    key_path: str = ""
    """Our own certificate, when one has been supplied rather than generated.
    Empty means "generate a pair and reuse it", which is what happens on first
    run and what UaExpert does too."""

    tags: dict[str, VariableTag] = field(default_factory=dict)
    """Variable name -> the tag it is bound to. A variable with no entry is
    simply unbound, which is a normal state and not an error."""

    def tag_for(self, variable: str) -> VariableTag | None:
        return self.tags.get(variable)

    def with_tag(self, variable: str, tag: VariableTag | None) -> ConnectionSettings:
        """A copy with one variable's tag set, or removed when `tag` is `None`."""
        updated = dict(self.tags)
        if tag is None:
            updated.pop(variable, None)
        else:
            updated[variable] = tag
        return replace(self, tags=updated)

    def credentials(self, password: str = "") -> Credentials:
        """What `io/` needs to open a session, with the secret supplied here.

        The password is a **parameter**, not a field: it arrives from the dialog
        or from the environment at the moment of connecting, and this object —
        which is the thing that gets written to disk — never holds it.
        """
        return Credentials(
            policy_name=self.policy_name,
            mode=self.security_mode,
            token=self.token_type,
            username=self.username,
            password=password or os.environ.get(PASSWORD_ENV, ""),
            certificate_path=self.certificate_path,
            key_path=self.key_path,
        )

    def describe(self) -> str:
        """One line for a status bar: where, and how. Never the password."""
        return f"{self.endpoint} - {self.credentials().describe()}"

    @property
    def needs_password(self) -> bool:
        """Whether connecting will ask for one. A username token with no password
        anywhere is the case the dialog has to prompt for."""
        return self.token_type is TokenType.USERNAME and not os.environ.get(PASSWORD_ENV)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "publishing_interval_ms": self.publishing_interval_ms,
            "allow_writing": self.allow_writing,
            "policy_name": self.policy_name,
            "security_mode": self.security_mode.value,
            "token_type": self.token_type.value,
            "username": self.username,
            "certificate_path": self.certificate_path,
            "key_path": self.key_path,
            "tags": {name: tag.to_dict() for name, tag in sorted(self.tags.items())},
        }

    @classmethod
    def from_dict(cls, data: Any) -> ConnectionSettings:
        if not isinstance(data, dict):
            return cls()
        raw_tags = data.get("tags")
        tags: dict[str, VariableTag] = {}
        if isinstance(raw_tags, dict):
            for name, entry in raw_tags.items():
                tag = VariableTag.from_dict(entry)
                if isinstance(name, str) and name and tag is not None:
                    tags[name] = tag

        endpoint = data.get("endpoint")
        interval = data.get("publishing_interval_ms")
        return cls(
            endpoint=endpoint if isinstance(endpoint, str) and endpoint else DEFAULT_ENDPOINT,
            publishing_interval_ms=(
                int(interval) if _is_positive_number(interval) else DEFAULT_PUBLISHING_INTERVAL_MS
            ),
            # Anything other than a stored `True` reads as off. A corrupted
            # setting must never be what turns writing on.
            allow_writing=data.get("allow_writing") is True,
            policy_name=_as_policy(data.get("policy_name")),
            security_mode=_as_mode(data.get("security_mode")),
            token_type=_as_token(data.get("token_type")),
            username=_as_text(data.get("username"), ""),
            certificate_path=_as_text(data.get("certificate_path"), ""),
            key_path=_as_text(data.get("key_path"), ""),
            tags=tags,
        )


class SettingsStore:
    """Reads and writes the two settings objects. The only user of `QSettings`.

    Stored as JSON under one key each rather than as a tree of native entries:
    the shape is then the same on disk as it is in `to_dict`, which is what the
    tests pin, and a whole section can be replaced atomically.
    """

    __slots__ = ("_settings",)

    def __init__(self, settings: QSettings | None = None) -> None:
        if settings is None:
            from PySide6.QtCore import QSettings as _QSettings

            settings = _QSettings()
        self._settings = settings

    @property
    def settings(self) -> QSettings:
        """The underlying store. Exposed so a test can point it at a temp file."""
        return self._settings

    def load_view(self) -> ViewSettings:
        return ViewSettings.from_dict(self._read(_VIEW_KEY))

    def save_view(self, view: ViewSettings) -> None:
        self._write(_VIEW_KEY, view.to_dict())

    def load_connection(self) -> ConnectionSettings:
        return ConnectionSettings.from_dict(self._read(_CONNECTION_KEY))

    def save_connection(self, connection: ConnectionSettings) -> None:
        self._write(_CONNECTION_KEY, connection.to_dict())

    def _read(self, key: str) -> Any:
        raw = self._settings.value(key)
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Damaged settings are not worth a crash on startup — the defaults
            # are always usable, and the next save overwrites the mess.
            logger.warning("settings entry is not valid JSON, using defaults", key=key)
            return None

    def _write(self, key: str, data: dict[str, Any]) -> None:
        self._settings.setValue(key, json.dumps(data, sort_keys=True))


def _as_text(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _as_policy(value: Any) -> str:
    """A policy this build can actually speak, or none at all.

    A name carried through unchecked reaches `set_security` and fails there, at
    the moment of connecting, with a message about asyncua rather than about the
    settings file it came out of.
    """
    return value if isinstance(value, str) and policy_by_name(value) is not None else POLICY_NONE


def _as_mode(value: Any) -> SecurityMode:
    """A stored mode, or no security. Unrecognised falls back to the safe end —
    an unusable setting must not silently become a stronger claim than it is."""
    for mode in SecurityMode:
        if mode.value == value:
            return mode
    return SecurityMode.NONE


def _as_token(value: Any) -> TokenType:
    for token in TokenType:
        if token.value == value:
            return token
    return TokenType.ANONYMOUS


def _as_path(value: Any) -> str:
    """A path this build can actually walk, or none at all.

    Validated on the way in like every other setting (R18). A malformed path
    would otherwise reach `resolve_value` on the source's thread and be reported
    per notification, at the publishing rate, about a settings file nobody is
    looking at.
    """
    if not isinstance(value, str) or not value:
        return ""
    try:
        parse_path(value)
    except DataSourceError:
        logger.warning("ignoring an unusable variable path", path=value)
        return ""
    return value


def _as_direction(value: Any) -> BindingDirection | None:
    """A stored direction, or nothing.

    Nothing rather than reading: absent means "not chosen", and the thing that
    named the variable then decides. A value this build does not recognise is
    treated the same way - a corrupted setting must never be what starts writing
    to a machine's server, which is the reasoning `allow_writing` follows (R18).
    """
    for direction in BindingDirection:
        if direction.value == value:
            return direction
    return None


def _as_decimals(value: Any) -> int:
    """A count of decimal places, or none.

    Negative is refused rather than clamped: it would mean multiplying by ten,
    which is a different feature and one nobody asked for. An absurd count is
    refused too - past this the divisor exceeds what a PLC integer can hold, so
    it is a corrupted setting rather than a choice.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    if value < 0 or value > MAX_DECIMALS:
        logger.warning("ignoring an unusable decimal count", decimals=value)
        return 0
    return value


def _as_float(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _is_positive_number(value: Any) -> TypeGuard[int | float]:
    """A `TypeGuard` rather than a plain bool so the caller may then convert it —
    otherwise every call site needs a second, identical `isinstance`."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

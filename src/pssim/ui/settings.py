"""Settings that outlive a session but are not part of a scene.

Two kinds live here, and neither belongs in a `*.pssim`:

- **View settings** — how wide the columns of each table are. R7 already decided
  window geometry is not scene content, and a column width is the same kind of
  thing: it is about this user's screen, not about the machine being simulated.
- **Connection settings** — the OPC UA endpoint, the publishing interval, whether
  writing is allowed at all, and which tag each of the project's variables reads
  from. Deliberately outside the project file so a `.pssim` carries no addresses
  and can be handed to anyone. The price, which is real: the tag mapping does not
  travel with the scene, so opening a colleague's project means assigning the
  tags again here.

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
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final, TypeGuard

from pssim.observability import get_logger

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation only
    from PySide6.QtCore import QSettings

logger = get_logger(__name__)

#: Where `pssim mock-server` listens. A default that connects to something real
#: on a developer's own machine and to nothing on anyone else's.
DEFAULT_ENDPOINT: Final = "opc.tcp://127.0.0.1:4840/pssim/"

DEFAULT_PUBLISHING_INTERVAL_MS: Final = 50

#: The QSettings keys. Spelled once — a typo in one of the two halves of a
#: read/write pair is silent, and looks exactly like "it did not save".
_VIEW_KEY: Final = "view/columns"
_CONNECTION_KEY: Final = "connection/opcua"


@dataclass(frozen=True, slots=True)
class VariableTag:
    """Which OPC UA node a project variable is bound to, and its unit conversion.

    `scale` and `offset` are the same pair `config.binding.JointBinding` uses and
    in the same order (`raw * scale + offset`) — a PLC sends millimetres or
    thousandths of a degree, and the scene runs in metres and radians (R8).
    """

    node_id: str
    scale: float = 1.0
    offset: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "scale": self.scale, "offset": self.offset}

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
        return cls(
            node_id=node_id,
            scale=_as_float(data.get("scale"), 1.0),
            offset=_as_float(data.get("offset"), 0.0),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "publishing_interval_ms": self.publishing_interval_ms,
            "allow_writing": self.allow_writing,
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


def _as_float(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _is_positive_number(value: Any) -> TypeGuard[int | float]:
    """A `TypeGuard` rather than a plain bool so the caller may then convert it —
    otherwise every call site needs a second, identical `isinstance`."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

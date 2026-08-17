"""The list of recently opened projects.

Two layers on purpose. The ordering rules — most recent first, no duplicates,
capped length — are a pure function and get tested without Qt. Persistence is a
thin `QSettings` wrapper on top.

`QSettings` rather than a file of our own: it already knows where per-user
settings belong on each platform, and a recent-files list is not worth inventing
a config directory for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pssim.observability import get_logger

logger = get_logger(__name__)

#: Long enough to be useful, short enough to stay a menu rather than a list.
MAX_RECENT: Final = 10

SETTINGS_KEY: Final = "recentProjects"

ORGANISATION: Final = "PSsimTool"
APPLICATION: Final = "PSsimTool"


def promote(existing: tuple[str, ...], path: str, limit: int = MAX_RECENT) -> tuple[str, ...]:
    """Put `path` at the front, drop any earlier copy of it, cap the length.

    Pure function. Comparison is case-insensitive on the whole string, which is
    right for Windows and merely harmless elsewhere — the cost of being wrong is
    one duplicate menu entry, not lost data.
    """
    lowered = path.casefold()
    kept = [item for item in existing if item.casefold() != lowered]
    return tuple([path, *kept][:limit])


def shorten(path: str, max_length: int = 60) -> str:
    """Path shortened for a menu label, keeping the end.

    The tail is what identifies a project; the head is usually a long shared
    prefix that says nothing.
    """
    if len(path) <= max_length:
        return path
    return "…" + path[-(max_length - 1) :]


class RecentProjects:
    """Recently opened projects, persisted between runs.

    Takes the `QSettings` instance rather than creating one, so tests can point
    it at a temporary ini file instead of the real user settings.
    """

    def __init__(self, settings: Any | None = None, limit: int = MAX_RECENT) -> None:
        self._settings = settings if settings is not None else default_settings()
        self._limit = limit

    @property
    def paths(self) -> tuple[Path, ...]:
        """Most recent first. Entries are not checked for existence here.

        A project on a network share that is temporarily unreachable should stay
        in the list; dropping it would punish the user for a disconnected drive.
        Whether a file opens is decided when they click it.
        """
        return tuple(Path(item) for item in self._stored)

    @property
    def _stored(self) -> tuple[str, ...]:
        raw = self._settings.value(SETTINGS_KEY, [])
        if isinstance(raw, str):
            # QSettings collapses a one-element list to a bare string.
            return (raw,)
        if not isinstance(raw, list):
            return ()
        return tuple(str(item) for item in raw if str(item))

    def add(self, path: Path) -> None:
        """Record a project as just opened."""
        updated = promote(self._stored, str(Path(path).resolve()), self._limit)
        self._settings.setValue(SETTINGS_KEY, list(updated))

    def remove(self, path: Path) -> None:
        """Forget one entry — used when a project turns out to be gone."""
        target = str(Path(path).resolve()).casefold()
        kept = [item for item in self._stored if item.casefold() != target]
        self._settings.setValue(SETTINGS_KEY, kept)

    def clear(self) -> None:
        self._settings.setValue(SETTINGS_KEY, [])

    @property
    def is_empty(self) -> bool:
        return not self._stored


def default_settings() -> Any:
    """Per-user settings in the platform's usual place."""
    from PySide6.QtCore import QSettings

    return QSettings(ORGANISATION, APPLICATION)

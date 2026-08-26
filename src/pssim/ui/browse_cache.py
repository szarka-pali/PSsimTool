"""What the address space said last time, so it is not asked twice.

Browsing a PLC is slow enough to notice — one request per folder opened, against
a server that may be on the other end of a plant network. Closing the connection
dialog used to throw all of it away, so reopening it meant walking back down to
where you were, a request at a time.

This keeps those answers. It outlives the dialog and the session; the window owns
it, hands it to whichever tree is showing, and the tree fills from it without
asking anything.

**In memory, for this run of the application, and not written anywhere.** A saved
address space is a stale one — the node somebody added on the PLC this morning
would not be in it — and `Refresh` exists precisely because a server changes
under you. Persisting it would turn a convenience into a source of wrong answers.

Pure: no Qt, no asyncua. Keyed by node id **and** path, because a struct's every
field shares one node id (R21) and they are different places.
"""

from __future__ import annotations

from typing import Final

from pssim.io.opcua_browse_session import BrowseResult
from pssim.io.opcua_path import parse_path

#: How many answers to keep. A folder is at most a few thousand rows and this is
#: a bound on a browse somebody actually performed by hand, so it is generous on
#: purpose; the guard is against a pathological address space, not against use.
DEFAULT_LIMIT: Final = 2000


class BrowseCache:
    """The children of each place that has been read, by node id and path."""

    __slots__ = ("_answers", "_limit")

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        self._answers: dict[tuple[str, str], BrowseResult] = {}
        self._limit = limit

    def __len__(self) -> int:
        return len(self._answers)

    def __contains__(self, key: object) -> bool:
        return key in self._answers

    def get(self, node_id: str, path: str = "") -> BrowseResult | None:
        """What was under this place, or `None` if it has not been read."""
        return self._answers.get((node_id, path))

    def put(self, node_id: str, path: str, result: BrowseResult) -> None:
        """Record one answer.

        A truncated answer is **not** kept: it is a partial picture of that
        folder, and serving it again from here would make the truncation
        permanent for the session without another request ever being made.
        """
        if result.is_truncated:
            return
        if len(self._answers) >= self._limit and (node_id, path) not in self._answers:
            # Oldest first, which for a browse is the way in — the root and the
            # folders on the path to wherever the user is now. Those are the
            # cheapest to fetch again.
            self._answers.pop(next(iter(self._answers)))
        self._answers[(node_id, path)] = result

    def forget(self, node_id: str, path: str = "") -> None:
        """Drop one place, and everything inside it that shares its node id.

        The second half is what makes refreshing a struct work: its fields are
        `(same node id, longer path)`, so they are stale the moment the struct is
        re-read. A **child node's** entries are not dropped, because nothing here
        can tell which node ids were under this one — they are re-read when
        opened again, or all at once by `clear`.
        """
        prefix = parse_path(path)
        for key in [
            (held_id, held_path)
            for held_id, held_path in self._answers
            if held_id == node_id and parse_path(held_path)[: len(prefix)] == prefix
        ]:
            self._answers.pop(key, None)

    def clear(self) -> None:
        """Forget the server entirely. What `Refresh All` is."""
        self._answers.clear()

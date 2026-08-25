"""Why a connection did or did not happen.

`SourceStatus.DISCONNECTED` is the whole of what the window could say before
this. It is true and it is useless: a server that refused the password, one that
does not offer anonymous, one that is not there at all and one whose certificate
we could not produce all look identical from outside.

A `DiagnosticLog` is the record of one attempt — a line per step, each with what
happened. It is written by whichever thread is connecting and read by the UI, so
appends are locked and readers get an immutable snapshot. Small on purpose: it
answers "what did it try, and where did it stop", not "everything asyncua logged".

The status code matters more than the message. `BadUserAccessDenied` is the
entire answer to "why not", and until now it appeared nowhere a user could see.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

#: How many attempts are kept. Only the last one is usually interesting, but the
#: one before it is what tells you whether a reconnect loop is making progress.
DEFAULT_HISTORY: Final = 200


class DiagnosticStep(StrEnum):
    """The stages of getting connected, in the order they happen."""

    DISCOVER = "discover"
    """Asking the server how it may be talked to."""

    SELECT = "select"
    """Choosing a policy, a mode and a token from what it offered."""

    CERTIFICATE = "certificate"
    """Finding or generating our own certificate. Only for a secure policy."""

    CHANNEL = "channel"
    """Opening the secure channel."""

    SESSION = "session"
    """Creating and activating the session. Where a bad password is refused."""

    SUBSCRIBE = "subscribe"
    """Creating the subscription and monitoring the nodes."""

    WRITE = "write"
    """Publishing a value back. Only when writing was deliberately allowed."""


class Outcome(StrEnum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    """Not attempted, and that being correct — a certificate is skipped for a
    policy that needs none, which is worth showing rather than hiding."""


@dataclass(frozen=True, slots=True)
class DiagnosticEntry:
    """One step of one attempt."""

    step: DiagnosticStep
    outcome: Outcome
    detail: str = ""
    status_code: str = ""
    """The OPC UA status code where the failure had one, e.g.
    `BadUserAccessDenied`. Kept apart from `detail` because it is the part worth
    reading first, and the part worth searching a manual for."""

    @property
    def is_failure(self) -> bool:
        return self.outcome is Outcome.FAILED

    def describe(self) -> str:
        """One line, as the diagnostics pane shows it."""
        parts = [self.step.value, self.outcome.value]
        if self.status_code:
            parts.append(self.status_code)
        if self.detail:
            parts.append(self.detail)
        return "  ".join(parts)


class DiagnosticLog:
    """The record of connection attempts, written by one thread and read by another.

    Mutable, with a lock, which is the exception this project allows for
    something that has an owner (`.claude/rules/code-style.md`). The owner is the
    source or the browse session; everyone else gets `entries`, which is a
    snapshot and cannot change underneath them.
    """

    __slots__ = ("_entries", "_lock", "_history")

    def __init__(self, history: int = DEFAULT_HISTORY) -> None:
        self._history = history
        self._lock = threading.Lock()
        self._entries: list[DiagnosticEntry] = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def entries(self) -> tuple[DiagnosticEntry, ...]:
        """Everything recorded, oldest first. A snapshot, safe to iterate."""
        with self._lock:
            return tuple(self._entries)

    @property
    def last_failure(self) -> DiagnosticEntry | None:
        """The most recent step that failed, which is what a status bar wants.

        `None` when nothing has failed — including when nothing has happened,
        because "no attempt yet" and "an attempt that worked" are both "no
        failure to report".
        """
        with self._lock:
            for entry in reversed(self._entries):
                if entry.is_failure:
                    return entry
        return None

    def record(
        self,
        step: DiagnosticStep,
        outcome: Outcome,
        detail: str = "",
        status_code: str = "",
    ) -> DiagnosticEntry:
        """Add one line. Returns it, so a caller can log the same thing once."""
        entry = DiagnosticEntry(step=step, outcome=outcome, detail=detail, status_code=status_code)
        with self._lock:
            self._entries.append(entry)
            # Trimmed from the front: a reconnect loop that has been running for
            # an hour must not grow without bound, and the newest lines are the
            # ones anyone reads.
            if len(self._entries) > self._history:
                del self._entries[: len(self._entries) - self._history]
        return entry

    def ok(self, step: DiagnosticStep, detail: str = "") -> DiagnosticEntry:
        return self.record(step, Outcome.OK, detail)

    def skipped(self, step: DiagnosticStep, detail: str = "") -> DiagnosticEntry:
        return self.record(step, Outcome.SKIPPED, detail)

    def failed(self, step: DiagnosticStep, error: BaseException) -> DiagnosticEntry:
        """Record a failure, pulling out the status code if there is one."""
        return self.record(
            step,
            Outcome.FAILED,
            detail=f"{type(error).__name__}: {error}".strip(),
            status_code=status_code_of(error),
        )

    def start_attempt(self, detail: str = "") -> None:
        """Note that another attempt is beginning, and what it is trying.

        **Appended, not cleared.** Clearing was the first design and it was
        wrong: losing the connection is a normal state and the source reconnects
        for ever (R12), so the next attempt would wipe the failure that explains
        the last one before anybody read it. The bounded history is what keeps
        this from growing instead.
        """
        if detail:
            self.record(DiagnosticStep.SELECT, Outcome.OK, detail)


def status_code_of(error: BaseException) -> str:
    """The OPC UA status code behind an exception, or `""`.

    asyncua names its errors after the codes — `BadUserAccessDenied` is both the
    class and the code — so the class name is the code whenever the error came
    from a server. Anything else (a socket error, a timeout) has none, and an
    empty string says so rather than inventing one.
    """
    from asyncua.ua.uaerrors import UaStatusCodeError

    if isinstance(error, UaStatusCodeError):
        name = type(error).__name__
        return name if name != "UaStatusCodeError" else _code_attribute(error)
    return ""


def _code_attribute(error: Any) -> str:
    """The numeric code of a bare `UaStatusCodeError`, which has no named class."""
    code = getattr(error, "code", None)
    return f"0x{code:08X}" if isinstance(code, int) else ""

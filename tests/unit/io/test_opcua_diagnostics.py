"""Tests of the connection diagnostics.

Pure apart from one thing: `status_code_of` asks asyncua what kind of error it
was handed, which is the whole point of it — the status code is the answer to
"why not", and inventing one would be worse than having none.
"""

from __future__ import annotations

import threading

from pssim.io.opcua_diagnostics import (
    DiagnosticLog,
    DiagnosticStep,
    Outcome,
    status_code_of,
)


class TestRecording:
    def test_a_fresh_log_is_empty(self) -> None:
        assert DiagnosticLog().entries == ()

    def test_a_step_is_kept(self) -> None:
        log = DiagnosticLog()

        log.ok(DiagnosticStep.SESSION, "opc.tcp://plc:4840/")

        assert len(log.entries) == 1

    def test_the_order_is_the_order_it_happened(self) -> None:
        log = DiagnosticLog()

        log.ok(DiagnosticStep.DISCOVER)
        log.ok(DiagnosticStep.SESSION)

        assert [entry.step for entry in log.entries] == [
            DiagnosticStep.DISCOVER,
            DiagnosticStep.SESSION,
        ]

    def test_a_skipped_step_is_recorded(self) -> None:
        # "Nothing to do" and "not attempted" read the same in a log that only
        # mentions what happened.
        log = DiagnosticLog()

        log.skipped(DiagnosticStep.CERTIFICATE, "no security, none needed")

        assert log.entries[0].outcome is Outcome.SKIPPED

    def test_the_history_is_bounded(self) -> None:
        # A reconnect loop runs for hours; the log must not grow with it.
        log = DiagnosticLog(history=5)

        for index in range(20):
            log.ok(DiagnosticStep.SESSION, str(index))

        assert len(log.entries) == 5

    def test_the_newest_lines_are_the_ones_kept(self) -> None:
        log = DiagnosticLog(history=3)

        for index in range(10):
            log.ok(DiagnosticStep.SESSION, str(index))

        assert [entry.detail for entry in log.entries] == ["7", "8", "9"]

    def test_a_new_attempt_keeps_what_came_before(self) -> None:
        # Clearing was the first design and it was wrong: the source reconnects
        # for ever, so the next attempt would wipe the failure that explains the
        # last one before anybody read it.
        log = DiagnosticLog()
        log.failed(DiagnosticStep.SESSION, RuntimeError("refused"))

        log.start_attempt("Basic256Sha256 / SignAndEncrypt, anonymous")

        assert log.last_failure is not None
        assert len(log.entries) == 2

    def test_an_attempt_says_what_it_is_trying(self) -> None:
        log = DiagnosticLog()

        log.start_attempt("None / None, anonymous")

        assert log.entries[0].detail == "None / None, anonymous"

    def test_reading_is_a_snapshot(self) -> None:
        log = DiagnosticLog()
        log.ok(DiagnosticStep.SESSION)

        held = log.entries
        log.ok(DiagnosticStep.SUBSCRIBE)

        assert len(held) == 1


class TestFailures:
    def test_nothing_failed_is_no_failure(self) -> None:
        log = DiagnosticLog()
        log.ok(DiagnosticStep.SESSION)

        assert log.last_failure is None

    def test_a_failure_is_found(self) -> None:
        log = DiagnosticLog()

        log.failed(DiagnosticStep.SESSION, RuntimeError("refused"))

        failure = log.last_failure
        assert failure is not None
        assert failure.step is DiagnosticStep.SESSION

    def test_the_most_recent_failure_wins(self) -> None:
        log = DiagnosticLog()
        log.failed(DiagnosticStep.DISCOVER, RuntimeError("first"))
        log.failed(DiagnosticStep.SESSION, RuntimeError("second"))

        failure = log.last_failure
        assert failure is not None
        assert "second" in failure.detail

    def test_a_later_success_does_not_hide_it(self) -> None:
        # A write that was refused while the session is fine is still worth
        # reporting, and the session line must not bury it.
        log = DiagnosticLog()
        log.failed(DiagnosticStep.WRITE, RuntimeError("read-only"))
        log.ok(DiagnosticStep.SUBSCRIBE)

        assert log.last_failure is not None

    def test_the_detail_names_the_exception(self) -> None:
        log = DiagnosticLog()

        log.failed(DiagnosticStep.SESSION, ValueError("bad url"))

        assert "ValueError" in log.entries[0].detail


class TestStatusCodes:
    def test_an_opcua_error_gives_its_code(self) -> None:
        # `BadUserAccessDenied` is both the class and the code, and it is the
        # entire answer to "why not".
        from asyncua.ua.uaerrors import BadUserAccessDenied

        assert status_code_of(BadUserAccessDenied()) == "BadUserAccessDenied"

    def test_a_plain_error_has_none(self) -> None:
        # A socket error or a timeout has no status code, and an empty string
        # says so rather than inventing one.
        assert status_code_of(TimeoutError()) == ""

    def test_a_recorded_failure_carries_the_code(self) -> None:
        from asyncua.ua.uaerrors import BadUserAccessDenied

        log = DiagnosticLog()

        log.failed(DiagnosticStep.SESSION, BadUserAccessDenied())

        assert log.entries[0].status_code == "BadUserAccessDenied"

    def test_the_code_leads_the_description(self) -> None:
        from asyncua.ua.uaerrors import BadUserAccessDenied

        log = DiagnosticLog()
        entry = log.failed(DiagnosticStep.SESSION, BadUserAccessDenied())

        # The code comes before the sentence, because it is the part worth
        # reading first and the part worth searching a manual for.
        described = entry.describe()
        assert described.index("BadUserAccessDenied") < described.index("does not have")


class TestThreadSafety:
    def test_concurrent_writers_lose_nothing(self) -> None:
        # Written by whichever thread is connecting, read by the UI. No sleeps: a
        # barrier releases every thread at once.
        log = DiagnosticLog(history=1000)
        barrier = threading.Barrier(8)

        def record(index: int) -> None:
            barrier.wait()
            for step in range(10):
                log.ok(DiagnosticStep.SESSION, f"{index}-{step}")

        threads = [threading.Thread(target=record, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(log.entries) == 80


class TestWhatCountsAsAnError:
    """`last_error` on a source means "why is there no connection", which is not
    the same as "has anything ever failed".

    Pinned here rather than only against a server: the append-only log made this
    a real trap — a failed first attempt went on being reported after a later one
    succeeded.
    """

    def test_a_failure_then_a_success_still_shows_in_the_log(self) -> None:
        log = DiagnosticLog()
        log.failed(DiagnosticStep.SESSION, RuntimeError("refused"))
        log.start_attempt("second try")
        log.ok(DiagnosticStep.SESSION, "opc.tcp://plc:4840/")

        # The log keeps it — that is the point of it being append-only.
        assert log.last_failure is not None

    def test_the_log_shows_both_attempts(self) -> None:
        log = DiagnosticLog()
        log.failed(DiagnosticStep.SESSION, RuntimeError("refused"))
        log.start_attempt("second try")
        log.ok(DiagnosticStep.SESSION, "opc.tcp://plc:4840/")

        outcomes = [entry.outcome for entry in log.entries]
        assert Outcome.FAILED in outcomes
        assert Outcome.OK in outcomes

"""Tests of converting PLC time onto the internal monotonic scale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from pssim.io.timebase import Timebase

EPOCH = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)


class TestInitialisation:
    def test_the_first_sample_fixes_the_offset(self) -> None:
        timebase = Timebase()

        internal = timebase.to_internal(EPOCH, now_monotonic_s=1000.0)

        assert internal == pytest.approx(1000.0)
        assert timebase.is_initialized is True

    def test_there_is_no_offset_before_the_first_sample(self) -> None:
        assert Timebase().offset_s is None

    def test_a_naive_datetime_is_an_error(self) -> None:
        # A naive value means the zone information was lost somewhere.
        with pytest.raises(ValueError, match="tz-aware"):
            Timebase().to_internal(datetime(2026, 8, 14, 12, 0, 0), now_monotonic_s=0.0)  # noqa: DTZ001


class TestConversion:
    def test_offset_zostava_pevny(self) -> None:
        # If the offset were recomputed continuously, every jump of the PLC clock would
        # feed into the interpolation and the parts would hop about.
        timebase = Timebase()
        timebase.to_internal(EPOCH, now_monotonic_s=1000.0)

        internal = timebase.to_internal(EPOCH + timedelta(seconds=5), now_monotonic_s=1005.0)

        assert internal == pytest.approx(1005.0)

    def test_the_gaps_between_samples_are_preserved(self) -> None:
        # The tolerance is 1 us, not less: `datetime.timestamp()` is float seconds since
        # the epoch (~1.8e9), so the resolution of float64 here is of the order of 1e-7 s.
        # For data with a 20-100 ms period that is five orders of magnitude more than needed.
        timebase = Timebase()
        timebase.to_internal(EPOCH, now_monotonic_s=1000.0)

        first = timebase.to_internal(EPOCH + timedelta(milliseconds=50), 1000.05)
        second = timebase.to_internal(EPOCH + timedelta(milliseconds=100), 1000.10)

        assert second - first == pytest.approx(0.05, abs=1e-6)

    def test_ine_pasmo_dava_rovnaky_vysledok(self) -> None:
        timebase = Timebase()
        timebase.to_internal(EPOCH, now_monotonic_s=1000.0)
        prague = timezone(timedelta(hours=2))

        internal = timebase.to_internal(
            (EPOCH + timedelta(seconds=5)).astimezone(prague), now_monotonic_s=1005.0
        )

        assert internal == pytest.approx(1005.0)


class TestDrift:
    def test_no_drift_raises_no_flag(self) -> None:
        timebase = Timebase(max_drift_s=5.0)
        timebase.to_internal(EPOCH, now_monotonic_s=1000.0)

        timebase.to_internal(EPOCH + timedelta(seconds=1), now_monotonic_s=1001.0)

        assert timebase.drift_exceeded is False

    def test_skok_hodin_plc_nastavi_priznak(self) -> None:
        timebase = Timebase(max_drift_s=5.0)
        timebase.to_internal(EPOCH, now_monotonic_s=1000.0)

        timebase.to_internal(EPOCH + timedelta(seconds=60), now_monotonic_s=1001.0)

        assert timebase.drift_exceeded is True

    def test_drift_neresetuje_offset(self) -> None:
        timebase = Timebase(max_drift_s=5.0)
        timebase.to_internal(EPOCH, now_monotonic_s=1000.0)
        offset_before = timebase.offset_s

        timebase.to_internal(EPOCH + timedelta(seconds=60), now_monotonic_s=1001.0)

        assert timebase.offset_s == offset_before

"""Testy prevodu času PLC na internú monotónnu škálu."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from pssim.io.timebase import Timebase

EPOCH = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)


class TestInicializacia:
    def test_prva_vzorka_urci_offset(self) -> None:
        timebase = Timebase()

        internal = timebase.to_internal(EPOCH, now_monotonic_s=1000.0)

        assert internal == pytest.approx(1000.0)
        assert timebase.is_initialized is True

    def test_pred_prvou_vzorkou_nie_je_offset(self) -> None:
        assert Timebase().offset_s is None

    def test_naivny_datetime_je_chyba(self) -> None:
        # Naivná hodnota znamená, že sa niekde stratila informácia o zóne.
        with pytest.raises(ValueError, match="tz-aware"):
            Timebase().to_internal(datetime(2026, 8, 14, 12, 0, 0), now_monotonic_s=0.0)  # noqa: DTZ001


class TestPrevod:
    def test_offset_zostava_pevny(self) -> None:
        # Keby sa offset prepočítaval priebežne, každý skok hodín PLC by sa
        # premietol do interpolácie a diely by poskakovali.
        timebase = Timebase()
        timebase.to_internal(EPOCH, now_monotonic_s=1000.0)

        internal = timebase.to_internal(EPOCH + timedelta(seconds=5), now_monotonic_s=1005.0)

        assert internal == pytest.approx(1005.0)

    def test_zachova_rozostupy_medzi_vzorkami(self) -> None:
        # Tolerancia je 1 us, nie menej: `datetime.timestamp()` je float sekúnd
        # od epochy (~1.8e9), takže rozlíšenie float64 je tu rádovo 1e-7 s.
        # Pre dáta s periódou 20-100 ms je to o päť rádov viac, než treba.
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
    def test_bez_driftu_nie_je_priznak(self) -> None:
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

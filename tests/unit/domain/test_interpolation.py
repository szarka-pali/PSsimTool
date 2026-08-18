"""Tests of sample interpolation.

Time is always an argument — no test here may reach for a clock.
"""

from __future__ import annotations

import pytest

from pssim.domain.interpolation import Sample, SignalBuffer
from tests.factories import buffer_with


class TestDegenerateInput:
    def test_an_empty_buffer_returns_none(self) -> None:
        assert SignalBuffer().sample_at(1.0) is None

    def test_a_single_sample_returns_its_value(self) -> None:
        assert buffer_with((10.0, 5.0)).sample_at(999.0) == 5.0

    def test_identical_timestamps_return_the_newer_value(self) -> None:
        # Common on the S7-1500, for instance, where SourceTimestamp has OB cycle resolution.
        signal = buffer_with((0.0, 0.0), (1.0, 10.0), (1.0, 20.0), (2.0, 30.0))

        assert signal.sample_at(1.0) == pytest.approx(10.0)

    def test_a_capacity_below_two_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            SignalBuffer(capacity=1)


class TestInterpolation:
    def test_midway_between_two_samples(self) -> None:
        signal = buffer_with((0.0, 0.0), (2.0, 10.0))

        assert signal.sample_at(1.0) == pytest.approx(5.0)

    def test_at_a_quarter_of_the_way(self) -> None:
        signal = buffer_with((0.0, 0.0), (4.0, 100.0))

        assert signal.sample_at(1.0) == pytest.approx(25.0)

    def test_exactly_on_a_sample(self) -> None:
        signal = buffer_with((0.0, 0.0), (1.0, 10.0), (2.0, 20.0))

        assert signal.sample_at(1.0) == pytest.approx(10.0)

    def test_the_right_pair_is_chosen_from_several(self) -> None:
        signal = buffer_with((0.0, 0.0), (1.0, 10.0), (2.0, 20.0), (3.0, 30.0))

        assert signal.sample_at(2.5) == pytest.approx(25.0)


class TestExtrapolation:
    def test_before_the_first_sample_the_first_value_comes_back(self) -> None:
        signal = buffer_with((10.0, 5.0), (11.0, 6.0))

        assert signal.sample_at(0.0) == pytest.approx(5.0)

    def test_after_the_last_sample_the_last_value_comes_back(self) -> None:
        # Extrapolation would send a part off to infinity whenever a signal got stuck.
        signal = buffer_with((10.0, 5.0), (11.0, 6.0))

        assert signal.sample_at(1000.0) == pytest.approx(6.0)


class TestSampleOrder:
    def test_an_older_sample_is_dropped(self) -> None:
        # On a reconnect, servers sometimes send an old value — it would cause a jump backwards.
        signal = buffer_with((10.0, 5.0))

        signal.put(Sample(source_time_s=9.0, value=99.0))

        assert len(signal) == 1
        assert signal.sample_at(10.0) == pytest.approx(5.0)

    def test_ring_buffer_zahodi_najstarsie(self) -> None:
        signal = buffer_with((0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0), capacity=2)

        assert len(signal) == 2
        assert signal.latest is not None
        assert signal.latest.value == pytest.approx(3.0)


class TestStale:
    def test_an_empty_buffer_is_stale(self) -> None:
        assert SignalBuffer().is_stale(at_time_s=0.0, stale_after_s=1.0) is True

    def test_a_fresh_sample_is_not_stale(self) -> None:
        assert buffer_with((10.0, 1.0)).is_stale(at_time_s=10.5, stale_after_s=1.0) is False

    def test_an_old_sample_is_stale(self) -> None:
        assert buffer_with((10.0, 1.0)).is_stale(at_time_s=12.0, stale_after_s=1.0) is True

    def test_exactly_on_the_threshold_is_not_stale_yet(self) -> None:
        assert buffer_with((10.0, 1.0)).is_stale(at_time_s=11.0, stale_after_s=1.0) is False

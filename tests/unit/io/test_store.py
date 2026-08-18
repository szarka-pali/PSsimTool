"""Tests of the thread-safe state store.

Concurrency is tested with `threading.Barrier`, never with `sleep()`.
"""

from __future__ import annotations

import threading

import pytest

from pssim.io.store import StateStore


class TestBasicOperations:
    def test_an_empty_store_has_no_signals(self) -> None:
        assert StateStore().sample_all(at_time_s=0.0) == {}

    def test_a_stored_value_can_be_read_back(self) -> None:
        store = StateStore()

        store.put("axis_x", value=1.5, source_time_s=10.0)

        assert store.sample("axis_x", at_time_s=10.0) == pytest.approx(1.5)

    def test_neznamy_signal_vrati_none(self) -> None:
        assert StateStore().sample("neexistuje", at_time_s=0.0) is None

    def test_sample_all_interpoluje_vsetky_signaly(self) -> None:
        store = StateStore()
        for signal, value in (("axis_x", 0.0), ("axis_z", 10.0)):
            store.put(signal, value=value, source_time_s=0.0)
            store.put(signal, value=value + 2.0, source_time_s=2.0)

        snapshot = store.sample_all(at_time_s=1.0)

        assert snapshot == pytest.approx({"axis_x": 1.0, "axis_z": 11.0})

    def test_clear_zmaze_vsetko(self) -> None:
        store = StateStore()
        store.put("axis_x", value=1.0, source_time_s=0.0)

        store.clear()

        assert len(store) == 0


class TestTimeQueries:
    def test_latest_time_is_the_maximum_across_signals(self) -> None:
        store = StateStore()
        store.put("axis_x", value=1.0, source_time_s=5.0)
        store.put("axis_z", value=1.0, source_time_s=8.0)

        assert store.latest_time() == pytest.approx(8.0)

    def test_latest_time_of_an_empty_store_is_none(self) -> None:
        assert StateStore().latest_time() is None

    def test_stale_signals_najde_zastarane(self) -> None:
        store = StateStore()
        store.put("cerstvy", value=1.0, source_time_s=10.0)
        store.put("stary", value=1.0, source_time_s=1.0)

        stale = store.stale_signals(at_time_s=10.0, stale_after_s=1.0)

        assert stale == frozenset({"stary"})


class TestConcurrency:
    def test_concurrent_writing_and_reading_loses_nothing(self) -> None:
        store = StateStore(capacity=256)
        writers = 4
        samples_per_writer = 200
        barrier = threading.Barrier(writers + 1)
        errors: list[BaseException] = []

        def write(index: int) -> None:
            barrier.wait()
            for step in range(samples_per_writer):
                store.put(f"signal_{index}", value=float(step), source_time_s=float(step))

        def read() -> None:
            barrier.wait()
            for _ in range(samples_per_writer):
                try:
                    store.sample_all(at_time_s=50.0)
                except BaseException as exc:  # noqa: BLE001 - we want to catch anything
                    errors.append(exc)
                    return

        threads = [threading.Thread(target=write, args=(index,)) for index in range(writers)]
        threads.append(threading.Thread(target=read))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)

        assert errors == []
        assert len(store) == writers

    def test_kazdy_signal_ma_svoj_buffer(self) -> None:
        store = StateStore()

        store.put("a", value=1.0, source_time_s=0.0)
        store.put("b", value=2.0, source_time_s=0.0)

        assert store.signal_names == frozenset({"a", "b"})

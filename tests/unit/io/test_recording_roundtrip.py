"""Tests of recording and reading it back."""

from __future__ import annotations

from pathlib import Path

import pytest

from pssim.domain.errors import DataSourceError
from pssim.io.recorder import RecordingStore
from pssim.io.replay import read_recording


class TestRecording:
    def test_written_samples_can_be_read_back(self, tmp_path: Path) -> None:
        path = tmp_path / "beh.jsonl"
        with RecordingStore(path) as store:
            store.put("axis_x", value=1.5, source_time_s=10.0)
            store.put("axis_z", value=2.5, source_time_s=10.1)

        samples = read_recording(path)

        assert [(item.signal, item.sample.value) for item in samples] == [
            ("axis_x", 1.5),
            ("axis_z", 2.5),
        ]

    def test_the_recording_is_ordered_by_time(self, tmp_path: Path) -> None:
        path = tmp_path / "beh.jsonl"
        with RecordingStore(path) as store:
            store.put("a", value=1.0, source_time_s=5.0)
            store.put("b", value=2.0, source_time_s=1.0)

        times = [item.sample.source_time_s for item in read_recording(path)]

        assert times == sorted(times)

    def test_the_store_stays_usable_while_recording(self, tmp_path: Path) -> None:
        with RecordingStore(tmp_path / "beh.jsonl") as store:
            store.put("axis_x", value=0.0, source_time_s=0.0)
            store.put("axis_x", value=2.0, source_time_s=2.0)

            assert store.sample("axis_x", at_time_s=1.0) == pytest.approx(1.0)

    def test_the_samples_are_counted(self, tmp_path: Path) -> None:
        with RecordingStore(tmp_path / "beh.jsonl") as store:
            for step in range(5):
                store.put("axis_x", value=float(step), source_time_s=float(step))

            assert store.sample_count == 5

    def test_with_nothing_open_it_writes_nothing_but_survives(self, tmp_path: Path) -> None:
        store = RecordingStore(tmp_path / "beh.jsonl")

        store.put("axis_x", value=1.0, source_time_s=0.0)

        assert store.sample_count == 0
        assert store.sample("axis_x", at_time_s=0.0) == pytest.approx(1.0)


class TestReading:
    def test_a_damaged_line_is_skipped(self, tmp_path: Path) -> None:
        # An interrupted write leaves the last line incomplete — that must not
        # invalidate the whole recording.
        path = tmp_path / "beh.jsonl"
        path.write_text(
            '{"t": 1.0, "signal": "a", "value": 1.0}\n{"t": 2.0, "sig\n',
            encoding="utf-8",
        )

        assert len(read_recording(path)) == 1

    def test_empty_lines_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "beh.jsonl"
        path.write_text('\n{"t": 1.0, "signal": "a", "value": 1.0}\n\n', encoding="utf-8")

        assert len(read_recording(path)) == 1

    def test_a_recording_with_no_usable_sample_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "prazdny.jsonl"
        path.write_text("\n\n", encoding="utf-8")

        with pytest.raises(DataSourceError, match="not a single usable sample"):
            read_recording(path)

    def test_a_missing_file_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(DataSourceError, match="cannot be read"):
            read_recording(tmp_path / "nic.jsonl")

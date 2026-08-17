"""Testy záznamu a jeho spätného načítania."""

from __future__ import annotations

from pathlib import Path

import pytest

from pssim.domain.errors import DataSourceError
from pssim.io.recorder import RecordingStore
from pssim.io.replay import read_recording


class TestZaznam:
    def test_zapisane_vzorky_sa_daju_nacitat(self, tmp_path: Path) -> None:
        path = tmp_path / "beh.jsonl"
        with RecordingStore(path) as store:
            store.put("os_x", value=1.5, source_time_s=10.0)
            store.put("os_z", value=2.5, source_time_s=10.1)

        samples = read_recording(path)

        assert [(item.signal, item.sample.value) for item in samples] == [
            ("os_x", 1.5),
            ("os_z", 2.5),
        ]

    def test_zaznam_sa_zoradi_podla_casu(self, tmp_path: Path) -> None:
        path = tmp_path / "beh.jsonl"
        with RecordingStore(path) as store:
            store.put("a", value=1.0, source_time_s=5.0)
            store.put("b", value=2.0, source_time_s=1.0)

        times = [item.sample.source_time_s for item in read_recording(path)]

        assert times == sorted(times)

    def test_store_zostava_pouzitelny_aj_pocas_zaznamu(self, tmp_path: Path) -> None:
        with RecordingStore(tmp_path / "beh.jsonl") as store:
            store.put("os_x", value=0.0, source_time_s=0.0)
            store.put("os_x", value=2.0, source_time_s=2.0)

            assert store.sample("os_x", at_time_s=1.0) == pytest.approx(1.0)

    def test_pocita_vzorky(self, tmp_path: Path) -> None:
        with RecordingStore(tmp_path / "beh.jsonl") as store:
            for step in range(5):
                store.put("os_x", value=float(step), source_time_s=float(step))

            assert store.sample_count == 5

    def test_bez_otvorenia_nezapisuje_ale_nespadne(self, tmp_path: Path) -> None:
        store = RecordingStore(tmp_path / "beh.jsonl")

        store.put("os_x", value=1.0, source_time_s=0.0)

        assert store.sample_count == 0
        assert store.sample("os_x", at_time_s=0.0) == pytest.approx(1.0)


class TestCitanie:
    def test_poskodeny_riadok_sa_preskoci(self, tmp_path: Path) -> None:
        # Prerušený zápis zanechá posledný riadok nekompletný — nesmie to
        # znehodnotiť celý záznam.
        path = tmp_path / "beh.jsonl"
        path.write_text(
            '{"t": 1.0, "signal": "a", "value": 1.0}\n{"t": 2.0, "sig\n',
            encoding="utf-8",
        )

        assert len(read_recording(path)) == 1

    def test_prazdne_riadky_sa_ignoruju(self, tmp_path: Path) -> None:
        path = tmp_path / "beh.jsonl"
        path.write_text('\n{"t": 1.0, "signal": "a", "value": 1.0}\n\n', encoding="utf-8")

        assert len(read_recording(path)) == 1

    def test_zaznam_bez_pouzitelnej_vzorky_je_chyba(self, tmp_path: Path) -> None:
        path = tmp_path / "prazdny.jsonl"
        path.write_text("\n\n", encoding="utf-8")

        with pytest.raises(DataSourceError, match="ani jednu použiteľnú vzorku"):
            read_recording(path)

    def test_neexistujuci_subor_je_chyba(self, tmp_path: Path) -> None:
        with pytest.raises(DataSourceError, match="sa nedá prečítať"):
            read_recording(tmp_path / "nic.jsonl")

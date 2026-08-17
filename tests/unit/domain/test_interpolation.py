"""Testy interpolácie vzoriek.

Čas je vždy argument — žiadny test tu nesmie siahnuť na hodiny.
"""

from __future__ import annotations

import pytest

from pssim.domain.interpolation import Sample, SignalBuffer
from tests.factories import buffer_with


class TestDegenerovaneVstupy:
    def test_prazdny_buffer_vrati_none(self) -> None:
        assert SignalBuffer().sample_at(1.0) is None

    def test_jedina_vzorka_vrati_svoju_hodnotu(self) -> None:
        assert buffer_with((10.0, 5.0)).sample_at(999.0) == 5.0

    def test_rovnake_casove_znamky_vratia_novsiu_hodnotu(self) -> None:
        # Bežné napr. na S7-1500, kde má SourceTimestamp rozlíšenie cyklu OB.
        signal = buffer_with((0.0, 0.0), (1.0, 10.0), (1.0, 20.0), (2.0, 30.0))

        assert signal.sample_at(1.0) == pytest.approx(10.0)

    def test_capacity_pod_dvoma_je_chyba(self) -> None:
        with pytest.raises(ValueError, match="aspoň 2"):
            SignalBuffer(capacity=1)


class TestInterpolacia:
    def test_v_strede_medzi_dvoma_vzorkami(self) -> None:
        signal = buffer_with((0.0, 0.0), (2.0, 10.0))

        assert signal.sample_at(1.0) == pytest.approx(5.0)

    def test_v_stvrtine(self) -> None:
        signal = buffer_with((0.0, 0.0), (4.0, 100.0))

        assert signal.sample_at(1.0) == pytest.approx(25.0)

    def test_presne_na_vzorke(self) -> None:
        signal = buffer_with((0.0, 0.0), (1.0, 10.0), (2.0, 20.0))

        assert signal.sample_at(1.0) == pytest.approx(10.0)

    def test_vyberie_spravnu_dvojicu_z_viacerych(self) -> None:
        signal = buffer_with((0.0, 0.0), (1.0, 10.0), (2.0, 20.0), (3.0, 30.0))

        assert signal.sample_at(2.5) == pytest.approx(25.0)


class TestExtrapolacia:
    def test_pred_prvou_vzorkou_vrati_prvu_hodnotu(self) -> None:
        signal = buffer_with((10.0, 5.0), (11.0, 6.0))

        assert signal.sample_at(0.0) == pytest.approx(5.0)

    def test_po_poslednej_vzorke_vrati_poslednu_hodnotu(self) -> None:
        # Extrapolácia by pri zaseknutom signáli poslala diel do nekonečna.
        signal = buffer_with((10.0, 5.0), (11.0, 6.0))

        assert signal.sample_at(1000.0) == pytest.approx(6.0)


class TestPoradieVzoriek:
    def test_starsia_vzorka_sa_zahodi(self) -> None:
        # Servery pri reconnecte občas pošlú starú hodnotu — spôsobila by skok dozadu.
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
    def test_prazdny_buffer_je_stale(self) -> None:
        assert SignalBuffer().is_stale(at_time_s=0.0, stale_after_s=1.0) is True

    def test_cerstva_vzorka_nie_je_stale(self) -> None:
        assert buffer_with((10.0, 1.0)).is_stale(at_time_s=10.5, stale_after_s=1.0) is False

    def test_stara_vzorka_je_stale(self) -> None:
        assert buffer_with((10.0, 1.0)).is_stale(at_time_s=12.0, stale_after_s=1.0) is True

    def test_presne_na_hranici_este_nie_je_stale(self) -> None:
        assert buffer_with((10.0, 1.0)).is_stale(at_time_s=11.0, stale_after_s=1.0) is False

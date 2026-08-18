"""Testy cache tesselovanej geometrie."""

from __future__ import annotations

from pathlib import Path

import pytest

from pssim.cad.cache import (
    IMPORTER_VERSION,
    CacheEntry,
    CacheKey,
    CacheMetadata,
    file_sha256,
    mesh_filename,
)
from pssim.cad.model import CadAssembly, CadNode
from pssim.domain.errors import CacheError


def key(**overrides: object) -> CacheKey:
    defaults: dict[str, object] = {
        "source_sha256": "abc123",
        "scale_to_m": 1e-3,
        "linear_deflection_mm": 0.5,
        "angular_deflection_rad": 0.35,
    }
    return CacheKey(**{**defaults, **overrides})  # type: ignore[arg-type]


def metadata(cache_key: CacheKey | None = None) -> CacheMetadata:
    return CacheMetadata(
        key=cache_key or key(),
        source_file="models/test.step",
        units_used="mm",
        assembly=CadAssembly(
            nodes=(CadNode(path="base", children=("base/portal",)), CadNode(path="base/portal")),
            roots=("base",),
        ),
    )


class TestCacheKey:
    def test_rovnaky_kluc_da_rovnaky_digest(self) -> None:
        assert key().digest == key().digest

    @pytest.mark.parametrize(
        "override",
        [
            {"source_sha256": "ine"},
            {"scale_to_m": 1.0},
            {"linear_deflection_mm": 0.1},
            {"angular_deflection_rad": 0.1},
            {"importer_version": IMPORTER_VERSION + 1},
        ],
    )
    def test_zmena_ktorehokolvek_vstupu_zmeni_digest(self, override: dict[str, object]) -> None:
        # Bez toho by sa po zmene parametrov ticho použila stará cache.
        assert key(**override).digest != key().digest


class TestZapisACitanie:
    def test_zapis_a_nacitanie_zachova_data(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())

        entry.write(metadata())

        assert entry.read().assembly.roots == ("base",)

    def test_neexistujuca_cache_navadza_na_import(self, tmp_path: Path) -> None:
        with pytest.raises(CacheError, match="import-step"):
            CacheEntry(root=tmp_path, key=key()).read()

    def test_exists_je_false_pred_zapisom(self, tmp_path: Path) -> None:
        assert CacheEntry(root=tmp_path, key=key()).exists is False

    def test_stara_verzia_importera_je_chyba(self, tmp_path: Path) -> None:
        # Simuluje cache zapísanú starším importérom: adresár sedí, obsah je starý.
        entry = CacheEntry(root=tmp_path, key=key())
        entry.write(metadata(key(importer_version=IMPORTER_VERSION - 1)))

        with pytest.raises(CacheError, match="importer version"):
            entry.read()

    def test_poskodeny_json_je_chyba(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())
        entry.directory.mkdir(parents=True)
        entry.metadata_path.write_text("{nedokoncene", encoding="utf-8")

        with pytest.raises(CacheError, match="cannot be read"):
            entry.read()

    def test_neuplny_json_je_chyba(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())
        entry.directory.mkdir(parents=True)
        entry.metadata_path.write_text('{"importer_version": 1}', encoding="utf-8")

        with pytest.raises(CacheError, match="damaged or incomplete"):
            entry.read()

    def test_zapis_nezanecha_docasny_subor(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())

        entry.write(metadata())

        assert list(entry.directory.glob("*.tmp")) == []


class TestHashovanie:
    def test_rovnaky_obsah_da_rovnaky_hash(self, tmp_path: Path) -> None:
        first = tmp_path / "a.step"
        second = tmp_path / "b.step"
        first.write_bytes(b"ISO-10303-21;")
        second.write_bytes(b"ISO-10303-21;")

        assert file_sha256(first) == file_sha256(second)

    def test_iny_obsah_da_iny_hash(self, tmp_path: Path) -> None:
        first = tmp_path / "a.step"
        second = tmp_path / "b.step"
        first.write_bytes(b"jeden")
        second.write_bytes(b"druhy")

        assert file_sha256(first) != file_sha256(second)

    def test_chybajuci_subor_je_chyba(self, tmp_path: Path) -> None:
        with pytest.raises(CacheError, match="cannot be read"):
            file_sha256(tmp_path / "nic.step")


class TestNazvySuborov:
    def test_dvojbodky_sa_odstrania(self) -> None:
        # XCAF entry vyzerá ako `0:1:1:3` — dvojbodka je na Windows zakázaná.
        name = mesh_filename("0:1:1:3")

        assert ":" not in name

    def test_rozne_kluce_daju_rozne_subory(self) -> None:
        # Po sanitizácii by sa `0:1` a `0_1` zliali — preto je v názve hash.
        assert mesh_filename("0:1") != mesh_filename("0_1")

    def test_rovnaky_kluc_da_rovnaky_subor(self) -> None:
        # Toto je podstata zdieľania meshu medzi inštanciami toho istého dielu.
        assert mesh_filename("0:1:1:3") == mesh_filename("0:1:1:3")

    def test_konci_na_npz(self) -> None:
        assert mesh_filename("0:1:1:3").endswith(".npz")

    def test_velmi_dlhy_kluc_da_rozumne_dlhy_nazov(self) -> None:
        name = mesh_filename(":".join(["0"] * 200))

        assert len(name) < 100

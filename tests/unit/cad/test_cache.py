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
        # Without it, a change of parameters would silently reuse the old cache.
        assert key(**override).digest != key().digest


class TestWritingAndReading:
    def test_writing_and_reading_preserves_the_data(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())

        entry.write(metadata())

        assert entry.read().assembly.roots == ("base",)

    def test_a_missing_cache_points_at_the_import(self, tmp_path: Path) -> None:
        with pytest.raises(CacheError, match="import-step"):
            CacheEntry(root=tmp_path, key=key()).read()

    def test_exists_is_false_before_the_write(self, tmp_path: Path) -> None:
        assert CacheEntry(root=tmp_path, key=key()).exists is False

    def test_an_old_importer_version_is_an_error(self, tmp_path: Path) -> None:
        # Simulates a cache written by an older importer: the directory matches, the content is old.
        entry = CacheEntry(root=tmp_path, key=key())
        entry.write(metadata(key(importer_version=IMPORTER_VERSION - 1)))

        with pytest.raises(CacheError, match="importer version"):
            entry.read()

    def test_damaged_json_is_an_error(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())
        entry.directory.mkdir(parents=True)
        entry.metadata_path.write_text("{nedokoncene", encoding="utf-8")

        with pytest.raises(CacheError, match="cannot be read"):
            entry.read()

    def test_incomplete_json_is_an_error(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())
        entry.directory.mkdir(parents=True)
        entry.metadata_path.write_text('{"importer_version": 1}', encoding="utf-8")

        with pytest.raises(CacheError, match="damaged or incomplete"):
            entry.read()

    def test_the_write_leaves_no_temporary_file(self, tmp_path: Path) -> None:
        entry = CacheEntry(root=tmp_path, key=key())

        entry.write(metadata())

        assert list(entry.directory.glob("*.tmp")) == []


class TestHashing:
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

    def test_a_missing_file_is_an_error_too(self, tmp_path: Path) -> None:
        with pytest.raises(CacheError, match="cannot be read"):
            file_sha256(tmp_path / "nic.step")


class TestFileNames:
    def test_colons_are_removed(self) -> None:
        # An XCAF entry looks like `0:1:1:3` — a colon is forbidden on Windows.
        name = mesh_filename("0:1:1:3")

        assert ":" not in name

    def test_different_keys_give_different_files(self) -> None:
        # After sanitising, `0:1` and `0_1` would collide — hence the hash in the name.
        assert mesh_filename("0:1") != mesh_filename("0_1")

    def test_the_same_key_gives_the_same_file(self) -> None:
        # This is the essence of sharing a mesh between instances of the same part.
        assert mesh_filename("0:1:1:3") == mesh_filename("0:1:1:3")

    def test_the_name_ends_with_npz(self) -> None:
        assert mesh_filename("0:1:1:3").endswith(".npz")

    def test_a_very_long_key_gives_a_sane_name(self) -> None:
        name = mesh_filename(":".join(["0"] * 200))

        assert len(name) < 100

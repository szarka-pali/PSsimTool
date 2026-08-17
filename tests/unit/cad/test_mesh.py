"""Testy neutrálneho formátu meshu.

Bez OpenCASCADE aj bez Panda3D — je to čistý numpy formát.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pssim.cad.mesh import (
    MESH_FORMAT_VERSION,
    MeshData,
    build_mesh,
    compute_vertex_normals,
    empty_mesh,
    read_mesh,
    write_mesh,
)
from pssim.domain.errors import CacheError

#: Štvorec v rovine z=0, dva trojuholníky. Normály musia mieriť na +Z.
SQUARE_VERTICES = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)
SQUARE_INDICES = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)


def square() -> MeshData:
    return build_mesh(SQUARE_VERTICES, SQUARE_INDICES)


class TestValidacia:
    def test_zly_tvar_vrcholov_je_chyba(self) -> None:
        with pytest.raises(ValueError, match=r"tvar \(N, 3\)"):
            MeshData(
                vertices=np.zeros((4, 2), dtype=np.float32),
                normals=np.zeros((4, 2), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
            )

    def test_pocet_normal_musi_sediet_s_vrcholmi(self) -> None:
        with pytest.raises(ValueError, match="jedna normála na vrchol"):
            MeshData(
                vertices=np.zeros((4, 3), dtype=np.float32),
                normals=np.zeros((3, 3), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
            )

    def test_index_mimo_rozsahu_je_chyba(self) -> None:
        # Bez tejto kontroly by Panda3D čítal mimo buffer a padol až pri renderi.
        with pytest.raises(ValueError, match="mieri mimo"):
            build_mesh(SQUARE_VERTICES, np.array([[0, 1, 99]], dtype=np.uint32))

    def test_prazdny_mesh_je_platny(self) -> None:
        assert empty_mesh().is_empty is True


class TestNormaly:
    def test_stvorec_v_rovine_z_ma_normaly_hore(self) -> None:
        normals = compute_vertex_normals(SQUARE_VERTICES, SQUARE_INDICES)

        assert normals == pytest.approx(np.tile([0.0, 0.0, 1.0], (4, 1)), abs=1e-6)

    def test_normaly_su_jednotkove(self) -> None:
        mesh = build_mesh(
            np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32),
            np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], dtype=np.uint32),
        )

        lengths = np.linalg.norm(mesh.normals, axis=1)

        assert lengths == pytest.approx(np.ones(4), abs=1e-6)

    def test_obratene_poradie_obrati_normalu(self) -> None:
        # Presne toto rieši oprava windingu pri TopAbs_REVERSED plochách.
        flipped = compute_vertex_normals(SQUARE_VERTICES, SQUARE_INDICES[:, ::-1])

        assert flipped == pytest.approx(np.tile([0.0, 0.0, -1.0], (4, 1)), abs=1e-6)

    def test_osamoteny_vrchol_dostane_nahradnu_normalu(self) -> None:
        # Nulová normála by v scéne dala čiernu plochu.
        vertices = np.vstack([SQUARE_VERTICES, [[5, 5, 5]]]).astype(np.float32)

        normals = compute_vertex_normals(vertices, SQUARE_INDICES)

        assert normals[4] == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)

    def test_zdielany_vrchol_priemeruje_susedne_plochy(self) -> None:
        # Dve steny pravého uhla → normála zdieľaného vrchola má ísť po uhlopriečke.
        vertices = np.array(
            [[0, 0, 0], [1, 0, 0], [1, 1, 0], [1, 0, 1], [0, 0, 1]], dtype=np.float32
        )
        indices = np.array([[0, 1, 2], [0, 4, 3]], dtype=np.uint32)

        normals = compute_vertex_normals(vertices, indices)

        assert np.linalg.norm(normals[0]) == pytest.approx(1.0, abs=1e-6)


class TestZapisACitanie:
    def test_roundtrip_zachova_vrcholy(self, tmp_path: Path) -> None:
        path = tmp_path / "mesh.npz"
        write_mesh(path, square())

        assert read_mesh(path).vertices == pytest.approx(SQUARE_VERTICES, abs=1e-6)

    def test_roundtrip_zachova_indexy(self, tmp_path: Path) -> None:
        path = tmp_path / "mesh.npz"
        write_mesh(path, square())

        assert np.array_equal(read_mesh(path).indices, SQUARE_INDICES)

    def test_roundtrip_zachova_normaly(self, tmp_path: Path) -> None:
        path = tmp_path / "mesh.npz"
        write_mesh(path, square())

        assert read_mesh(path).normals == pytest.approx(square().normals, abs=1e-6)

    def test_typy_su_zjednotene(self, tmp_path: Path) -> None:
        # Do cache sa nesmú dostať raz float64 a raz float32 podľa toho,
        # odkiaľ dáta prišli — Panda3D číta buffer po bajtoch.
        path = tmp_path / "mesh.npz"
        write_mesh(path, build_mesh(SQUARE_VERTICES.astype(np.float64), SQUARE_INDICES))

        loaded = read_mesh(path)

        assert loaded.vertices.dtype == np.float32
        assert loaded.indices.dtype == np.uint32

    def test_zapis_vytvori_chybajuci_adresar(self, tmp_path: Path) -> None:
        path = tmp_path / "hlbsie" / "este" / "mesh.npz"

        write_mesh(path, square())

        assert path.is_file()

    def test_zapis_nezanecha_docasny_subor(self, tmp_path: Path) -> None:
        write_mesh(tmp_path / "mesh.npz", square())

        assert list(tmp_path.glob("*.tmp")) == []

    def test_chybajuci_subor_je_chyba(self, tmp_path: Path) -> None:
        with pytest.raises(CacheError, match="sa nedá prečítať"):
            read_mesh(tmp_path / "nic.npz")

    def test_poskodeny_subor_je_chyba(self, tmp_path: Path) -> None:
        path = tmp_path / "mesh.npz"
        path.write_bytes(b"toto nie je npz")

        with pytest.raises(CacheError):
            read_mesh(path)

    def test_stara_verzia_formatu_je_chyba(self, tmp_path: Path) -> None:
        path = tmp_path / "mesh.npz"
        mesh = square()
        with path.open("wb") as handle:
            np.savez_compressed(
                handle,
                version=np.array([MESH_FORMAT_VERSION + 1], dtype=np.int32),
                vertices=mesh.vertices,
                normals=mesh.normals,
                indices=mesh.indices,
            )

        with pytest.raises(CacheError, match="formáte verzie"):
            read_mesh(path)


class TestOdvodeneUdaje:
    def test_pocty(self) -> None:
        mesh = square()

        assert (mesh.vertex_count, mesh.triangle_count) == (4, 2)

    def test_bounding_box(self) -> None:
        low, high = square().bounding_box()

        assert low == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
        assert high == pytest.approx((1.0, 1.0, 0.0), abs=1e-6)

    def test_bounding_box_prazdneho_meshu_je_nulovy(self) -> None:
        low, high = empty_mesh().bounding_box()

        assert low == high == (0.0, 0.0, 0.0)

    def test_bounding_box_zaporne_suradnice(self) -> None:
        mesh = build_mesh(SQUARE_VERTICES - 2.0, SQUARE_INDICES)

        low, _ = mesh.bounding_box()

        assert low == pytest.approx((-2.0, -2.0, -2.0), abs=1e-6)

    def test_uhlopriecka_kvadra_sedi(self) -> None:
        low, high = build_mesh(
            np.array([[0, 0, 0], [3, 4, 0], [0, 4, 0]], dtype=np.float32),
            np.array([[0, 1, 2]], dtype=np.uint32),
        ).bounding_box()

        diagonal = math.dist(low, high)

        assert diagonal == pytest.approx(5.0, abs=1e-6)

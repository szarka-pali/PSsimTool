"""Tests of the neutral mesh format.

Without OpenCASCADE and without Panda3D — it is a pure numpy format.
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

#: A square in the plane z=0, two triangles. The normals must point at +Z.
SQUARE_VERTICES = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)
SQUARE_INDICES = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)


def square() -> MeshData:
    return build_mesh(SQUARE_VERTICES, SQUARE_INDICES)


class TestValidation:
    def test_a_wrong_vertex_shape_is_an_error(self) -> None:
        with pytest.raises(ValueError, match=r"shape \(N, 3\)"):
            MeshData(
                vertices=np.zeros((4, 2), dtype=np.float32),
                normals=np.zeros((4, 2), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
            )

    def test_the_normal_count_must_match_the_vertices(self) -> None:
        with pytest.raises(ValueError, match="one normal per vertex"):
            MeshData(
                vertices=np.zeros((4, 3), dtype=np.float32),
                normals=np.zeros((3, 3), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
            )

    def test_an_index_out_of_range_is_an_error(self) -> None:
        # Without this check Panda3D would read past the buffer and crash at render time.
        with pytest.raises(ValueError, match="mieri mimo"):
            build_mesh(SQUARE_VERTICES, np.array([[0, 1, 99]], dtype=np.uint32))

    def test_an_empty_mesh_is_valid(self) -> None:
        assert empty_mesh().is_empty is True


class TestNormals:
    def test_a_square_in_the_z_plane_has_normals_up(self) -> None:
        normals = compute_vertex_normals(SQUARE_VERTICES, SQUARE_INDICES)

        assert normals == pytest.approx(np.tile([0.0, 0.0, 1.0], (4, 1)), abs=1e-6)

    def test_the_normals_are_unit_length(self) -> None:
        mesh = build_mesh(
            np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32),
            np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], dtype=np.uint32),
        )

        lengths = np.linalg.norm(mesh.normals, axis=1)

        assert lengths == pytest.approx(np.ones(4), abs=1e-6)

    def test_obratene_poradie_obrati_normalu(self) -> None:
        # This is exactly what the winding fix for TopAbs_REVERSED faces is for.
        flipped = compute_vertex_normals(SQUARE_VERTICES, SQUARE_INDICES[:, ::-1])

        assert flipped == pytest.approx(np.tile([0.0, 0.0, -1.0], (4, 1)), abs=1e-6)

    def test_osamoteny_vrchol_dostane_nahradnu_normalu(self) -> None:
        # A zero normal would give a black face in the scene.
        vertices = np.vstack([SQUARE_VERTICES, [[5, 5, 5]]]).astype(np.float32)

        normals = compute_vertex_normals(vertices, SQUARE_INDICES)

        assert normals[4] == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)

    def test_a_shared_vertex_averages_the_adjacent_faces(self) -> None:
        # Two faces of a right angle → the shared vertex normal should run along the diagonal.
        vertices = np.array(
            [[0, 0, 0], [1, 0, 0], [1, 1, 0], [1, 0, 1], [0, 0, 1]], dtype=np.float32
        )
        indices = np.array([[0, 1, 2], [0, 4, 3]], dtype=np.uint32)

        normals = compute_vertex_normals(vertices, indices)

        assert np.linalg.norm(normals[0]) == pytest.approx(1.0, abs=1e-6)


class TestWritingAndReading:
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
        # The cache must not end up with float64 in one place and float32 in another
        # depending on where the data came from — Panda3D reads the buffer byte by byte.
        path = tmp_path / "mesh.npz"
        write_mesh(path, build_mesh(SQUARE_VERTICES.astype(np.float64), SQUARE_INDICES))

        loaded = read_mesh(path)

        assert loaded.vertices.dtype == np.float32
        assert loaded.indices.dtype == np.uint32

    def test_the_write_creates_a_missing_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "hlbsie" / "este" / "mesh.npz"

        write_mesh(path, square())

        assert path.is_file()

    def test_the_write_leaves_no_temporary_file(self, tmp_path: Path) -> None:
        write_mesh(tmp_path / "mesh.npz", square())

        assert list(tmp_path.glob("*.tmp")) == []

    def test_a_missing_file_is_an_error_too(self, tmp_path: Path) -> None:
        with pytest.raises(CacheError, match="cannot be read"):
            read_mesh(tmp_path / "nic.npz")

    def test_a_damaged_file_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "mesh.npz"
        path.write_bytes(b"toto nie je npz")

        with pytest.raises(CacheError):
            read_mesh(path)

    def test_an_old_format_version_is_an_error(self, tmp_path: Path) -> None:
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

        with pytest.raises(CacheError, match="format version"):
            read_mesh(path)


class TestDerivedValues:
    def test_pocty(self) -> None:
        mesh = square()

        assert (mesh.vertex_count, mesh.triangle_count) == (4, 2)

    def test_bounding_box(self) -> None:
        low, high = square().bounding_box()

        assert low == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
        assert high == pytest.approx((1.0, 1.0, 0.0), abs=1e-6)

    def test_the_bounding_box_of_an_empty_mesh_is_zero(self) -> None:
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

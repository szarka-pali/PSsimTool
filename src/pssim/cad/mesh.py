"""The neutral format of tessellated geometry.

`cad/` must know nothing about Panda3D, so a `.bam` cannot be written into the cache.
The format is therefore `.npz` (a numpy archive): vertices, normals and triangle indices.

Why not glTF (the original intention in docs/architecture.md R2):

- glTF would add a dependency on `trimesh`, and in Panda3D on the `panda3d-gltf` loader
  plugin — two extra moving parts in a path that needs to be dependable
- `.npz` is read by numpy, which is in the project already, and `viz/` builds a `Geom`
  from it directly, with no conversion
- the format is fully testable in `tests/unit/` — without OpenCASCADE and without Panda3D

The price is that the mesh cannot be opened in Blender. The original STEP is there for that.

Units: vertices are **in metres**, the conversion happens at import.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from pssim.domain.errors import CacheError

MESH_FORMAT_VERSION: Final = 1
"""Bump this on an incompatible format change. It is part of the file, not of the cache
key — `IMPORTER_VERSION` in `cache.py` is what invalidates the cache."""

_REQUIRED_ARRAYS: Final = ("vertices", "normals", "indices")


@dataclass(frozen=True, slots=True)
class MeshData:
    """The triangle mesh of one part.

    - `vertices` — `(N, 3) float32`, in metres
    - `normals` — `(N, 3) float32`, unit length, one per vertex
    - `indices` — `(M, 3) uint32`, 0-based (OCC is 1-based, the conversion is at import)
    """

    vertices: np.ndarray
    normals: np.ndarray
    indices: np.ndarray

    def __post_init__(self) -> None:
        if self.vertices.ndim != 2 or self.vertices.shape[1] != 3:
            raise ValueError(f"vertices must have shape (N, 3), they have {self.vertices.shape}")
        if self.normals.shape != self.vertices.shape:
            raise ValueError(
                f"normals {self.normals.shape} must have the same shape as vertices "
                f"{self.vertices.shape} - one normal per vertex"
            )
        if self.indices.ndim != 2 or self.indices.shape[1] != 3:
            raise ValueError(f"indices must have shape (M, 3), they have {self.indices.shape}")
        if len(self.indices) and int(self.indices.max()) >= len(self.vertices):
            raise ValueError(
                f"index {int(self.indices.max())} mieri mimo {len(self.vertices)} vrcholov"
            )

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def triangle_count(self) -> int:
        return len(self.indices)

    @property
    def is_empty(self) -> bool:
        return self.triangle_count == 0

    def bounding_box(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """The min and max corner in metres. Used as a sanity check after an import.

        An empty mesh returns two zeros — the caller should handle that rather than
        rely on it.
        """
        if not len(self.vertices):
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        low = self.vertices.min(axis=0)
        high = self.vertices.max(axis=0)
        return (float(low[0]), float(low[1]), float(low[2])), (
            float(high[0]),
            float(high[1]),
            float(high[2]),
        )


def empty_mesh() -> MeshData:
    """An empty mesh — for nodes with no geometry of their own."""
    return MeshData(
        vertices=np.zeros((0, 3), dtype=np.float32),
        normals=np.zeros((0, 3), dtype=np.float32),
        indices=np.zeros((0, 3), dtype=np.uint32),
    )


def build_mesh(
    vertices: np.ndarray,
    indices: np.ndarray,
    normals: np.ndarray | None = None,
) -> MeshData:
    """Assemble a `MeshData` and compute the normals if they are not given.

    The types are unified to `float32`/`uint32` — without that, the cache would end up
    with `float64` in one place and `float32` in another depending on where the data
    came from.
    """
    vertices = np.ascontiguousarray(vertices, dtype=np.float32)
    indices = np.ascontiguousarray(indices, dtype=np.uint32)

    # The index range has to be checked NOW, not in `MeshData.__post_init__`:
    # computing the normals indexes the vertex array, and a bad index would crash
    # on an unhelpful IndexError before it ever reached the readable check.
    if len(indices) and int(indices.max()) >= len(vertices):
        raise ValueError(f"index {int(indices.max())} mieri mimo {len(vertices)} vrcholov")

    resolved = (
        compute_vertex_normals(vertices, indices)
        if normals is None
        else np.ascontiguousarray(normals, dtype=np.float32)
    )
    return MeshData(vertices=vertices, normals=resolved, indices=indices)


def compute_vertex_normals(vertices: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Vertex normals as the area-weighted average of the adjacent triangles' normals.

    The area weighting is free: an unnormalised cross product has a magnitude
    proportional to the triangle's area, so it is enough not to normalise it before
    summing.

    An isolated vertex (in no triangle) or a degenerate triangle gives a zero normal —
    that is replaced with `+Z`, so no black faces appear in the scene.
    """
    normals = np.zeros(vertices.shape, dtype=np.float64)
    if len(indices):
        corner_a = vertices[indices[:, 0]]
        corner_b = vertices[indices[:, 1]]
        corner_c = vertices[indices[:, 2]]
        face_normals = np.cross(corner_b - corner_a, corner_c - corner_a)

        # np.add.at, not `normals[idx] += ...` — with a repeated index the assignment
        # would overwrite instead of accumulating and shared vertices would be wrong.
        for corner in range(3):
            np.add.at(normals, indices[:, corner], face_normals)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    degenerate = lengths[:, 0] == 0.0
    normals[degenerate] = (0.0, 0.0, 1.0)
    lengths[degenerate] = 1.0
    return np.ascontiguousarray(normals / lengths, dtype=np.float32)


def write_mesh(path: str | Path, mesh: MeshData) -> None:
    """Write a mesh atomically.

    Atomically because an interrupted write would leave a file that exists but cannot be
    read — and that is worse than a missing file.
    """
    file_path = Path(path)
    temporary = file_path.with_suffix(".npz.tmp")
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as handle:
            np.savez_compressed(
                handle,
                version=np.array([MESH_FORMAT_VERSION], dtype=np.int32),
                vertices=mesh.vertices,
                normals=mesh.normals,
                indices=mesh.indices,
            )
        temporary.replace(file_path)
    except OSError as exc:
        raise CacheError(f"{file_path}: the mesh cannot be written: {exc}") from exc


def read_mesh(path: str | Path) -> MeshData:
    """Read a mesh from the cache."""
    file_path = Path(path)
    try:
        with np.load(file_path) as archive:
            missing = [name for name in _REQUIRED_ARRAYS if name not in archive]
            if missing:
                raise CacheError(f"{file_path}: the mesh file is missing {', '.join(missing)}")

            version = int(archive["version"][0]) if "version" in archive else 0
            if version != MESH_FORMAT_VERSION:
                raise CacheError(
                    f"{file_path}: the mesh is in format version {version}, "
                    f"the current one is {MESH_FORMAT_VERSION}. Delete the cache and import again."
                )

            return MeshData(
                vertices=np.ascontiguousarray(archive["vertices"], dtype=np.float32),
                normals=np.ascontiguousarray(archive["normals"], dtype=np.float32),
                indices=np.ascontiguousarray(archive["indices"], dtype=np.uint32),
            )
    except OSError as exc:
        raise CacheError(f"{file_path}: the mesh cannot be read: {exc}") from exc
    except ValueError as exc:
        raise CacheError(f"{file_path}: the mesh file is damaged: {exc}") from exc

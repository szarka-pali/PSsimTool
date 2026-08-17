"""Neutrálny formát tesselovanej geometrie.

`cad/` o Panda3D vedieť nesmie, takže do cache sa nedá zapísať `.bam`. Formát
je preto `.npz` (numpy archív): vrcholy, normály a indexy trojuholníkov.

Prečo nie glTF (pôvodný zámer v docs/architecture.md R2):

- glTF by pridal závislosť na `trimesh` a v Panda3D na loader plugin
  `panda3d-gltf` — dva pohyblivé diely navyše v ceste, ktorú potrebujeme mať
  spoľahlivú
- `.npz` číta numpy, ktorý už v projekte je, a `viz/` z neho postaví `Geom`
  priamo, bez konverzie
- formát je plne testovateľný v `tests/unit/` — bez OpenCASCADE aj bez Panda3D

Cena za to je, že mesh sa nedá otvoriť v Blenderi. Na to je tu pôvodný STEP.

Jednotky: vrcholy sú **v metroch**, prevod sa deje pri importe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from pssim.domain.errors import CacheError

MESH_FORMAT_VERSION: Final = 1
"""Zvýš pri nekompatibilnej zmene formátu. Je súčasťou súboru, nie cache kľúča —
`IMPORTER_VERSION` v `cache.py` je to, čo invaliduje cache."""

_REQUIRED_ARRAYS: Final = ("vertices", "normals", "indices")


@dataclass(frozen=True, slots=True)
class MeshData:
    """Trojuholníková sieť jedného dielu.

    - `vertices` — `(N, 3) float32`, v metroch
    - `normals` — `(N, 3) float32`, jednotkové, jedna na vrchol
    - `indices` — `(M, 3) uint32`, 0-based (OCC je 1-based, prevod je pri importe)
    """

    vertices: np.ndarray
    normals: np.ndarray
    indices: np.ndarray

    def __post_init__(self) -> None:
        if self.vertices.ndim != 2 or self.vertices.shape[1] != 3:
            raise ValueError(f"vertices musia mať tvar (N, 3), majú {self.vertices.shape}")
        if self.normals.shape != self.vertices.shape:
            raise ValueError(
                f"normals {self.normals.shape} musia mať rovnaký tvar ako vertices "
                f"{self.vertices.shape} — jedna normála na vrchol"
            )
        if self.indices.ndim != 2 or self.indices.shape[1] != 3:
            raise ValueError(f"indices musia mať tvar (M, 3), majú {self.indices.shape}")
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
        """Min a max roh v metroch. Slúži na sanity kontrolu po importe.

        Prázdny mesh vráti dve nuly — volajúci to má ošetriť, nie sa spoliehať.
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
    """Prázdna sieť — pre uzly bez vlastnej geometrie."""
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
    """Zloží `MeshData` a dopočíta normály, ak nie sú zadané.

    Typy sa zjednotia na `float32`/`uint32` — bez toho by sa do cache dostali
    raz `float64` a raz `float32` podľa toho, odkiaľ dáta prišli.
    """
    vertices = np.ascontiguousarray(vertices, dtype=np.float32)
    indices = np.ascontiguousarray(indices, dtype=np.uint32)

    # Rozsah indexov sa musí overiť TERAZ, nie až v `MeshData.__post_init__`:
    # výpočet normál indexuje pole vrcholov a pri zlom indexe by spadol na
    # neužitočnom IndexError skôr, než sa dostane k zrozumiteľnej kontrole.
    if len(indices) and int(indices.max()) >= len(vertices):
        raise ValueError(f"index {int(indices.max())} mieri mimo {len(vertices)} vrcholov")

    resolved = (
        compute_vertex_normals(vertices, indices)
        if normals is None
        else np.ascontiguousarray(normals, dtype=np.float32)
    )
    return MeshData(vertices=vertices, normals=resolved, indices=indices)


def compute_vertex_normals(vertices: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Normály vrcholov ako plošne vážený priemer normál priľahlých trojuholníkov.

    Váženie plochou je zadarmo: nenormalizovaný vektorový súčin má veľkosť
    úmernú ploche trojuholníka, takže stačí ho nenormalizovať pred sčítaním.

    Osamotený vrchol (v žiadnom trojuholníku) alebo degenerovaný trojuholník
    dá nulovú normálu — nahrádza sa `+Z`, aby v scéne nevznikli čierne plochy.
    """
    normals = np.zeros(vertices.shape, dtype=np.float64)
    if len(indices):
        corner_a = vertices[indices[:, 0]]
        corner_b = vertices[indices[:, 1]]
        corner_c = vertices[indices[:, 2]]
        face_normals = np.cross(corner_b - corner_a, corner_c - corner_a)

        # np.add.at, nie `normals[idx] += ...` — pri opakovanom indexe by sa
        # priradenie prepísalo namiesto sčítania a zdieľané vrcholy by boli zle.
        for corner in range(3):
            np.add.at(normals, indices[:, corner], face_normals)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    degenerate = lengths[:, 0] == 0.0
    normals[degenerate] = (0.0, 0.0, 1.0)
    lengths[degenerate] = 1.0
    return np.ascontiguousarray(normals / lengths, dtype=np.float32)


def write_mesh(path: str | Path, mesh: MeshData) -> None:
    """Zapíše mesh atomicky.

    Atomicky preto, že prerušený zápis by zanechal súbor, ktorý existuje,
    ale nedá sa načítať — a to je horšie než chýbajúci súbor.
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
        raise CacheError(f"{file_path}: mesh sa nedá zapísať: {exc}") from exc


def read_mesh(path: str | Path) -> MeshData:
    """Načíta mesh z cache."""
    file_path = Path(path)
    try:
        with np.load(file_path) as archive:
            missing = [name for name in _REQUIRED_ARRAYS if name not in archive]
            if missing:
                raise CacheError(f"{file_path}: v mesh súbore chýba {', '.join(missing)}")

            version = int(archive["version"][0]) if "version" in archive else 0
            if version != MESH_FORMAT_VERSION:
                raise CacheError(
                    f"{file_path}: mesh je vo formáte verzie {version}, "
                    f"aktuálna je {MESH_FORMAT_VERSION}. Zmaž cache a importuj znovu."
                )

            return MeshData(
                vertices=np.ascontiguousarray(archive["vertices"], dtype=np.float32),
                normals=np.ascontiguousarray(archive["normals"], dtype=np.float32),
                indices=np.ascontiguousarray(archive["indices"], dtype=np.uint32),
            )
    except OSError as exc:
        raise CacheError(f"{file_path}: mesh sa nedá prečítať: {exc}") from exc
    except ValueError as exc:
        raise CacheError(f"{file_path}: mesh súbor je poškodený: {exc}") from exc

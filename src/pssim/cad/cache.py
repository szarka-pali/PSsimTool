"""The cache of tessellated geometry.

Tessellating a large assembly takes minutes, so it is done once (`pssim import-step`)
and the result is cached. See docs/architecture.md R2.

The cache is **entirely disposable**: nothing that cannot be rebuilt from `models/` and
`machines/` belongs in it.

The module is pure (no OCP, no Panda3D) and fully testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pssim.cad.model import CadAssembly, CadNode
from pssim.domain.errors import CacheError

IMPORTER_VERSION: Final = 2
"""Bump this on every change to the importer that changes its output.

Without that, the old cache is used silently and nobody understands why the change had
no effect. The version is part of the cache key.

History:
  1 — the first version, assembly tree only, no geometry
  2 — geometry written out (.npz), the mesh shared between instances of the same part
"""

_METADATA_FILENAME: Final = "meta.json"
_HASH_CHUNK_BYTES: Final = 1 << 20


@dataclass(frozen=True, slots=True)
class CacheKey:
    """The identity of one import.

    The key covers the file content, the tessellation parameters and the importer
    version. A change to any of them must lead to a different cache directory.
    """

    source_sha256: str
    scale_to_m: float
    linear_deflection_mm: float
    angular_deflection_rad: float
    importer_version: int = IMPORTER_VERSION

    @property
    def digest(self) -> str:
        """The short hash used as the directory name."""
        payload = json.dumps(
            {
                "source": self.source_sha256,
                "scale": self.scale_to_m,
                "linear": self.linear_deflection_mm,
                "angular": self.angular_deflection_rad,
                "version": self.importer_version,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class CacheMetadata:
    """The contents of `meta.json`. Without it the cache cannot be interpreted."""

    key: CacheKey
    source_file: str
    units_used: str
    assembly: CadAssembly

    def to_dict(self) -> dict[str, Any]:
        return {
            "importer_version": self.key.importer_version,
            "source_file": self.source_file,
            "source_sha256": self.key.source_sha256,
            "units_used": self.units_used,
            "scale_to_m": self.key.scale_to_m,
            "tessellation": {
                "linear_deflection_mm": self.key.linear_deflection_mm,
                "angular_deflection_rad": self.key.angular_deflection_rad,
            },
            "roots": list(self.assembly.roots),
            "nodes": [node.to_dict() for node in self.assembly.nodes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheMetadata:
        try:
            tessellation = data["tessellation"]
            key = CacheKey(
                source_sha256=str(data["source_sha256"]),
                scale_to_m=float(data["scale_to_m"]),
                linear_deflection_mm=float(tessellation["linear_deflection_mm"]),
                angular_deflection_rad=float(tessellation["angular_deflection_rad"]),
                importer_version=int(data["importer_version"]),
            )
            return cls(
                key=key,
                source_file=str(data["source_file"]),
                units_used=str(data["units_used"]),
                assembly=CadAssembly(
                    nodes=tuple(CadNode.from_dict(node) for node in data["nodes"]),
                    roots=tuple(str(root) for root in data.get("roots", ())),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CacheError(f"meta.json is damaged or incomplete: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """The cache directory of one specific import."""

    root: Path
    key: CacheKey

    @property
    def directory(self) -> Path:
        return self.root / self.key.digest

    @property
    def metadata_path(self) -> Path:
        return self.directory / _METADATA_FILENAME

    @property
    def exists(self) -> bool:
        return self.metadata_path.is_file()

    def mesh_path(self, mesh: str) -> Path:
        return self.directory / mesh

    def read(self) -> CacheMetadata:
        """Read the metadata. Verifies that the cache belongs to this key."""
        if not self.exists:
            raise CacheError(
                f"no cache exists for this import: {self.directory}. "
                f"Run `uv run pssim import-step <file.step>`."
            )
        try:
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CacheError(f"{self.metadata_path}: cannot be read: {exc}") from exc

        metadata = CacheMetadata.from_dict(raw)
        if metadata.key.importer_version != self.key.importer_version:
            raise CacheError(
                f"{self.metadata_path}: the cache is from importer version "
                f"{metadata.key.importer_version}, the current one is {self.key.importer_version}. "
                f"Delete the cache directory and import again."
            )
        return metadata

    def write(self, metadata: CacheMetadata) -> None:
        """Write the metadata atomically.

        Atomically because an interrupted write would leave a cache that "exists" but
        cannot be read — and that is worse than no cache at all.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.metadata_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.metadata_path)
        except OSError as exc:
            raise CacheError(f"{self.metadata_path}: cannot be written: {exc}") from exc


def file_sha256(path: str | Path) -> str:
    """The hash of a file's content. Read in blocks — STEP files run to hundreds of MB."""
    file_path = Path(path)
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise CacheError(f"{file_path}: cannot be read: {exc}") from exc
    return digest.hexdigest()


def mesh_filename(mesh_key: str) -> str:
    """A safe file name for the geometry of one part **definition**.

    `mesh_key` is the XCAF entry (`0:1:1:3`), not a node path — instances of the same
    part therefore point at one shared file. An assembly with a thousand screws has one
    screw in the cache, not a thousand copies.

    The key may contain characters not used in file names (`:`), so it is sanitised. The
    short hash on the end guarantees that two different keys do not end up in the same
    file after sanitising.
    """
    slug = "".join(char if char.isalnum() or char in "-_" else "_" for char in mesh_key)
    suffix = hashlib.sha256(mesh_key.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:60]}_{suffix}.npz"

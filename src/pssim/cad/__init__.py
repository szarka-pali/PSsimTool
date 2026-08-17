"""Import CAD geometrie.

Pravidlá sú v `.claude/rules/cad-import.md`, postup OpenCASCADE volaní
v `.claude/skills/domenovy-kontext/referencie/step-import.md`.

`OCP` je ťažký import (stovky MB) — importuje sa vnútri funkcií, nikdy na module
level, aby unit testy a `pssim --help` neplatili jeho načítanie.
"""

from pssim.cad.cache import IMPORTER_VERSION, CacheEntry, CacheKey, CacheMetadata
from pssim.cad.mesh import MeshData, read_mesh, write_mesh
from pssim.cad.model import CadNode

__all__ = [
    "IMPORTER_VERSION",
    "CacheEntry",
    "CacheKey",
    "CacheMetadata",
    "CadNode",
    "MeshData",
    "read_mesh",
    "write_mesh",
]

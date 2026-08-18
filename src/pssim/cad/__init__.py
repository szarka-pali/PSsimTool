"""CAD geometry import.

The rules are in `.claude/rules/cad-import.md`, the sequence of OpenCASCADE calls in
`.claude/skills/domenovy-kontext/referencie/step-import.md`.

`OCP` is a heavy import (hundreds of MB) — it is imported inside functions, never at
module level, so that the unit tests and `pssim --help` do not pay for loading it.
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

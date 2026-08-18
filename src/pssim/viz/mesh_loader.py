"""Converting `cad.mesh.MeshData` into a Panda3D `GeomNode`.

This is the only place where the neutral format from the cache meets Panda3D. `cad/` knows
nothing about Panda3D and `domain/` even less — see docs/architecture.md.

The data is copied into the buffers **in one go through `copyDataFrom`**, not row by row
through `GeomVertexWriter`. On an assembly with a million triangles the difference is
between seconds and minutes; that is exactly why the cache format is a numpy array and not
text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from pssim.cad.mesh import MeshData, read_mesh
from pssim.observability import get_logger

logger = get_logger(__name__)


def geom_node_from_mesh(mesh: MeshData, name: str) -> Any:
    """Build a `GeomNode` from a mesh.

    An empty mesh gives an empty `GeomNode` — the caller can attach it to the scene
    without a further check.
    """
    from panda3d.core import (
        Geom,
        GeomEnums,
        GeomNode,
        GeomTriangles,
        GeomVertexData,
        GeomVertexFormat,
    )

    node = GeomNode(name)
    if mesh.is_empty:
        return node

    # V3n3 = position + normal, both 3× float32, interleaved in one array.
    vertex_data = GeomVertexData(name, GeomVertexFormat.getV3n3(), Geom.UHStatic)
    vertex_data.setNumRows(mesh.vertex_count)
    interleaved = np.hstack([mesh.vertices, mesh.normals]).astype(np.float32)
    vertex_data.modifyArray(0).modifyHandle().copyDataFrom(interleaved.tobytes())

    primitive = GeomTriangles(Geom.UHStatic)
    # Without NTUint32 the indices are truncated to 16 bits and a part with more than
    # 65 535 vertices falls apart into a meaningless tangle of triangles.
    primitive.setIndexType(GeomEnums.NTUint32)
    primitive.modifyVertices().modifyHandle().copyDataFrom(
        np.ascontiguousarray(mesh.indices, dtype=np.uint32).tobytes()
    )
    primitive.closePrimitive()

    geom = Geom(vertex_data)
    geom.addPrimitive(primitive)
    node.addGeom(geom)
    return node


def load_geom_node(mesh_path: str | Path, name: str) -> Any | None:
    """Read a mesh from the cache and build a `GeomNode` from it.

    Returns `None` if the file is missing or damaged. A missing mesh **must not bring down
    application startup** — the rest of the machine should be displayed and the user gets a
    warning in the log, not a traceback.
    """
    path = Path(mesh_path)
    if not path.is_file():
        logger.warning("mesh missing from the cache", node=name, file=str(path))
        return None

    try:
        return geom_node_from_mesh(read_mesh(path), name)
    except Exception:
        logger.exception("mesh cannot be read, skipping it", node=name, file=str(path))
        return None

"""Prevod `cad.mesh.MeshData` na Panda3D `GeomNode`.

Toto je jediné miesto, kde sa neutrálny formát z cache stretáva s Panda3D.
`cad/` o Panda3D nevie a `domain/` už vôbec — viď docs/architecture.md.

Dáta sa do bufferov kopírujú **naraz cez `copyDataFrom`**, nie po riadkoch cez
`GeomVertexWriter`. Pri zostave s miliónom trojuholníkov je rozdiel medzi
sekundami a minútami; práve preto je formát cache numpy pole a nie textový.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from pssim.cad.mesh import MeshData, read_mesh
from pssim.observability import get_logger

logger = get_logger(__name__)


def geom_node_from_mesh(mesh: MeshData, name: str) -> Any:
    """Postaví `GeomNode` z meshu.

    Prázdny mesh dá prázdny `GeomNode` — volajúci ho môže pripojiť do scény
    bez ďalšej kontroly.
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

    # V3n3 = pozícia + normála, oboje 3× float32, prekladané v jednom poli.
    vertex_data = GeomVertexData(name, GeomVertexFormat.getV3n3(), Geom.UHStatic)
    vertex_data.setNumRows(mesh.vertex_count)
    interleaved = np.hstack([mesh.vertices, mesh.normals]).astype(np.float32)
    vertex_data.modifyArray(0).modifyHandle().copyDataFrom(interleaved.tobytes())

    primitive = GeomTriangles(Geom.UHStatic)
    # Bez NTUint32 sa indexy orežú na 16 bitov a diel nad 65 535 vrcholov
    # sa rozsype na nezmyselnú spleť trojuholníkov.
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
    """Načíta mesh z cache a postaví z neho `GeomNode`.

    Vracia `None`, ak súbor chýba alebo je poškodený. Chýbajúci mesh **nesmie
    zhodiť štart aplikácie** — zvyšok stroja sa má zobraziť a používateľ dostane
    varovanie v logu, nie traceback.
    """
    path = Path(mesh_path)
    if not path.is_file():
        logger.warning("mesh chýba v cache", node=name, file=str(path))
        return None

    try:
        return geom_node_from_mesh(read_mesh(path), name)
    except Exception:
        logger.exception("mesh sa nedá načítať, preskakujem", node=name, file=str(path))
        return None

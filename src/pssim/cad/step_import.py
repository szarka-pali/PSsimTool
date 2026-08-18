"""STEP import through OpenCASCADE (XCAF).

Verified against `cadquery-ocp 7.9.3` on `tests/data/fixture.step` —
`tests/integration/test_step_import.py` (marker `cad`). **Always run those tests after
upgrading OCP**; the bindings change in details between versions.

The procedure, the traps in the bindings and known pathologies of real STEP files:
`.claude/skills/domenovy-kontext/referencie/step-import.md`

The module has two parts:

- a **pure** one (`build_paths`, `scale_transform`, `cache_key_for`) — no OCP, tested in
  `tests/unit/cad/`
- an **OCP-dependent** one (`read_step_assembly` and the `_` functions below it) — tested
  by integration against the fixture file
"""

# OCP is a package of re-export shims (`OCP.TDF` → `OCP.OCP.TDF`) that pyright cannot
# see through. At run time everything resolves correctly — the tests with the `cad`
# marker prove it. That is why the check is disabled for the whole file rather than ad
# hoc on individual lines.
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from pssim.cad.cache import CacheEntry, CacheKey, CacheMetadata, file_sha256, mesh_filename
from pssim.cad.mesh import MeshData, build_mesh, empty_mesh, write_mesh
from pssim.cad.model import DEFAULT_COLOR, CadAssembly, CadNode
from pssim.domain.errors import CadImportError
from pssim.domain.machine import Transform
from pssim.observability import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ImportSettings:
    """The parameters of one import. All of them go into the cache key."""

    step_file: Path
    scale_to_m: float
    units: str
    linear_deflection_mm: float = 0.5
    angular_deflection_rad: float = 0.35


@dataclass(frozen=True, slots=True)
class RawNode:
    """A node as OCP returned it — before a stable path has been assigned.

    The separation from `CadNode` is deliberate: composing paths is pure logic with
    tests, whereas reading from OCP cannot be tested in unit tests.

    `mesh_key` is the identity of the part **definition** (the XCAF entry of the label),
    not of the instance. The same part used ten times has ten `RawNode`s with the same
    `mesh_key`, so the geometry is written into the cache once. Without that, an assembly
    with a thousand screws would have a thousand copies of the same screw in the cache.
    """

    name: str
    transform: Transform
    color: tuple[float, float, float, float] = DEFAULT_COLOR
    children: tuple[RawNode, ...] = ()
    triangle_count: int = 0
    mesh_key: str | None = None

    @property
    def has_geometry(self) -> bool:
        return self.mesh_key is not None


# -- the pure part: stable paths -------------------------------------------


def build_paths(roots: tuple[RawNode, ...]) -> CadAssembly:
    """Assign stable paths to the nodes and return a flat assembly.

    The index `[n]` (1-based) is added **only** to siblings of the same name — a single
    `Portal` stays `base/Portal`, ten `Part1`s become `base/Part1[1]`..`[10]`.

    The order is taken as it came from `GetComponents()`. Never iterate over a `dict` or
    a `set` — the paths have to be deterministic between imports, or `machines/*.yaml`
    breaks after every reimport.
    """
    nodes: list[CadNode] = []
    root_paths = _walk_level(roots, prefix="", collected=nodes)
    return CadAssembly(nodes=tuple(nodes), roots=root_paths)


def _walk_level(
    level: tuple[RawNode, ...], prefix: str, collected: list[CadNode]
) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for node in level:
        counts[node.name] = counts.get(node.name, 0) + 1

    seen: dict[str, int] = {}
    paths: list[str] = []

    for node in level:
        if counts[node.name] > 1:
            seen[node.name] = seen.get(node.name, 0) + 1
            segment = f"{node.name}[{seen[node.name]}]"
        else:
            segment = node.name

        path = f"{prefix}/{segment}" if prefix else segment
        child_paths = _walk_level(node.children, prefix=path, collected=collected)

        collected.append(
            CadNode(
                path=path,
                transform=node.transform,
                # The mesh name is derived from the DEFINITION, not from the path — so
                # instances of the same part point at one shared file.
                mesh=mesh_filename(node.mesh_key) if node.mesh_key is not None else None,
                color=node.color,
                children=child_paths,
                triangle_count=node.triangle_count,
            )
        )
        paths.append(path)

    return tuple(paths)


def scale_transform(transform: Transform, scale_to_m: float) -> Transform:
    """Rescale the translation part of a transformation.

    Rotation is not scaled. Forget to scale the translation and the vertices shrink while
    the offsets do not — the model falls apart into pieces kilometres away from each other.
    """
    x, y, z = transform.xyz
    return Transform(xyz=(x * scale_to_m, y * scale_to_m, z * scale_to_m), rpy=transform.rpy)


def cache_key_for(settings: ImportSettings) -> CacheKey:
    return CacheKey(
        source_sha256=file_sha256(settings.step_file),
        scale_to_m=settings.scale_to_m,
        linear_deflection_mm=settings.linear_deflection_mm,
        angular_deflection_rad=settings.angular_deflection_rad,
    )


# -- the OCP-dependent part -------------------------------------------------


def import_step(
    settings: ImportSettings, cache_root: Path, *, force: bool = False
) -> CacheMetadata:
    """Import a STEP into the cache and return the metadata.

    If a cache for the given key already exists, it is read instead (unless
    `force=True`).
    """
    if not settings.step_file.is_file():
        raise CadImportError(f"STEP súbor neexistuje: {settings.step_file}")

    entry = CacheEntry(root=cache_root, key=cache_key_for(settings))
    if entry.exists and not force:
        logger.info("cache is up to date, skipping the import", directory=str(entry.directory))
        return entry.read()

    logger.info(
        "importing STEP (this may take minutes)",
        file=str(settings.step_file),
        units=settings.units,
    )

    roots, meshes = read_step(settings)
    assembly = build_paths(roots)

    metadata = CacheMetadata(
        key=entry.key,
        source_file=str(settings.step_file),
        units_used=settings.units,
        assembly=assembly,
    )
    entry.write(metadata)
    _write_meshes(entry, meshes)

    logger.info(
        "import finished",
        nodes=len(assembly.nodes),
        meshes=len(meshes),
        triangles=assembly.triangle_count,
        directory=str(entry.directory),
    )
    return metadata


def _write_meshes(entry: CacheEntry, meshes: dict[str, MeshData]) -> None:
    """Write the geometry into the cache — one file per part definition."""
    for mesh_key, mesh in meshes.items():
        if mesh.is_empty:
            continue
        write_mesh(entry.mesh_path(mesh_filename(mesh_key)), mesh)


def read_step_assembly(settings: ImportSettings) -> tuple[RawNode, ...]:
    """Read a STEP and return the roots of the assembly tree, without geometry.

    A thin wrapper over `read_step()` for the cases where the geometry is not needed
    (validating a definition, diagnostics).
    """
    return read_step(settings)[0]


def read_step(settings: ImportSettings) -> tuple[tuple[RawNode, ...], dict[str, MeshData]]:
    """Read a STEP: the assembly tree + geometry keyed by part definition.

    Verified against `tests/data/fixture.step` (OCP 7.9.3) —
    `tests/integration/test_step_import.py`.
    """
    doc = _open_document(settings.step_file)
    color_tool = _color_tool(doc)

    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    if free_shapes.Length() == 0:
        raise CadImportError(
            f"{settings.step_file}: dokument neobsahuje žiadny shape. "
            f"Súbor je prázdny alebo sa nepodaril transfer."
        )

    # Geometry is tessellated once per part definition; instances share it.
    meshes: dict[str, MeshData] = {}
    roots = tuple(
        _read_label(free_shapes.Value(index), color_tool, settings, meshes)
        for index in range(1, free_shapes.Length() + 1)  # OCC indexuje od 1
    )
    return roots, meshes


def _open_document(step_file: Path) -> Any:
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application

    # The document must be created through XCAFApp_Application, otherwise it has no
    # initialised XCAF attributes and the shape/color tools do not work on it.
    application = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("pssim"))
    application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)

    # OCC does not report errors as exceptions — the return code MUST be tested,
    # or you get an empty document and no error.
    status = reader.ReadFile(str(step_file))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise CadImportError(f"{step_file}: STEP sa nedá prečítať (status {status})")
    if not reader.Transfer(doc):
        raise CadImportError(f"{step_file}: transfer do XCAF dokumentu zlyhal")

    return doc


def _color_tool(doc: Any) -> Any:
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    return XCAFDoc_DocumentTool.ColorTool_s(doc.Main())


def _read_label(
    label: Any,
    color_tool: Any,
    settings: ImportSettings,
    meshes: dict[str, MeshData],
) -> RawNode:
    """Read one label of the assembly tree recursively.

    The key thing: **an instance (component) and a definition (referred shape) are two
    different labels.** The name and the geometry sit on the definition, the position on
    the instance. The same part used ten times has one definition and ten instances.

    `meshes` is filled in as we go — every definition is tessellated only once.
    """
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    transform = scale_transform(_label_transform(label), settings.scale_to_m)
    definition = _referred_label(label)
    name = _label_name(definition) or _label_name(label) or f"Unnamed_{label.Tag()}"
    # The colour may sit on the instance as well as on the definition; the instance
    # wins, because the same part may appear twice in an assembly in different colours.
    color = (
        _shape_color(XCAFDoc_ShapeTool.GetShape_s(label), color_tool)
        or _shape_color(XCAFDoc_ShapeTool.GetShape_s(definition), color_tool)
        or DEFAULT_COLOR
    )

    if XCAFDoc_ShapeTool.IsAssembly_s(definition):
        components = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetComponents_s(definition, components)
        children = tuple(
            _read_label(components.Value(index), color_tool, settings, meshes)
            for index in range(1, components.Length() + 1)
        )
        return RawNode(name=name, transform=transform, color=color, children=children)

    mesh_key = _label_entry(definition)
    mesh = meshes.get(mesh_key)
    if mesh is None:
        mesh = _tessellate(definition, settings)
        meshes[mesh_key] = mesh

    return RawNode(
        name=name,
        transform=transform,
        color=color,
        triangle_count=mesh.triangle_count,
        mesh_key=None if mesh.is_empty else mesh_key,
    )


def _label_entry(label: Any) -> str:
    """The XCAF entry of a label (`0:1:1:3`) — a stable identifier of a definition in the document.

    Serves as the key of a shared mesh. It is stable within one loading of the document,
    which is enough — the cache is invalidated by the hash of the input file anyway.
    """
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool

    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return entry.ToCString()


def _referred_label(label: Any) -> Any:
    """The definition a label refers to. For a non-instance it returns the label itself."""
    from OCP.TDF import TDF_Label
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    if not XCAFDoc_ShapeTool.IsReference_s(label):
        return label
    referred = TDF_Label()
    if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred):
        return referred
    return label


def _label_name(label: Any) -> str | None:
    """The name of a node, or `None` when the label has none.

    A part without a name is common in real files — the caller must have a fallback.
    """
    from OCP.TDataStd import TDataStd_Name

    attribute = TDataStd_Name()
    if not label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return None
    name = attribute.Get().ToExtString().strip()
    return name or None


def _label_transform(label: Any) -> Transform:
    """The transformation of an instance relative to its parent: translation and rotation.

    The rotation is pulled out of the `gp_Trsf` as a quaternion and converted into
    roll/pitch/yaw in Intrinsic XYZ order — that is the convention of
    `domain.machine.Transform.rpy`.
    """
    from OCP.gp import gp_EulerSequence
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    trsf = XCAFDoc_ShapeTool.GetLocation_s(label).Transformation()
    translation = trsf.TranslationPart()
    roll, pitch, yaw = trsf.GetRotation().GetEulerAngles(gp_EulerSequence.gp_Intrinsic_XYZ)

    return Transform(
        xyz=(translation.X(), translation.Y(), translation.Z()),
        rpy=(roll, pitch, yaw),
    )


def _shape_color(shape: Any, color_tool: Any) -> tuple[float, float, float, float] | None:
    """The colour of a shape, or `None` when it has none.

    `XCAFDoc_ColorTool.GetColor` in OCP 7.9 takes a **shape, not a label** — despite
    what the OCCT documentation says.
    """
    from OCP.Quantity import Quantity_Color
    from OCP.XCAFDoc import XCAFDoc_ColorType

    if shape.IsNull():
        return None

    color = Quantity_Color()
    for color_type in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
        if color_tool.GetColor(shape, color_type, color):
            return (color.Red(), color.Green(), color.Blue(), 1.0)
    return None


def _tessellate(label: Any, settings: ImportSettings) -> MeshData:
    """Tessellate a shape and return its geometry in metres.

    Walks every face of the shape and glues them into one mesh. Vertices are **not**
    shared between faces — every face contributes its own. For mechanical parts that is
    correct: on the edge of a box the adjacent faces have different normals, and a shared
    vertex would average them into a rounded look.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    shape = XCAFDoc_ShapeTool.GetShape_s(label)
    if shape.IsNull():
        return empty_mesh()

    # linear_deflection is in the model's units (typically mm), not in metres.
    BRepMesh_IncrementalMesh(
        shape,
        settings.linear_deflection_mm,
        False,
        settings.angular_deflection_rad,
        True,
    )

    vertex_blocks: list[np.ndarray] = []
    index_blocks: list[np.ndarray] = []
    vertex_offset = 0
    skipped_faces = 0

    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    while explorer.More():
        location = TopLoc_Location()
        # Explorer vracia TopoDS_Shape; Triangulation_s chce TopoDS_Face.
        face = TopoDS.Face_s(explorer.Current())
        triangulation = BRep_Tool.Triangulation_s(face, location)

        # None is a COMMON case: degenerate faces are normal in real files.
        if triangulation is None:
            skipped_faces += 1
            explorer.Next()
            continue

        transformation = location.Transformation()
        node_count = triangulation.NbNodes()
        vertices = np.empty((node_count, 3), dtype=np.float64)
        for index in range(1, node_count + 1):  # OCC indexuje od 1
            point = triangulation.Node(index).Transformed(transformation)
            vertices[index - 1] = (point.X(), point.Y(), point.Z())

        triangle_count = triangulation.NbTriangles()
        indices = np.empty((triangle_count, 3), dtype=np.int64)
        for index in range(1, triangle_count + 1):
            first, second, third = triangulation.Triangle(index).Get()
            indices[index - 1] = (first - 1, second - 1, third - 1)

        # A reversed face has a reversed vertex order. Without this correction the
        # normals would point inwards and the part would look inside out in the scene.
        if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            indices = indices[:, ::-1]

        vertex_blocks.append(vertices * settings.scale_to_m)
        index_blocks.append(indices + vertex_offset)
        vertex_offset += node_count
        explorer.Next()

    if skipped_faces:
        logger.debug("faces without triangulation skipped", count=skipped_faces)

    if not vertex_blocks:
        return empty_mesh()

    return build_mesh(
        vertices=np.concatenate(vertex_blocks),
        indices=np.concatenate(index_blocks),
    )

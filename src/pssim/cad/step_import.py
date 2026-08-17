"""Import STEP cez OpenCASCADE (XCAF).

Overené proti `cadquery-ocp 7.9.3` na `tests/data/fixture.step` —
`tests/integration/test_step_import.py` (marker `cad`). **Po povýšení OCP tie
testy vždy spusti**, bindings sa medzi verziami menia v detailoch.

Postup, pasce v bindings a známe patológie reálnych STEP súborov:
`.claude/skills/domenovy-kontext/referencie/step-import.md`

Modul má dve časti:

- **čistú** (`build_paths`, `scale_transform`, `cache_key_for`) — bez OCP,
  testovaná v `tests/unit/cad/`
- **OCP-závislú** (`read_step_assembly` a `_`-funkcie pod ňou) — testovaná
  integračne proti fixture súboru
"""

# OCP je balík re-exportných shimov (`OCP.TDF` → `OCP.OCP.TDF`), cez ktoré
# pyright nevidí. Za behu sa všetko resolvuje správne — dokazujú to testy
# s markerom `cad`. Preto je kontrola vypnutá pre celý súbor, nie ad hoc
# na jednotlivých riadkoch.
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
    """Parametre jedného importu. Všetky vstupujú do cache kľúča."""

    step_file: Path
    scale_to_m: float
    units: str
    linear_deflection_mm: float = 0.5
    angular_deflection_rad: float = 0.35


@dataclass(frozen=True, slots=True)
class RawNode:
    """Uzol tak, ako ho vrátilo OCP — pred pridelením stabilnej cesty.

    Oddelenie od `CadNode` je zámerné: skladanie ciest je čistá logika s testami,
    zatiaľ čo čítanie z OCP testovať v unit testoch nejde.

    `mesh_key` je identita **definície** dielu (XCAF entry labelu), nie inštancie.
    Ten istý diel použitý desaťkrát má desať `RawNode` s rovnakým `mesh_key`,
    takže sa geometria do cache zapíše raz. Bez toho by zostava s tisíckou
    skrutiek mala v cache tisíc kópií tej istej skrutky.
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


# -- čistá časť: stabilné cesty -------------------------------------------


def build_paths(roots: tuple[RawNode, ...]) -> CadAssembly:
    """Priradí uzlom stabilné cesty a vráti plochý assembly.

    Index `[n]` (1-based) sa pridáva **len** rovnomenným siblingom — jeden
    `Portal` zostane `base/Portal`, desať `Part1` bude `base/Part1[1]`..`[10]`.

    Poradie sa berie tak, ako prišlo z `GetComponents()`. Nikdy neiteruj cez
    `dict` ani `set` — cesty musia byť medzi importmi deterministické, inak
    sa `machines/*.yaml` rozbije po každom reimporte.
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
                # Názov meshu sa odvodzuje od DEFINÍCIE, nie od cesty — inštancie
                # toho istého dielu tak ukazujú na jeden zdieľaný súbor.
                mesh=mesh_filename(node.mesh_key) if node.mesh_key is not None else None,
                color=node.color,
                children=child_paths,
                triangle_count=node.triangle_count,
            )
        )
        paths.append(path)

    return tuple(paths)


def scale_transform(transform: Transform, scale_to_m: float) -> Transform:
    """Preškáluje translačnú časť transformácie.

    Rotácia sa neškáluje. Ak sa zabudne škálovať translácia, vrcholy sa zmenšia
    a offsety nie — model sa rozsype na kusy vzdialené kilometre.
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


# -- časť závislá od OCP ---------------------------------------------------


def import_step(
    settings: ImportSettings, cache_root: Path, *, force: bool = False
) -> CacheMetadata:
    """Naimportuje STEP do cache a vráti metadáta.

    Ak cache pre daný kľúč už existuje, načíta sa (pokiaľ nie je `force=True`).
    """
    if not settings.step_file.is_file():
        raise CadImportError(f"STEP súbor neexistuje: {settings.step_file}")

    entry = CacheEntry(root=cache_root, key=cache_key_for(settings))
    if entry.exists and not force:
        logger.info("cache je aktuálna, preskakujem import", directory=str(entry.directory))
        return entry.read()

    logger.info(
        "importujem STEP (môže to trvať minúty)",
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
        "import hotový",
        nodes=len(assembly.nodes),
        meshes=len(meshes),
        triangles=assembly.triangle_count,
        directory=str(entry.directory),
    )
    return metadata


def _write_meshes(entry: CacheEntry, meshes: dict[str, MeshData]) -> None:
    """Zapíše geometriu do cache — jeden súbor na definíciu dielu."""
    for mesh_key, mesh in meshes.items():
        if mesh.is_empty:
            continue
        write_mesh(entry.mesh_path(mesh_filename(mesh_key)), mesh)


def read_step_assembly(settings: ImportSettings) -> tuple[RawNode, ...]:
    """Prečíta STEP a vráti korene assembly tree, bez geometrie.

    Tenký obal nad `read_step()` pre prípady, keď geometria netreba
    (validácia definície, diagnostika).
    """
    return read_step(settings)[0]


def read_step(settings: ImportSettings) -> tuple[tuple[RawNode, ...], dict[str, MeshData]]:
    """Prečíta STEP: assembly tree + geometria kľúčovaná podľa definície dielu.

    Overené proti `tests/data/fixture.step` (OCP 7.9.3) —
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

    # Geometria sa tesseluje raz na definíciu dielu; inštancie ju zdieľajú.
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

    # Dokument musí vzniknúť cez XCAFApp_Application, inak nemá inicializované
    # XCAF atribúty a shape/color tool nad ním nefungujú.
    application = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("pssim"))
    application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)

    # OCC nehlási chyby výnimkami — návratový kód sa MUSÍ testovať,
    # inak dostaneš prázdny dokument a žiadnu chybu.
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
    """Prečíta jeden label assembly tree rekurzívne.

    Kľúčová vec: **inštancia (component) a definícia (referred shape) sú dva
    rôzne labely.** Meno a geometria sedia na definícii, poloha na inštancii.
    Ten istý diel použitý desaťkrát má jednu definíciu a desať inštancií.

    `meshes` sa priebežne dopĺňa — každá definícia sa tesseluje len raz.
    """
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    transform = scale_transform(_label_transform(label), settings.scale_to_m)
    definition = _referred_label(label)
    name = _label_name(definition) or _label_name(label) or f"Unnamed_{label.Tag()}"
    # Farba môže sedieť na inštancii aj na definícii; inštancia má prednosť,
    # lebo ten istý diel môže byť v zostave dvakrát v rôznych farbách.
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
    """XCAF entry labelu (`0:1:1:3`) — stabilný identifikátor definície v dokumente.

    Slúži ako kľúč zdieľaného meshu. Je stabilný v rámci jedného načítania
    dokumentu, čo stačí — cache sa aj tak invaliduje hashom vstupného súboru.
    """
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool

    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return entry.ToCString()


def _referred_label(label: Any) -> Any:
    """Definícia, na ktorú label odkazuje. Pre neinštanciu vráti label samotný."""
    from OCP.TDF import TDF_Label
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    if not XCAFDoc_ShapeTool.IsReference_s(label):
        return label
    referred = TDF_Label()
    if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred):
        return referred
    return label


def _label_name(label: Any) -> str | None:
    """Názov uzla, alebo `None` ak ho label nemá.

    Diel bez názvu je v reálnych súboroch bežný — volajúci musí mať fallback.
    """
    from OCP.TDataStd import TDataStd_Name

    attribute = TDataStd_Name()
    if not label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return None
    name = attribute.Get().ToExtString().strip()
    return name or None


def _label_transform(label: Any) -> Transform:
    """Transformácia inštancie voči rodičovi: posun aj rotácia.

    Rotácia sa z `gp_Trsf` vytiahne ako kvaternión a prevedie na roll/pitch/yaw
    v poradí Intrinsic XYZ — to je konvencia `domain.machine.Transform.rpy`.
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
    """Farba shapu, alebo `None` ak žiadnu nemá.

    `XCAFDoc_ColorTool.GetColor` v OCP 7.9 prijíma **shape, nie label** —
    napriek tomu, čo hovorí dokumentácia OCCT.
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
    """Tesseluje shape a vráti jeho geometriu v metroch.

    Prechádza všetky plochy shapu a zlepí ich do jednej siete. Vrcholy sa medzi
    plochami **nezdieľajú** — každá plocha prispeje vlastnými. Pre strojárske
    diely je to správne: na hrane kvádra majú susedné steny rôzne normály
    a zdieľaný vrchol by ich spriemeroval do zaobleného vzhľadu.
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

    # linear_deflection je v jednotkách modelu (typicky mm), nie v metroch.
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

        # None je BEŽNÝ prípad: degenerované plochy sú v reálnych súboroch normálne.
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

        # Obrátená plocha má obrátené poradie vrcholov. Bez tejto opravy by
        # normály mierili dovnútra a diel by v scéne vyzeral „naruby".
        if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            indices = indices[:, ::-1]

        vertex_blocks.append(vertices * settings.scale_to_m)
        index_blocks.append(indices + vertex_offset)
        vertex_offset += node_count
        explorer.Next()

    if skipped_faces:
        logger.debug("plochy bez triangulácie preskočené", count=skipped_faces)

    if not vertex_blocks:
        return empty_mesh()

    return build_mesh(
        vertices=np.concatenate(vertex_blocks),
        indices=np.concatenate(index_blocks),
    )

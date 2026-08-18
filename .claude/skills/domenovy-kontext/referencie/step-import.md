# Importing STEP through OpenCASCADE

A reference document. Load it when working on `src/pssim/cad/`.

> **Verified against `cadquery-ocp 7.9.3.1.1`** (OCCT 7.9) on `tests/data/fixture.step`.
> Tests: `uv run pytest -m cad`. If you upgrade OCP, **run those tests before you believe
> this document** — the bindings change in details between versions.

## Traps in the OCP bindings that cost time

These are the concrete things that did not match the OCCT documentation or intuition.
If something throws a `TypeError` or an `ImportError`, look here first.

| What you would expect | What it actually is (OCP 7.9) |
|---|---|
| `STEPCAFControlReader` | **`STEPCAFControl_Reader`** — with an underscore. Likewise `STEPCAFControl_Writer`. |
| `shape_tool.IsAssembly(label)` | **`XCAFDoc_ShapeTool.IsAssembly_s(label)`** — static. Likewise `GetComponents_s`, `GetLocation_s`, `GetShape_s`, `IsReference_s`, `GetReferredShape_s`. |
| `shape_tool.GetFreeShapes(seq)` | an instance method, **without** `_s`. Inconsistent with the line above, but that is how it is. |
| `color_tool.GetColor(label, ...)` | takes a **`TopoDS_Shape`, not a `TDF_Label`** — despite the OCCT documentation. Call `GetShape_s(label)` first. |
| `BRep_Tool.Triangulation_s(shape, loc)` | wants a **`TopoDS_Face`**. `TopExp_Explorer.Current()` returns a `TopoDS_Shape` → cast with **`TopoDS.Face_s(...)`**. |
| `TDocStd_Document(...)` is enough | **it is not.** The document must go through `XCAFApp_Application.GetApplication_s().NewDocument("MDTV-XCAF", doc)`, otherwise it has no XCAF attributes and the tools find nothing in it. |
| static methods have `_s` | true in `OCP`, **not true in `pythonocc-core`**. If something is not found, try both. |

The rule that follows: **do not write more than one unverified call at a time.** Print
`dir(Class)` and `Class.method.__doc__` — pybind11 generates the complete signatures of every
overload into the docstring.

## Why CAF and not the plain reader

| | `STEPControl_Reader` | `STEPCAFControlReader` |
|---|---|---|
| Geometry | yes | yes |
| Assembly tree with names | **no** | yes |
| Instance transformations | fused into the shape | yes, separately |
| Colours and materials | no | yes |
| Suitable for PSsimTool | **no** | yes |

Without the assembly tree the joints could not be mapped onto parts — you would have to
identify them from their geometry, which is not doable.

## The procedure

```python
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPCAFControl import STEPCAFControl_Reader  # CAREFUL: with an underscore
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool

# 1) the XCAF document — it MUST go through XCAFApp_Application, otherwise it has
#    no initialised XCAF attributes and the tools find nothing in it.
application = XCAFApp_Application.GetApplication_s()
doc = TDocStd_Document(TCollection_ExtendedString("pssim"))
application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

# 2) reading
reader = STEPCAFControl_Reader()
reader.SetColorMode(True)
reader.SetNameMode(True)
reader.SetLayerMode(True)
status = reader.ReadFile(str(path))  # wants a str, not a Path
if status != IFSelect_ReturnStatus.IFSelect_RetDone:
    raise CadImportError(...)
if not reader.Transfer(doc):
    raise CadImportError(...)

# 3) the tools for walking the tree
shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
```

Return codes are **not reported as exceptions** — you have to test them yourself, otherwise
you get an empty document and no error.

## Walking the assembly tree

```python
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.XCAFDoc import XCAFDoc_ShapeTool

free_shapes = TDF_LabelSequence()
shape_tool.GetFreeShapes(free_shapes)  # an instance method, WITHOUT _s

# The rest are STATIC (called on the class, not on the shape_tool instance):
XCAFDoc_ShapeTool.IsAssembly_s(label)  # has children
XCAFDoc_ShapeTool.GetComponents_s(label, seq)  # the children
XCAFDoc_ShapeTool.IsReference_s(label)  # it is an instance of another shape
XCAFDoc_ShapeTool.GetReferredShape_s(label, out)  # what it refers to (out = TDF_Label)
XCAFDoc_ShapeTool.GetLocation_s(label)  # the TopLoc_Location of this instance
XCAFDoc_ShapeTool.GetShape_s(label)  # TopoDS_Shape
```

The key thing to understand: **an instance (component) and a definition (referred shape) are
two different labels.** The same part used ten times has one definition and ten instances,
each with its own `TopLoc_Location`.

In practice that means splitting the sources:

| Datum | Take it from |
|---|---|
| position relative to the parent | the **instance** (`GetLocation_s(component_label)`) |
| name | the **definition** (`GetReferredShape_s` → `TDataStd_Name`) |
| geometry | the **definition** |
| colour | the instance, falling back to the definition |

If you read the name from the instance you get nothing but `Unnamed_*` — instances usually
have no name. That is exactly why `instanceTo()` can be used in Panda3D, and why you have to
generate **stable paths**, not just names.

## Rotation from a transformation

`TopLoc_Location.Transformation()` gives a `gp_Trsf`. From it:

```python
from OCP.gp import gp_EulerSequence

translation = trsf.TranslationPart()  # gp_XYZ, .X()/.Y()/.Z()
roll, pitch, yaw = trsf.GetRotation().GetEulerAngles(gp_EulerSequence.gp_Intrinsic_XYZ)
```

`GetRotation()` returns a `gp_Quaternion`. The order `gp_Intrinsic_XYZ` matches the
convention of `domain.machine.Transform.rpy` — change it and every existing
`machines/*.yaml` falls apart.

Scale **only the translation part**. Angles are dimensionless.

## Stable node paths

`machines/*.yaml` refers to nodes by path. The format:

```
base/portal/Carriage[2]/Bolt[5]
```

- segments separated by `/`
- `[n]` is a **1-based index among siblings of the same name**, added only when there is more
  than one of that name
- the path must be **deterministic between imports** — take the order from `GetComponents()`,
  never from `dict` iteration or from a `set`

Duplicate names are common in real assemblies (`Part1` ten times). Without indexing, the YAML
would refer to an ambiguous node.

## Tessellation

```python
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)
# arguments: (shape, theLinDeflection, isRelative, theAngDeflection, isInParallel)

explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
while explorer.More():
    # Current() returns a TopoDS_Shape, Triangulation_s wants a TopoDS_Face → cast it.
    face = TopoDS.Face_s(explorer.Current())
    loc = TopLoc_Location()
    triangulation = BRep_Tool.Triangulation_s(face, loc)
    if triangulation is None:  # a COMMON case, not an error
        explorer.Next()
        continue
    # ... vertices: triangulation.Node(i), i from 1 to NbNodes()
    # ... triangles: triangulation.Triangle(i), indices are 1-based too
    explorer.Next()
```

- **Indices in OCC are 1-based.** This is the most common off-by-one in the whole import.
- `Triangulation_s()` may return `None` — degenerate faces are common in real files.
- Vertices are in the face's local coordinates; `loc.Transformation()` has to be applied.
- The face orientation (`face.Orientation()`) decides whether the triangle's index order has
  to be flipped. Forget it and the normals point the wrong way and the model looks inside out.
- `linear_deflection` is in the model's units, so typically **millimetres** — not metres.

## Units

The STEP header contains a unit, but not always a trustworthy one. The procedure:

1. Try to read it from the file.
2. If that fails, take `units:` from `machines/*.yaml`.
3. **Write the one you finally used into the cache metadata.**

Scale **the translation part of the transformations too**, not only the vertices. If the
vertices shrink by a factor of 1000 and the offsets do not, the model falls apart into pieces
kilometres away from each other.

## The cache format

```
assets/cache/<hash>/
  meta.json          required, see below
  <path-slug>.bam    one mesh per shape definition (not per instance)
```

`meta.json`:

```json
{
  "importer_version": 1,
  "source_file": "models/priklad.step",
  "source_sha256": "…",
  "units_used": "mm",
  "scale_to_m": 0.001,
  "tessellation": { "linear_deflection_mm": 0.5, "angular_deflection_rad": 0.35 },
  "nodes": [
    {
      "path": "base/portal",
      "mesh": "portal.bam",
      "transform": { "xyz": [0, 0, 0.15], "rpy": [0, 0, 0] },
      "color": [0.6, 0.6, 0.62, 1.0],
      "children": ["base/portal/Carriage[1]"]
    }
  ]
}
```

You **must bump** `importer_version` when you change the importer so that its output changes.
Otherwise the old cache is used silently and nobody understands why the change had no effect.

## Pathologies of real STEP files

Handle each of these explicitly and log it. Never crash with an `AttributeError` from the
depths of OCP — the user has no way of finding out what happened.

| Pathology | How it shows up | What to do |
|---|---|---|
| A face without triangulation | `Triangulation_s()` → `None` | skip it, log the count |
| A part without a name | an empty `TDataStd_Name` | `Unnamed_<tag>` |
| Duplicate sibling names | an ambiguous path | index them `[n]` |
| An empty compound | a shape with no faces | skip it, log it |
| A zero/degenerate transformation | the part sits at zero or disappears | log a warning, keep it |
| A part without a colour | `color_tool` returns nothing | default grey `[0.6, 0.6, 0.62, 1]` |
| Enormous coordinates (mm vs. m mix-up) | the machine is kilometres from the camera | check the bounding box, warn above 1000 m |
| An assembly wrapped in several levels of compounds | a needlessly deep tree | do not collapse it — paths must stay stable |
| Thousands of parts | tessellation takes minutes | which is why import is a separate command, not app startup |

## Alternatives that were considered and rejected

| Tool | Why not |
|---|---|
| FreeCAD headless | works, but drags in the whole GUI stack; the distribution would grow by gigabytes |
| `gmsh` | aimed at FEM meshes; preserves neither the assembly tree nor colours |
| `assimp` | does not support STEP at all |
| commercial SDKs (Datakit, HOOPS) | licence costs; STEP is sufficient for our case |
| `cadquery` / `build123d` (high level) | built on the same OCP, but they hide access to the XCAF assembly tree |

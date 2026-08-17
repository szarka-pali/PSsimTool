# Import STEP cez OpenCASCADE

Referenčný dokument. Načítaj ho, keď pracuješ na `src/pssim/cad/`.

> **Overené proti `cadquery-ocp 7.9.3.1.1`** (OCCT 7.9) na `tests/data/fixture.step`.
> Testy: `uv run pytest -m cad`. Ak povýšiš OCP, **prejdi tieto testy skôr,
> než uveríš tomuto dokumentu** — bindings sa medzi verziami menia v detailoch.

## Pasce v OCP bindings, ktoré stáli čas

Toto sú konkrétne veci, ktoré nesedeli s dokumentáciou OCCT ani s intuíciou.
Ak niečo hádže `TypeError` alebo `ImportError`, pozri sem najprv.

| Čakal by si | V skutočnosti (OCP 7.9) |
|---|---|
| `STEPCAFControlReader` | **`STEPCAFControl_Reader`** — s podtržníkom. Rovnako `STEPCAFControl_Writer`. |
| `shape_tool.IsAssembly(label)` | **`XCAFDoc_ShapeTool.IsAssembly_s(label)`** — statická. Rovnako `GetComponents_s`, `GetLocation_s`, `GetShape_s`, `IsReference_s`, `GetReferredShape_s`. |
| `shape_tool.GetFreeShapes(seq)` | inštančná, **bez** `_s`. Nekonzistentné s predchádzajúcim riadkom, ale je to tak. |
| `color_tool.GetColor(label, ...)` | prijíma **`TopoDS_Shape`, nie `TDF_Label`** — napriek dokumentácii OCCT. Najprv `GetShape_s(label)`. |
| `BRep_Tool.Triangulation_s(shape, loc)` | chce **`TopoDS_Face`**. `TopExp_Explorer.Current()` vracia `TopoDS_Shape` → pretypuj cez **`TopoDS.Face_s(...)`**. |
| `TDocStd_Document(...)` stačí | **nestačí.** Dokument musí prejsť cez `XCAFApp_Application.GetApplication_s().NewDocument("MDTV-XCAF", doc)`, inak nemá XCAF atribúty a nástroje nad ním nič nenájdu. |
| statické metódy majú `_s` | platí v `OCP`, **neplatí v `pythonocc-core`**. Ak sa niečo nenájde, skús obe. |

Pravidlo, ktoré z toho plynie: **nepíš viac než jedno neoverené volanie naraz.**
Vypíš si `dir(Trieda)` a `Trieda.metoda.__doc__` — pybind11 do docstringu generuje
kompletné signatúry všetkých preťažení.

## Prečo CAF a nie obyčajný reader

| | `STEPControl_Reader` | `STEPCAFControlReader` |
|---|---|---|
| Geometria | áno | áno |
| Assembly tree s názvami | **nie** | áno |
| Transformácie inštancií | zliate do shape | áno, samostatne |
| Farby a materiály | nie | áno |
| Vhodné pre PSsimTool | **nie** | áno |

Bez assembly tree by sa kĺby nedali namapovať na diely — musel by si ich identifikovať
podľa geometrie, čo je nerobiteľné.

## Postup

```python
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPCAFControl import STEPCAFControl_Reader  # POZOR: s podtržníkom
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool

# 1) XCAF dokument — MUSÍ prejsť cez XCAFApp_Application, inak nemá
#    inicializované XCAF atribúty a nástroje nad ním nič nenájdu.
application = XCAFApp_Application.GetApplication_s()
doc = TDocStd_Document(TCollection_ExtendedString("pssim"))
application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

# 2) načítanie
reader = STEPCAFControl_Reader()
reader.SetColorMode(True)
reader.SetNameMode(True)
reader.SetLayerMode(True)
status = reader.ReadFile(str(path))  # chce str, nie Path
if status != IFSelect_ReturnStatus.IFSelect_RetDone:
    raise CadImportError(...)
if not reader.Transfer(doc):
    raise CadImportError(...)

# 3) nástroje na prechádzanie
shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
```

Návratové kódy sa **nehlásia výnimkami** — musíš ich testovať sám, inak dostaneš
prázdny dokument a žiadnu chybu.

## Prechádzanie assembly tree

```python
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.XCAFDoc import XCAFDoc_ShapeTool

free_shapes = TDF_LabelSequence()
shape_tool.GetFreeShapes(free_shapes)  # inštančná, BEZ _s

# Ostatné sú STATICKÉ (volajú sa na triede, nie na inštancii shape_tool):
XCAFDoc_ShapeTool.IsAssembly_s(label)  # má potomkov
XCAFDoc_ShapeTool.GetComponents_s(label, seq)  # potomkovia
XCAFDoc_ShapeTool.IsReference_s(label)  # je to inštancia iného shape
XCAFDoc_ShapeTool.GetReferredShape_s(label, out)  # na čo odkazuje (out = TDF_Label)
XCAFDoc_ShapeTool.GetLocation_s(label)  # TopLoc_Location tejto inštancie
XCAFDoc_ShapeTool.GetShape_s(label)  # TopoDS_Shape
```

Kľúčová vec, ktorú treba pochopiť: **inštancia (component) a definícia (referred
shape) sú dva rôzne labely.** Ten istý diel použitý desaťkrát má jednu definíciu
a desať inštancií, každú s vlastnou `TopLoc_Location`.

Prakticky to znamená rozdelenie zdrojov:

| Údaj | Ber z |
|---|---|
| poloha voči rodičovi | **inštancia** (`GetLocation_s(component_label)`) |
| názov | **definícia** (`GetReferredShape_s` → `TDataStd_Name`) |
| geometria | **definícia** |
| farba | inštancia, s fallbackom na definíciu |

Ak čítaš názov z inštancie, dostaneš samé `Unnamed_*` — inštancie meno spravidla
nemajú. Práve preto sa dá v Panda3D použiť `instanceTo()` a preto musíš generovať
**stabilné cesty**, nie len názvy.

## Rotácia z transformácie

`TopLoc_Location.Transformation()` dá `gp_Trsf`. Z neho:

```python
from OCP.gp import gp_EulerSequence

translation = trsf.TranslationPart()  # gp_XYZ, .X()/.Y()/.Z()
roll, pitch, yaw = trsf.GetRotation().GetEulerAngles(gp_EulerSequence.gp_Intrinsic_XYZ)
```

`GetRotation()` vracia `gp_Quaternion`. Poradie `gp_Intrinsic_XYZ` zodpovedá
konvencii `domain.machine.Transform.rpy` — ak ho zmeníš, rozsypú sa všetky
existujúce `machines/*.yaml`.

Škáluj **len translačnú časť**. Uhly sú bezrozmerné.

## Stabilné cesty uzlov

`machines/*.yaml` sa odkazuje na uzly cestou. Formát:

```
base/portal/Carriage[2]/Bolt[5]
```

- segmenty oddelené `/`
- `[n]` je **1-based index medzi rovnomennými siblingami**, pridáva sa len ak je
  rovnomenných viac ako jeden
- cesta musí byť **deterministická medzi importmi** — poradie ber z `GetComponents()`,
  nikdy z `dict` iterácie ani zo `set`

Duplicitné názvy sú v reálnych zostavách bežné (`Part1` desaťkrát). Bez indexovania
by sa YAML odkazoval na nejednoznačný uzol.

## Tesselácia

```python
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)
# argumenty: (shape, theLinDeflection, isRelative, theAngDeflection, isInParallel)

explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
while explorer.More():
    # Current() vracia TopoDS_Shape, Triangulation_s chce TopoDS_Face → pretypuj.
    face = TopoDS.Face_s(explorer.Current())
    loc = TopLoc_Location()
    triangulation = BRep_Tool.Triangulation_s(face, loc)
    if triangulation is None:  # BEŽNÝ prípad, nie chyba
        explorer.Next()
        continue
    # ... vrcholy: triangulation.Node(i), i od 1 do NbNodes()
    # ... trojuholníky: triangulation.Triangle(i), indexy tiež 1-based
    explorer.Next()
```

- **Indexy v OCC sú 1-based.** Toto je najčastejší off-by-one v celom importe.
- `Triangulation_s()` môže vrátiť `None` — degenerované plochy sú v reálnych súboroch bežné.
- Vrcholy sú v lokálnych súradniciach plochy, treba aplikovať `loc.Transformation()`.
- Orientácia plochy (`face.Orientation()`) určuje, či treba obrátiť poradie indexov
  trojuholníka. Ak zabudneš, normály budú naopak a model bude vyzerať „naruby".
- `linear_deflection` je v jednotkách modelu, teda typicky **milimetroch** — nie v metroch.

## Jednotky

STEP hlavička obsahuje jednotku, ale nie vždy dôveryhodne. Postup:

1. Skús ju prečítať zo súboru.
2. Ak sa nedá, ber `units:` z `machines/*.yaml`.
3. **Zapíš do cache metadát, ktorú si nakoniec použil.**

Škáluj **aj translačnú časť transformácií** z assembly tree, nie len vrcholy.
Ak sa vrcholy zmenšia 1000× a offsety nie, model sa rozsype na kusy vzdialené kilometre.

## Formát cache

```
assets/cache/<hash>/
  meta.json          povinné, viď nižšie
  <path-slug>.bam    jeden mesh na definíciu shape (nie na inštanciu)
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

`importer_version` **musíš zvýšiť**, keď zmeníš importér tak, že sa mení výstup.
Inak sa bude ticho používať stará cache a nikto nepochopí, prečo sa zmena neprejavila.

## Patológie reálnych STEP súborov

Každú z týchto vecí ošetri explicitne a zaloguj. Nikdy nespadni s `AttributeError`
z hlbín OCP — používateľ nemá ako zistiť, čo sa stalo.

| Patológia | Ako sa prejaví | Čo s tým |
|---|---|---|
| Plocha bez triangulácie | `Triangulation_s()` → `None` | preskoč, zaloguj počet |
| Diel bez názvu | prázdny `TDataStd_Name` | `Unnamed_<tag>` |
| Duplicitné názvy siblingov | nejednoznačná cesta | indexuj `[n]` |
| Prázdny compound | shape bez plôch | preskoč, zaloguj |
| Nulová/degenerovaná transformácia | diel v nule alebo zmizne | zaloguj varovanie, zachovaj |
| Diel bez farby | `color_tool` nič nevráti | default šedá `[0.6, 0.6, 0.62, 1]` |
| Obrovské súradnice (mm vs. m zámena) | stroj kilometre od kamery | skontroluj bounding box, varuj nad 1000 m |
| Assembly zabalené vo viacerých úrovniach compoundov | zbytočne hlboký strom | nekolabuj — cesty musia zostať stabilné |
| Tisíce dielov | tesselácia trvá minúty | preto je import samostatný príkaz, nie štart appky |

## Alternatívy, ktoré boli zvážené a zamietnuté

| Nástroj | Prečo nie |
|---|---|
| FreeCAD headless | funguje, ale ťahá celý GUI stack; distribúcia by narástla o gigabajty |
| `gmsh` | mieri na FEM meshe; assembly tree ani farby nezachová |
| `assimp` | STEP nepodporuje vôbec |
| komerčné SDK (Datakit, HOOPS) | licenčné náklady; STEP je pre náš prípad dostatočný |
| `cadquery` / `build123d` (vysokoúrovňové) | postavené na tom istom OCP, ale skrývajú prístup k XCAF assembly tree |

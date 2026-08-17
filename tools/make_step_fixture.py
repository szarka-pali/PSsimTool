"""Vygeneruje malý STEP fixture pre testy importu.

Spustenie::

    uv run python tools/make_step_fixture.py

Výsledok (`tests/data/fixture.step`) je vo verzovaní — je malý a testy ho
potrebujú. Reálne CAD súbory v repozitári nie sú, viď `models/README.md`.

Zostava je zámerne postavená tak, aby obsahovala presne tie prípady, ktoré
robia problém pri reálnych súboroch:

    base                     assembly, koreň
      portal                 assembly, posunutý o 100 mm v X
        Part1                dva rovnomenní siblingovia → indexovanie [1]/[2]
        Part1
        hlava                otočený diel → overuje rotáciu z gp_Trsf
      kryt                   jednoduchý diel s farbou

Rozmery sú v milimetroch, aby sa dal otestovať prevod na metre.
"""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "data" / "fixture.step"

#: Posun portálu v mm. Testy overujú, že po importe je z toho 0.1 m.
PORTAL_OFFSET_MM = 100.0

#: Otočenie hlavy okolo Z v radiánoch (90°).
HEAD_ROTATION_RAD = 1.5707963267948966


def build() -> None:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    application = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("pssim-fixture"))
    application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def named_solid(label_name: str, dx: float, dy: float, dz: float):
        box = BRepPrimAPI_MakeBox(dx, dy, dz).Shape()
        label = shape_tool.AddShape(box, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(label_name))
        return label

    def assembly(label_name: str):
        label = shape_tool.NewShape()
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(label_name))
        return label

    def translation(x: float, y: float, z: float) -> TopLoc_Location:
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(x, y, z))
        return TopLoc_Location(trsf)

    def rotation_z(angle_rad: float) -> TopLoc_Location:
        trsf = gp_Trsf()
        trsf.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)), angle_rad)
        return TopLoc_Location(trsf)

    # Definície dielov (každá sa dá inštancovať viackrát).
    part_def = named_solid("Part1", 20.0, 20.0, 20.0)
    head_def = named_solid("hlava", 30.0, 10.0, 10.0)
    cover_def = named_solid("kryt", 200.0, 5.0, 80.0)

    # Zostava.
    root = assembly("base")
    portal = assembly("portal")

    # Dvaja rovnomenní siblingovia — kvôli tomu existuje indexovanie [n].
    shape_tool.AddComponent(portal, part_def, translation(0.0, 0.0, 0.0))
    shape_tool.AddComponent(portal, part_def, translation(0.0, 40.0, 0.0))
    shape_tool.AddComponent(portal, head_def, rotation_z(HEAD_ROTATION_RAD))

    shape_tool.AddComponent(root, portal, translation(PORTAL_OFFSET_MM, 0.0, 0.0))
    cover_instance = shape_tool.AddComponent(root, cover_def, translation(0.0, -40.0, 0.0))

    # Farba len na jednom dieli — zvyšok musí dostať default. Diel bez farby
    # je v reálnych súboroch úplne bežný.
    color_tool.SetColor(
        cover_instance,
        Quantity_Color(0.2, 0.4, 0.8, Quantity_TOC_RGB),
        XCAFDoc_ColorType.XCAFDoc_ColorSurf,
    )

    shape_tool.UpdateAssemblies()

    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    if not writer.Transfer(doc):
        raise SystemExit("Transfer do STEP writeru zlyhal")

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    status = writer.Write(str(FIXTURE_PATH))

    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise SystemExit(f"zápis STEP zlyhal so statusom {status}")

    size_kb = FIXTURE_PATH.stat().st_size / 1024
    print(f"zapísané: {FIXTURE_PATH} ({size_kb:.1f} kB)")


if __name__ == "__main__":
    try:
        build()
    except ImportError as exc:
        raise SystemExit(f"chýba OpenCASCADE — spusti `uv sync --extra cad`\n{exc}") from exc
    sys.exit(0)

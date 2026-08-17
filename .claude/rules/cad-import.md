---
paths:
  - "src/pssim/cad/**"
description: Pravidlá pre import CAD geometrie (STEP, OpenCASCADE, tesselácia)
---

# Vrstva `cad/` — import geometrie

## Čo použiť

- STEP čítame cez **`STEPCAFControlReader` + `XCAFDoc`** (balík `cadquery-ocp`),
  **nie** cez `STEPControl_Reader`. Rozdiel: CAF verzia dá assembly tree s názvami,
  transformáciami a farbami. Bez toho nemáš na čo namapovať kĺby.
- Tesselácia: `BRepMesh_IncrementalMesh` s explicitne zadanou lineárnou aj uhlovou
  deviáciou. Nikdy sa nespoliehaj na default.
- `OCP` je ťažký import (stovky MB). Importuj ho **vnútri funkcie**, nie na module level —
  inak `pssim --help` a unit testy platia jeho načítanie.

## Jednotky

- STEP je najčastejšie v **milimetroch**, scéna je v **metroch**. Škáluj **pri importe**,
  nikdy neskôr.
- Jednotku čítaj zo súboru, ak ju obsahuje; ak nie, ber ju z `units:` v `machines/*.yaml`
  a to, ktorú si použil, **zapíš do cache metadát**. Inak nikto nezistí, prečo je stroj
  tisíckrát väčší.
- Transformácie z assembly tree sú tiež v jednotkách STEP — škáluj aj ich translačnú časť,
  nie len vrcholy.

## Cache

- Cache kľúč = hash z **(obsah STEP súboru + parametre tesselácie + verzia importéra)**.
  Ak zmeníš importér tak, že sa mení výstup, **zvýš `IMPORTER_VERSION`** — inak sa bude
  používať stará cache a nikto nepochopí, prečo sa zmena neprejavila.
- Cache je **plne zahoditeľná**. Nikdy do nej neukladaj nič, čo sa nedá znovu vyrobiť
  zo `models/` a `machines/`.
- Do cache patria aj metadáta (JSON): pôvodný súbor, jednotky, parametre, assembly tree,
  mapovanie názvov uzlov na mesh súbory. Bez nich je cache neinterpretovateľná.

## Geometria

- Formát meshu je `.npz` (`cad/mesh.py`), **nie** `.bam` ani glTF — dôvody v
  `docs/architecture.md` R2b. Vrcholy sú v **metroch**, indexy **0-based**
  (OCC je 1-based, prevod je pri importe).
- Mesh sa kľúčuje podľa **definície dielu** (XCAF entry), nie podľa cesty uzla.
  Inštancie toho istého dielu zdieľajú jeden súbor.
- **Vrcholy sa medzi plochami nezdieľajú.** Na hrane kvádra majú susedné steny rôzne
  normály; zdieľaný vrchol by ich spriemeroval a diel by vyzeral zaoblene.
- Orientácia plochy (`TopAbs_REVERSED`) určuje poradie indexov trojuholníka.
  Ak sa zabudne obrátiť, normály mieria dovnútra a diel vyzerá „naruby".
  Stráži to `test_normaly_kvadra_mieria_von`.
- Meniť konvenciu uhlov (`gp_Intrinsic_XYZ`) sa nesmie bez prepísania
  `viz/transforms.rpy_to_quat` — rozsypali by sa všetky existujúce `machines/*.yaml`.

## Výkon

- Tesselácia veľkého assembly trvá **minúty**. Nikdy ju nespúšťaj počas štartu aplikácie —
  len cez `pssim import-step`. Ak cache chýba, aplikácia to má **ohlásiť ako chybu**
  s návodom, nie sa na 5 minút zaseknúť.
- Deviáciu voľ podľa veľkosti dielu, nie absolútne — jemná deviácia na 3-metrovom ráme
  vygeneruje milióny trojuholníkov.
- Diely, ktoré nie sú kĺbom ani jeho potomkom, sa dajú v exporte spojiť.
  Rozhodnutie čo je pohyblivé patrí do `viz/scene_builder.py`, `cad/` len dodá suroviny
  a zachová hierarchiu.

## Robustnosť

- Reálne STEP súbory z praxe sú **rozbité**: prázdne shapes, nulové transformácie,
  duplicitné názvy uzlov, cyklické referencie, diely bez farby. Každý z týchto prípadov
  ošetri explicitne a zaloguj — nikdy nespadni s `AttributeError` z hlbín OCP.
- Duplicitné názvy uzlov sú bežné (`Part1` desaťkrát). Generuj stabilné cesty
  (`base/portal/Part1[2]`), nie len názvy — YAML sa na ne odkazuje.
- Ak sa uzol z `machines/*.yaml` v assembly nenájde, je to `ConfigError` s výpisom
  **podobných** dostupných názvov. Nie tiché ignorovanie.

## Testovanie

- Do `tests/unit/` patrí **cache logika, hashovanie, škálovanie jednotiek a spracovanie
  assembly tree** — všetko čisté funkcie nad dátovými štruktúrami.
- Volania OCP patria do `tests/integration/test_step_import.py` (marker `cad`).
  Bežia proti `tests/data/fixture.step`, ktorý generuje `tools/make_step_fixture.py`.
  Reálne súbory z `models/` v repozitári nie sú.
- Ak potrebuješ do fixture pridať ďalší prípad (viac úrovní vnorenia, diel bez názvu,
  zrkadlená inštancia), **rozšír generátor a fixture pregeneruj** — needituj STEP ručne.
- **Po povýšení `cadquery-ocp` vždy spusti `uv run pytest -m cad`.** Bindings sa medzi
  verziami menia v detailoch a tieto testy sú jediné, čo to zachytí.

# Architektúra PSsimTool

Tento dokument popisuje **prečo** je systém rozdelený tak, ako je. Konkrétne konvencie
sú v `CLAUDE.md` a `.claude/rules/`.

## Tok dát

```
   PLC (OPC UA server)                          .step súbor
          │                                          │
          │ subscription (MonitoredItem)             │ jednorazovo, offline
          ▼                                          ▼
  ┌───────────────────┐                     ┌─────────────────────┐
  │ io/opcua_source   │                     │ cad/step_import     │
  │ vlákno B, asyncio │                     │ OCP: STEPCAF reader │
  └─────────┬─────────┘                     │  → tesselácia       │
            │ put(signal, value, t)         │  → assembly tree    │
            ▼                               └──────────┬──────────┘
  ┌───────────────────┐                                │ .bam / .gltf
  │ io/store          │  latest-value + ring buffer    ▼
  │ StateStore (lock) │                     ┌─────────────────────┐
  └─────────┬─────────┘                     │ assets/cache/       │
            │ sample_all(t)                 └──────────┬──────────┘
            ▼  vlákno A, 60 fps                        │
  ┌───────────────────────────────────────────────────┐│
  │ viz/app: Panda3D task                             ││
  │  1. prečítaj snapshot z StateStore                │◄┘
  │  2. domain/kinematics: hodnota → JointPose        │
  │  3. NodePath.setPosQuat()                         │
  └───────────────────────────────────────────────────┘
```

`machines/*.yaml` viaže tieto dva svety: hovorí, ktorý **uzol assembly** je ktorý **kĺb**
a ktorý **OPC UA node** ho riadi.

## Vrstvy a prečo sú oddelené

| Vrstva | Zodpovednosť | Smie importovať |
|---|---|---|
| `domain/` | model stroja, kinematika, interpolácia, jednotky, chyby | len stdlib |
| `config/` | YAML schéma, validácia, preklad do `domain` | `domain`, pydantic, yaml |
| `io/` | zdroje dát a ich životný cyklus, thread-safe store | `domain`, `config`, asyncua |
| `cad/` | STEP → mesh, cache | `domain`, OCP, trimesh, numpy |
| `viz/` | Panda3D scéna a render task | všetko okrem `ui` |
| `ui/` | PySide6 shell | všetko |

Dôvod pre prísny `domain/` bez závislostí je praktický, nie ideologický: **kinematiku
a interpoláciu potrebuješ testovať bez otvárania okna a bez PLC.** To je 90 % logiky,
ktorá sa dá pokaziť, a zároveň 100 % toho, čo sa dá otestovať v milisekundách.

## Kľúčové rozhodnutia

### R1 — STEP čítame cez OpenCASCADE (`cadquery-ocp`), nie cez konvertor

Panda3D nevie STEP. Alternatívy boli FreeCAD headless (ťažký, GUI závislosti),
`gmsh` (mieri na FEM meshe, nezachová assembly), `assimp` (STEP nepodporuje).

Používame **`STEPCAFControlReader` + `XCAFDoc`**, nie `STEPControl_Reader`. Rozdiel je
podstatný: CAF verzia dá **assembly tree s názvami, transformáciami a farbami**. Bez toho
nemáš na čo namapovať kĺby a musel by si diely identifikovať ručne podľa geometrie.

### R2 — Tesselácia je offline, výsledok sa cachuje

Tesselácia assembly s tisíckami dielov trvá desiatky sekúnd až minúty. Cache kľúč je hash
zo (obsah STEP súboru + parametre tesselácie + `IMPORTER_VERSION`). Cache je v
`assets/cache/` a je **plne zahoditeľná** — zmazanie znamená len ďalší pomalý štart,
nikdy nie stratu dát.

### R2b — Formát geometrie v cache je `.npz`, nie glTF

`cad/` o Panda3D vedieť nesmie, takže `.bam` do cache zapísať nemôže. Pôvodný zámer bol
glTF cez `trimesh`, ale to by pridalo dva pohyblivé diely (`trimesh` pri zápise,
loader plugin `panda3d-gltf` pri čítaní) do cesty, ktorá musí byť spoľahlivá.

Namiesto toho je formát `.npz`: vrcholy, normály a indexy ako numpy polia. `numpy`
v projekte už je, `viz/` z toho postaví `Geom` priamo cez `copyDataFrom` (jeden blokový
kopírovací príkaz namiesto `GeomVertexWriter` po riadkoch), a celý formát sa dá otestovať
v `tests/unit/` bez OpenCASCADE aj bez Panda3D.

Cena: mesh sa nedá otvoriť v Blenderi. Na prezeranie je tu pôvodný STEP.

**Geometria je kľúčovaná podľa definície dielu**, nie podľa cesty uzla. Ten istý diel
použitý desaťkrát má v cache jeden súbor a desať uzlov naň ukazuje. Bez toho by zostava
s tisíckou skrutiek mala v cache tisíc kópií tej istej skrutky.

### R2c — Kĺb sa pohybuje relatívne k polohe z CAD

Uzol má dve polohy: tú z CAD assembly a tú, ktorú diktuje kĺb. Skladajú sa —
CAD určuje, kde diel je v nule, kĺb pridáva pohyb na vrch.

Alternatíva (kĺb polohu prepíše) by znamenala, že diel pri prvej hodnote z PLC skočí
do počiatku svojho rodiča a definícia stroja by musela v `origin:` duplikovať to,
čo už je v STEP súbore.

### R3 — Jednotky: metre a radiány, konverzia na hranici

CAD dáva mm, PLC dáva čokoľvek (mm, stupne, inkrementy enkodéra). Ak sa konverzia deje
na viacerých miestach, skôr či neskôr sa niekde vynásobí dvakrát a diel odletí do vesmíru.
Preto: každý vstup sa konvertuje **raz**, v `config/loader.py` (`scale`, `offset` v YAML),
alebo v `cad/` (jednotky STEP). Vnútri systému existuje jedna jednotka.

### R4 — OPC UA v samostatnom vlákne s vlastným asyncio loopom

Panda3D task manager podporuje `async def` tasky, ale awaituje **Panda3D futures**,
nie asyncio. Nedá sa v ňom bežať asyncua klient.

Preto: vlákno B beží `asyncio.run()` s asyncua klientom, notifikácie zapisuje do
`StateStore` pod lockom. Vlákno A (Panda3D) z neho **len číta**. Zdieľame
`latest value + krátky ring buffer`, nie queue — queue by pri zaostávaní renderu rástla
a zobrazovali by sa staré dáta.

### R5 — Interpolácia je povinná, nie voliteľná

OPC UA subscription reálne doručuje dáta každých 20–100 ms, renderujeme 60 fps.
Bez interpolácie je pohyb trhaný. `domain/interpolation.py` drží pre každý signál
krátku históriu `(source_time, value)` a vzorkuje ju v čase `now - render_delay`,
kde `render_delay` je zámerné malé spozdenie (default 2× publishing interval),
aby sa interpolovalo medzi dvoma známymi bodmi a nie extrapolovalo.

**Hranica použiteľnosti:** ak PLC os mení polohu rýchlejšie, než ju OPC UA stíha
publikovať, interpolácia to nezachráni — bude vyhladzovať pohyb, ktorý sa v skutočnosti
nedeje. Vtedy treba iný transport (viď R6).

### R6 — Zdroj dát je za rozhraním, OPC UA je len prvá implementácia

`io/base.py` definuje `DataSource` (Protocol). Implementácie: `OpcUaSource`,
`ReplaySource`, `MockSource`. Ak sa ukáže, že OPC UA je pre rýchle osi pomalé,
pridá sa `AdsSource` (Beckhoff, `pyads`) alebo `S7Source` (`python-snap7`) bez zásahu
do `viz/` a `domain/`.

### R7 — Záznam a replay od začiatku

`pssim record` uloží dátový tok do JSONL, `pssim replay` ho prehrá cez ten istý
`DataSource` interface. Bez toho sa nedá vyvíjať bez hardware ani reprodukovať chyby,
ktoré sa stali raz na stroji u zákazníka.

### R8 — Bez fyziky, kým nebude dôvod

Dáta z PLC sú kinematika — polohy sú dané, nie vypočítané. Fyzikálny engine by tu
nič neriešil a priniesol by nedeterminizmus. Ak neskôr treba detekciu kolízií,
`panda3d.bullet` je súčasťou Panda3D a konvexné obaly sa dajú vytiahnuť z OCC.

### R9 — Shell v PySide6, viewport v Panda3D

DirectGUI nezvládne stromy, dockovanie a property gridy na úrovni, akú CAD-like nástroj
potrebuje. Panda3D vie renderovať do rodičovského window handle, takže sa dá vložiť
do `QWidget`. `viz/` je preto navrhnuté tak, aby fungovalo aj samostatne
(`pssim run --no-ui`) — na debug a na testy.

## Výkon

Assembly zo STEP-u má typicky stovky až tisíce dielov, čo je pri naivnom prístupe
neúnosný počet draw callov. Preto pri stavbe scény:

- diely, ktoré **nie sú** kĺbom ani potomkom kĺbu → `flattenStrong()` do jedného Geomu
- opakované diely (skrutky, valčeky dopravníka) → `instanceTo()`
- vzdialené celky → `LODNode`
- pohyblivé diely zostávajú samostatné `NodePath` — tie flattenovať nemožno

Rozdelenie na statické a pohyblivé robí `viz/scene_builder.py` podľa definície kĺbov.

## Čo zámerne nie je vyriešené

| Vec | Stav |
|---|---|
| Zápis do PLC | mimo rozsah, len čítanie |
| IK / plánovanie trajektórií | mimo rozsah, PLC dáva hotové polohy |
| Kolízie | odložené, viď R8 |
| Iné CAD formáty než STEP | STEP je minimum; IGES/JT/glTF sa dajú pridať v `cad/` |
| Viac strojov v jednej scéne | dátový model to dovoľuje, scene builder zatiaľ nie |
| Bezpečnosť OPC UA (certifikáty) | rozhranie pripravené, konfigurácia neimplementovaná |

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
  ┌───────────────────┐                                │ .npz (vrcholy)
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

Hranicu drží `viz/embed.EmbeddedRenderer`: dovnútra Panda3D, von len čísla
a `CadAssembly`. `ui/viewport.py` ho iba drží a preposiela mu Qt udalosti,
takže `ui/` Panda3D vôbec neimportuje.

Tri veci, ktoré pri vkladaní prekvapia a stálo to čas ich nájsť:

1. **Render loop nepatrí Panda3D.** `base.run()` prevezme riadenie a Qt zamrzne.
   Tiká `QTimer`, ktorý volá `taskMgr.step()`.
2. **Veľkosť okna je vo fyzických pixeloch.** Qt počíta v logických; pri 125 %
   škálovaní Windows je rozdiel 1,25× a prejaví sa ako čierny pás vpravo a dole.
   Prepočet je v `ui/viewport._device_size()`.
3. **Myš dostáva Panda3D okno, nie Qt widget.** Ovládanie kamery preto nemôže byť
   v `mousePressEvent()` — je vo `viz/orbit_control.py` nad udalosťami Panda3D.

### R9b — Kamera je orbitálna, nie voľná

`viz/orbit.OrbitCamera` drží stav sféricky: bod záujmu, vzdialenosť, azimut,
elevácia. Alternatíva (voľná kamera s kvaterniónom) sa pri prehliadaní modelu
chová horšie — stráca „hore" a používateľ sa v nej ľahko stratí.

Elevácia je orezaná pred pólmi (`lookAt` tam stráca referenciu a obraz sa preklopí)
a kamera sa nikdy nenakláňa nabok. Zoom je multiplikatívny, aby krok kolieska
zodpovedal aktuálnemu priblíženiu.

Celá matematika je **čistá funkcia** v `viz/orbit.py` bez Panda3D. Dôvod je ten istý
ako pri `domain/`: „model sa točí divne" je inak chyba, ktorá sa ladí len očami.
Panda3D časť (`viz/orbit_control.py`) len dodá čísla z myši.

Vstavaný trackball sa **nepoužíva** (`base.disableMouse()`) — má neintuitívne
ovládanie a nedá sa mu povedať, okolo čoho má orbitovať.

### R9c — Štandardné pohľady majú jediný zdroj pravdy

`viz/orbit.STANDARD_VIEWS` mapuje názov pohľadu na `(azimut, elevácia)`. Všetko
ostatné sa z toho odvodzuje: `viz/camera.view_direction()` počíta smerový vektor
pre `pssim screenshot`, `ui/main_window` z toho robí položky menu a `ui/icons`
kreslí ikony premietnutím osí tou istou kamerou.

Predtým existovala definícia „čo je čelný pohľad" na dvoch miestach (uhly pre
interaktívnu kameru, vektory pre screenshot). Také dvojice sa časom rozídu
a rozdiel si nikto nevšimne, kým sa nezačne diviť, prečo `--view front`
v screenshote vyzerá inak než `Ctrl+2` v aplikácii.

`top` a `bottom` používajú **orezanú** eleváciu, nie presne `±pi/2`: v póle
stráca `lookAt` referenciu „hore" a obraz sa preklopí.

### R10 — Umiestnenie modelu je transformácia koreňa, nie zásah do geometrie

Posun a otočenie modelu (`Model → Placement…`) sa aplikuje na **koreňový
`NodePath`**, nie na vrcholy v cache. Cache tak zostáva viazaná výhradne na
obsah STEP súboru a parametre tesselácie — dva rôzne umiestnenia toho istého
modelu nevyrobia dve kópie geometrie.

Dôsledky, ktoré z toho plynú a sú zámerné:

- Otočenie je okolo **počiatku modelu**, nie okolo ťažiska. To je to, čo človek
  čaká, keď zadáva „otoč o 90° okolo Z".
- Kríž v počiatku sa nehýbe — je referencia, voči ktorej sa model umiestňuje.
- Umiestnenie prežije načítanie iného súboru; aplikuje sa pred rámovaním kamery,
  aby kamera mierila tam, kde model naozaj skončí.

**Jednotky sa prevádzajú v `domain/placement.py`, nie v dialógu.** UI je ďalšia
hranica systému a platí pre ňu to isté, čo pre `config/` a `io/` (viď R3):
používateľ zadáva mm a stupne, scéna beží v metroch a radiánoch, konverzia sa
deje raz a má testy. Šesť polí krát dva smery je dosť príležitostí na preklep.

### R11 — Preklady cez Qt, zdrojový jazyk angličtina

Texty pre používateľa sú v kóde napísané **po anglicky** a obalené v `tr()`.
Preklady sú `.ts`/`.qm` súbory, mechanizmus je `ui/i18n.py`.

Prečo Qt a nie vlastný slovník: Qt už rieši fallback na zdrojový text, množné
čísla, extrakčné nástroje a hlavne **preklad vlastných dialógov Qt** —
`QFileDialog`, tlačidlá `OK`/`Cancel`. Vlastná implementácia by tie štandardné
prvky nechala v angličtine a UI by bolo napoly preložené.

Z toho plynú dve pravidlá:

- **Formátovanie hlášok nepatrí do `domain/`.** Predtým `domain/placement.py`
  vracalo vetu do stavového riadku; doména však nemá ako vedieť, v akom jazyku
  appka beží, a nesmie importovať Qt. Presunuté do `ui/labels.py`.
- **Štandardné tlačidlá Qt sa neprepisujú.** Natvrdo nastavený text by pri
  prepnutí jazyka zostal v angličtine, kým zvyšok dialógu by sa preložil.

Logy sa **neprekladajú** — sú pre vývojára, nie pre používateľa.

### R12 — Viac modelov: stav je v registri, nie v scéne ani vo widgete

Modely drží `ui/model_registry.ModelRegistry` — čistá, netestovateľne nudná
kolekcia bez Qt a bez Panda3D. `ui/model_tree` ju **vykresľuje**,
`viz/embed.EmbeddedRenderer` ju **kreslí**, ale ani jeden ju nevlastní.

Prečo nie stav v tree widgete (kam by ho väčšina uložila): zaujímavá logika sú
unikátne názvy pri opakovanom otvorení toho istého súboru a to, čo sa stane
s výberom po zmazaní modelu. Oboje sa dá pokaziť a oboje sa v registri dá
otestovať bez okna.

Kľúčovanie je podľa **generovaného `model_id`**, nie podľa cesty k súboru:
ten istý súbor sa smie otvoriť viackrát. Zobrazovaný názov je len na pozeranie
a môže sa opakovať, takže sa ním nikdy nič nekľúčuje.

Z toho plynie jedno pravidlo, ktoré sa dá ľahko porušiť: **výber musí byť
synchronizovaný v oba smery.** Klik v tree ide do registra, ale výber sa mení
aj z kódu (susedný model po zmazaní), a vtedy sa musí premietnuť späť do tree —
inak zostane zvýraznený riadok, s ktorým aplikácia už nepracuje. Chytilo to až
reálne spustenie, unit testy nie.

Zvýraznenie v scéne je **drôtený rám**, nie zmena farby: modely majú vlastné
farby zo STEP súboru, takže tónovanie by na niektorých nebolo vidieť a na iných
by klamalo.

### R13 — Projekt je JSON v mm a stupňoch, modely sa načítavajú po jednom

The project file (`*.pssim`) records what the scene consists of: the models, their
placements, which one is selected, and where the camera is. It does **not** contain
geometry — a project is a list of references, so the same STEP file behind ten
projects is still one file and one cache entry.

Three decisions inside that are worth the words:

**JSON, not YAML.** The file is written by the application, never by hand, so YAML's
flexibility buys nothing and costs a diff: two saves of the same scene must differ
only where the scene differs. `config/schema.py` keeps YAML because machine
definitions *are* written by hand; this one is not.

**Millimetres and degrees, including the camera.** The same boundary rule as R3, and
the file is the boundary. The numbers in the file are the numbers the user typed into
the Placement dialog, which means a project can be read and checked without
converting anything mentally. Conversion happens once, in `config/project.py` and
`ui/project_controller.py`, and has tests in both directions.

**Model paths are relative only when the model lives inside the project's folder.**
Then the project and its `models/` subfolder move or get shared as a unit. A model
kept anywhere else is stored absolute: it does not travel with the project, and a
chain of `..` segments back out to it breaks the moment the project file alone is
moved — where an absolute path still opens.

Not stored: the camera's zoom limits (they follow from the size of the scene, and the
scene is whatever was just loaded), window geometry, and the cache directory.

`selected` names the model, it does not identify it. Model ids are generated per
session (R12) and would mean nothing after a reload; the name is what the user sees
in the tree, so it is what survives.

Loading is a **queue, not a loop**: `ui/project_controller.ProjectLoader` starts one
import, waits for `on_import_finished`, then starts the next. The importer writes
into a shared cache, so two at once would race. Missing files are collected by
`plan_load` and reported **once** — a project referencing five moved files must not
mean five modal dialogs — and everything still on disk loads anyway. Selection and
camera are applied at the end, once every model is in.

Format version is refused **forward, not backward**: a file written by a newer build
is an error rather than something half-read, because silently ignoring a section the
user configured is worse than saying no.

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

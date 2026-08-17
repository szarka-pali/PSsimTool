# PSsimTool

3D simulácia strojov v reálnom čase. Geometria z CAD (STEP), pohyb z PLC cez **OPC UA**.

Nie je to fyzikálny simulátor — polohy a natočenia prichádzajú z riadiaceho systému,
aplikácia ich zobrazuje. Slúži na to, aby bolo vidieť, čo PLC program naozaj robí.

## Rýchly štart

```bash
uv sync --all-extras
```

Projekt vyžaduje **Python 3.12** — `panda3d` ani `cadquery-ocp` nemajú wheels pre 3.13+.
`uv` si správnu verziu stiahne sám, systémový Python meniť netreba.

### Desktopová aplikácia

```bash
uv run pssim ui
```

Otvorí okno s hlavným menu. Bez PLC, bez definície stroja.

**UI je po anglicky.** Zdrojové texty sú v kóde priamo v angličtine a obalené
v `tr()`, takže sa dajú preložiť bez zásahu do logiky. Iný jazyk sa vyberie
prepínačom:

```bash
uv run pssim ui --lang sk
```

Zoznam jazykov a návod na pridanie ďalšieho je v
[src/pssim/ui/translations/README.md](src/pssim/ui/translations/README.md).
Zatiaľ je skompilovaná len angličtina — `--lang sk` skončí chybou, kým
preklad nevznikne. Voľba jazyka v menu pribudne neskôr; napojí sa na
`ui/i18n.install_translator()`.

| Menu | Položka | Čo robí |
|---|---|---|
| `File` | `Exit` (Ctrl+Q) | ukončí aplikáciu |
| `Open` | `Open 3D file…` (Ctrl+O) | otvorí STEP súbor a zobrazí ho |

Import beží **na pozadí** — okno počas neho reaguje. Veľká zostava sa tesseluje
minúty, výsledok sa cachuje do `assets/cache/`, takže druhé otvorenie je okamžité.
Po načítaní sa kamera automaticky vycentruje na model.

Súbor bez definície stroja sa považuje za **milimetrový** (`ASSUMED_UNITS`
v `ui/loader.py`) — väčšina strojárskeho CAD to tak má.

### Lišta a štandardné pohľady

Tlačidlo **Pohľad** rozbalí menu so siedmimi orientáciami; ikona tlačidla vždy
ukazuje, v ktorej sa práve nachádzaš.

| Pohľad | Skratka | | Pohľad | Skratka |
|---|---|---|---|---|
| Isometric | Ctrl+1 | | Right | Ctrl+5 |
| Front | Ctrl+2 | | Top | Ctrl+6 |
| Back | Ctrl+3 | | Bottom | Ctrl+7 |
| Left | Ctrl+4 | | **Zobraz celé** | Ctrl+0 |

Prepnutie pohľadu **zachová priblíženie** aj bod záujmu — mení sa len uhol.
`Zobraz celé` vycentruje kameru späť na celý model.

Ikony sa kreslia za behu (`ui/icons.py`), v repozitári nie sú žiadne binárne
assety. Každá ikona premieta osi tou istou kamerou, aká sa po kliknutí použije,
takže nemôže ukazovať niečo iné, než sa naozaj stane.

### Umiestnenie modelu

`Model → Placement…` (Ctrl+M) otvorí dialóg s posunom v X/Y/Z a otočením okolo
každej z osí. Zadáva sa v **milimetroch a stupňoch** — tak, ako je zvykom v CAD;
prevod na interné metre a radiány robí `domain/placement.py`.

Slúži na to, aby sa dal model posadiť tam, kam patrí: CAD súbor má počiatok tam,
kde ho nechal konštruktér, a to nemusí byť bod, voči ktorému chceš merať.

- Zmena sa prejaví **okamžite** — hodnoty sa inak zadávajú naslepo.
- `Zrušiť` vráti stav, aký bol pri otvorení dialógu.
- Dialóg je **nemodálny**, takže sa počas zadávania dá scénou otáčať.
- Otočenie sa deje okolo **počiatku modelu**, nie okolo jeho ťažiska.
- Kríž v počiatku sa nehýbe — je to referencia, voči ktorej sa model umiestňuje.
- Kamera zostáva; ak model odíde mimo záber, vráti ho `Ctrl+0`.

### Kartézsky kríž

V počiatku súradníc modelu je kríž s osami **X červená, Y zelená, Z modrá**
a popiskami — rovnaká konvencia ako v CAD nástrojoch. Slúži na orientáciu pri
otáčaní, hlavne pri symetrických dieloch.

Veľkosť sa odvíja od rozmeru modelu (štvrtina jeho polomeru), takže je čitateľný
na jednom dieli aj na celej linke.

### Ovládanie scény myšou

| Vstup | Akcia |
|---|---|
| stredné tlačidlo + tah | otáčanie okolo modelu |
| **Shift** + stredné + tah | posun |
| pravé tlačidlo + tah | posun |
| ľavé tlačidlo + tah | otáčanie |
| koliesko | priblíženie / oddialenie |

Konvencia je prevzatá z CAD nástrojov (SolidWorks, Fusion, Inventor). Väzby sú
na jednom mieste — `viz.orbit.drag_action()`.

Kamera sa nenakláňa nabok a elevácia je orezaná pred pólmi, takže sa obraz
nikdy neprevráti. Priblíženie je násobné, nie sčítacie: krok kolieska je pri
detaile malý a pri odzoomovanom pohľade veľký.

### Demo bez PLC a bez vlastného CAD

`machines/demo.yaml` beží na testovacej geometrii a na simulovanom PLC, takže
sa dá spustiť hneď po naklonovaní. Najprv import geometrie do cache:

```bash
uv run pssim import-step tests/data/fixture.step --machine machines/demo.yaml
```

Potom v jednom termináli simulované PLC:

```bash
uv run pssim mock-server
```

a v druhom aplikácia:

```bash
uv run pssim run machines/demo.yaml
```

Otvorí sa okno s portálom, ktorý sa hýbe podľa hodnôt z mock servera.

### S vlastným strojom

Skopíruj `machines/priklad.yaml`, uprav cesty uzlov a OPC UA nody, a naimportuj
geometriu (trvá minúty, výsledok sa cachuje):

```bash
uv run pssim import-step models/stroj.step --machine machines/stroj.yaml
```

## Ako to funguje

```
.step ──► cad/ (OpenCASCADE) ──► assets/cache/*.npz ──┐
                                                       ├──► viz/ (Panda3D)
PLC ──► io/ (asyncua) ──► StateStore ──► domain/ ──────┘
```

`machines/*.yaml` je most medzi tým: hovorí, ktorý uzol CAD assembly je ktorý kĺb
a ktorý OPC UA node ho riadi.

```yaml
machine: priklad
step_file: models/priklad.step
units: mm
joints:
  - name: os_x
    parent: base
    child: portal
    type: prismatic
    axis: [1, 0, 0]
    limits: [0.0, 2.5]
    signal:
      node: "ns=2;s=Axes.X.ActPos"
      scale: 0.001        # PLC posiela mm, scéna je v metroch
```

Podrobnosti a dôvody rozhodnutí: **[docs/architecture.md](docs/architecture.md)**.
Doménový slovník a mapovanie signálov: `.claude/skills/domenovy-kontext/`.

## Vývoj

| Účel | Príkaz |
|---|---|
| Rýchle testy | `uv run pytest tests/unit -q` |
| Všetky testy | `uv run pytest` |
| Lint + formát | `uv run ruff format . && uv run ruff check --fix .` |
| Typová kontrola | `uv run pyright` |
| Záznam dát z PLC | `uv run pssim record machines/priklad.yaml -o recordings/beh.jsonl` |
| Prehranie záznamu | `uv run pssim replay recordings/beh.jsonl machines/priklad.yaml` |

Pred commitom musí prejsť `ruff check` aj `pytest tests/unit`.

### Rozdelenie testov

- `tests/unit/` — bez I/O, bez okna, bez OPC UA. Beží do niekoľkých sekúnd.
- `tests/integration/` s markerom `integration` — proti mock OPC UA serveru
  (`uv run pytest -m integration`).
- `tests/integration/` s markerom `cad` — import STEP, vyžaduje `uv sync --extra cad`
  (`uv run pytest -m cad`).
- `tests/integration/` s markerom `ui` — okno a menu, vyžaduje `uv sync --extra ui`
  (`uv run pytest -m ui`). Bežia headless cez `QT_QPA_PLATFORM=offscreen`,
  žiadne okno sa neotvára.

Testovací STEP súbor `tests/data/fixture.step` je vo verzovaní (50 kB).
Vygeneruje ho `uv run python tools/make_step_fixture.py` — obsahuje zámerne
duplicitné názvy dielov, otočený diel a diel bez farby.

## Stav implementácie

| Časť | Stav |
|---|---|
| `domain/` — model stroja, kinematika, interpolácia, jednotky, čas | hotové, 163 unit testov |
| `config/` — YAML schéma a loader | hotové, pokryté testami |
| `io/store`, `io/replay`, `io/recorder`, `io/timebase` | hotové, pokryté testami |
| `io/opcua_source`, `io/mock_server` | **overené integračnými testami** proti mock serveru; proti reálnemu PLC zatiaľ nie |
| `cad/cache`, `cad/mesh`, stabilné cesty uzlov, škálovanie jednotiek | hotové, pokryté testami |
| `cad/step_import` — čítanie STEP cez OpenCASCADE | **overené** proti `tests/data/fixture.step` (35 testov): assembly tree, názvy, farby, jednotky, rotácie, geometria, cache |
| `viz/scene_builder`, `viz/transforms` | hotové, pokryté testami (bez Panda3D) |
| `viz/mesh_loader`, `viz/app` — scéna a render loop | **overené** headless testami celej reťaze (29 testov) aj reálnym spustením |
| `viz/orbit`, `viz/orbit_control` — ovládanie kamery a pohľady | hotové (unit + integračné testy) |
| `viz/axes` — kartézsky kríž | hotové, pokryté testami |
| `viz/embed` + `ui/viewport` — Panda3D vo QWidget | hotové, overené reálnym spustením |
| `domain/placement` — posun a otočenie modelu, prevod jednotiek | hotové, pokryté testami |
| `ui/i18n` — mechanizmus prekladov | hotové; preklad do sk zatiaľ neexistuje |
| `ui/` — okno, menu, lišta, umiestnenie, načítanie STEP na pozadí | hotové (122 testov) |
| `ui/` — strom dielov, property grid, HUD | neimplementované |

Celá reťaz **STEP → cache → scéna → hodnota z PLC → poloha dielu** je pokrytá
testami v `tests/integration/test_viz_scene.py`, ktoré bežia bez otvorenia okna.

## Štruktúra repozitára

```
src/pssim/          zdrojový kód (viď docs/architecture.md pre vrstvy)
machines/           YAML definície strojov — verzované
models/             vstupné CAD súbory — negitované (veľké binárky)
assets/cache/       generované meshe — negitované, zahoditeľné
recordings/         záznamy dátových tokov — negitované
docs/               architektúra a rozhodnutia
tests/              unit + integration
.claude/            konfigurácia Claude Code (verzovaná, zdieľaná s tímom)
```

## Konfigurácia Claude Code

`.claude/` a `CLAUDE.md` sú súčasťou repozitára — po naklonovaní sa Claude Code chová
u každého rovnako. Vysvetlenie, ako je to postavené a prečo, je v
[docs/claude-code-prirucka.md](docs/claude-code-prirucka.md).

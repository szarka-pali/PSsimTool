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
| `ui/` — PySide6 shell | neimplementované |

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

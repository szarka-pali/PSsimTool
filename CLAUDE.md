# PSsimTool

## Čo to je

Desktopová aplikácia na **3D simuláciu strojov** v reálnom čase. Načíta geometriu stroja
z CAD súborov (minimálne STEP), poskladá z nej kinematickú hierarchiu podľa definície stroja
a jej pohyblivé časti riadi **live hodnotami z PLC cez OPC UA** — polohy, natočenia
a vlastnosti objektov.

Používateľ je inžinier uvádzajúci stroj do prevádzky alebo programátor PLC, ktorý potrebuje
vidieť, čo riadiaci program naozaj robí. Nie je to hra ani fyzikálny simulátor:
**dáta prichádzajú z PLC, aplikácia ich len zobrazuje.** Vlastnú dynamiku nepočíta.

Hranica systému: aplikácia je **OPC UA klient**. Neobsahuje PLC logiku, needituje CAD,
nezapisuje do PLC (okrem výslovne označených ovládacích prvkov, ktoré zatiaľ neexistujú).

## Príkazy

Projekt beží na **Python 3.12** (nie novšom — `panda3d` a `cadquery-ocp` nemajú wheels
pre 3.13+). `uv` si správnu verziu stiahne sám.

| Účel | Príkaz |
|---|---|
| Inštalácia závislostí | `uv sync --all-extras` |
| Spustenie lokálne | `uv run pssim run machines/priklad.yaml` |
| Mock PLC (druhý terminál) | `uv run pssim mock-server` |
| **Testy (rýchle)** | `uv run pytest tests/unit -q` |
| Testy proti mock PLC | `uv run pytest -m integration` |
| Testy importu STEP | `uv run pytest -m cad` (treba `uv sync --extra cad`) |
| Testy (všetky, pomalé) | `uv run pytest` |
| Lint + formát | `uv run ruff format . && uv run ruff check --fix .` |
| Typová kontrola | `uv run pyright` |
| Import STEP do cache | `uv run pssim import-step <subor.step>` |
| Build distribúcie | `uv run python setup_dist.py bdist_apps` |

> Po každej zmene kódu spusti `uv run ruff check . && uv run pytest tests/unit -q`.
> Ak neprejde, oprav to pred tým, než mi ohlásiš hotovo.

## Štruktúra

```
src/pssim/
  domain/       čistá logika: model stroja, kinematika, interpolácia, jednotky, chyby
                NEIMPORTUJE panda3d, asyncua, pydantic ani OCP
  config/       pydantic schémy YAML definícií strojov + loader do domain modelu
  io/           zdroje dát: OPC UA klient, replay, mock server, thread-safe state store
  cad/          import STEP → tesselácia → cache; nič o Panda3D nevie
  viz/          Panda3D: scéna, mapovanie kĺbov na NodePath, HUD
  ui/           PySide6 shell (okno, stromy, property grid) — hostí viz viewport
  cli.py        vstupné body (typer)
machines/       YAML definície strojov (verzované)
models/         vstupné CAD súbory — VEĽKÉ BINÁRKY, needituj, negituj
assets/cache/   GENEROVANÉ tesselované meshe — needituj ručne, kľudne zmaž
recordings/     zaznamenané dátové toky na replay (negitované)
tools/          pomocné skripty (generátor STEP fixture)
tests/unit/     rýchle, bez I/O, bez okna, bez OPC UA
tests/integration/  markery `integration` (mock PLC) a `cad` (OpenCASCADE)
tests/data/     malé fixture súbory — verzované, na rozdiel od models/
docs/           architektúra a rozhodnutia
```

## Konvencie, ktoré platia bez výnimky

- **Závislosti idú len dovnútra.** `domain/` neimportuje nič z `viz/`, `io/`, `cad/`, `ui/`
  ani žiadny externý framework. Ak treba dostať dáta do domény, pošli ich ako argument.
- **Jednotky: scéna je v metroch a radiánoch.** CAD je typicky v mm, PLC posiela často
  mm a stupne. Konverzia sa deje **na hranici** (`config/loader.py`, `cad/`, `io/`),
  nikdy nie v `domain/` a nikdy nie v `viz/`. Pozri `src/pssim/domain/units.py`.
- **Čas: vždy `SourceTimestamp` z OPC UA**, nie lokálny čas príchodu. Interne sekundy
  ako `float` v monotónnej škále. Konverzia v `io/`.
- **Nikdy nepolluj OPC UA v render loope.** Dáta chodia cez subscriptions do
  `io/store.StateStore`, render vlákno z neho len číta interpolovaný snapshot.
- **Chyby:** vyhadzuj typované chyby z `src/pssim/domain/errors.py`, nie generický `Exception`.
- **Logovanie:** `structlog` cez `pssim.observability.get_logger()`, nikdy `print()`.
- **Panda3D objekty nikdy neopúšťajú `viz/`.** Žiadny `NodePath`, `LVector3` ani `LQuaternion`
  v signatúrach mimo `viz/`. Domain vracia `JointPose` (os + uhol / posun), viz to preloží.
- **Nové závislosti len po schválení.** Napíš, prečo nestačí stdlib alebo už prítomná knižnica.

## Čo NEROBIŤ

- Needituj: `assets/cache/**`, `models/**`, `uv.lock`, `.env*`, `recordings/**`
- Nespúšťaj: nič, čo **zapisuje** do OPC UA servera na reálnom stroji. Zápis testuj
  výhradne proti `pssim mock-server`.
- Nemeň formát YAML definície stroja v `config/schema.py` nekompatibilne — existujúce
  `machines/*.yaml` musia zostať načítateľné. Pri zmene doplň migračnú cestu.
- Nepridávaj do `domain/` numpy/pydantic „pre pohodlie" — je to testovateľné práve preto,
  že tam nič také nie je.
- Nepridávaj komentáre, ktoré len opakujú kód.

## Keď si nie si istý

- Ak zadanie pripúšťa dva rozumné výklady, **spýtaj sa** pred implementáciou.
- Ak treba zmeniť viac ako ~5 súborov, najprv napíš plán a nechaj ho schváliť.
- Vzory, ktoré chceme napodobňovať:
  - `src/pssim/domain/kinematics.py` — takto vyzerá čistá, plne otestovaná doménová vrstva
  - `src/pssim/io/base.py` — takto definujeme hranicu k vonkajšiemu svetu (Protocol, nie ABC)
- **Nepíš kód proti API knižnice, ktoré si neoveril.** `asyncua` a `OCP` majú rozsiahle
  a neintuitívne API. Ak si nie si istý volaním, over ho v REPL alebo v dokumentácii
  a napíš to do commit správy.

## Ďalší kontext

@docs/architecture.md

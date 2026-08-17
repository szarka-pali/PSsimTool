---
paths:
  - "src/**"
description: Štýl a štruktúra produkčného kódu (Python)
---

# Štýl kódu

Formátovanie riešime `ruff format`, nie diskusiou — nerieš odsadenie ani zátvorky.
Toto sú pravidlá, ktoré linter nezachytí.

## Pomenovanie

- Funkcie sú **činnosti** (`sample_signal`, `build_scene`), premenné sú **veci** (`position`).
- **Všetko po anglicky** — identifikátory, komentáre, docstringy, logy aj texty
  pre používateľa. Dôvod je v `CLAUDE.md`, sekcia *Language*. Staré slovenské
  komentáre prekladaj len keď na súbor siahaš z iného dôvodu.
  Doménové pojmy nechávaj v tvare zo slovníka (`joint`, `signal`, `deflection`).
- **Texty pre používateľa** musia byť obalené v `self.tr()` (v `QObject`) alebo
  `QCoreApplication.translate("Kontext", "text")` (na module level). Neobalený
  text sa nedostane do `.ts` a nikdy sa nepreloží.
  Viď `src/pssim/ui/translations/README.md`.
- **Logy sa neprekladajú** cez `tr()` — sú pre vývojára, nie pre používateľa.
  Ale píšu sa po anglicky, ako všetko ostatné.
- Žiadne skratky okrem ustálených (`id`, `url`, `cad`, `hpr`, `rpy`). Nie `jnt_cnt`, ale `joint_count`.
- Boolean začína `is_`/`has_`/`can_` (`is_stale`, `has_limits`).
- Ak potrebuješ v názve `and`/`or`, funkcia robí dve veci — rozdeľ ju.
- **Jednotky do názvu, ak nie sú z typu zrejmé:** `delay_s`, `interval_ms`, `angle_rad`,
  `length_mm`. Toto je v tomto projekte najčastejší zdroj chýb — nešetri znakmi.

## Typy

- Každá verejná funkcia má **plné typové anotácie**. `pyright` beží v `strict` režime na `src/`.
- Žiadne `Any` bez komentára prečo. Žiadne `# type: ignore` bez uvedeného dôvodu.
- Dátové nosiče: `@dataclass(frozen=True, slots=True)`. Mutovateľný stav len tam,
  kde má vlastníka a lock (`io/store.py`).
- Hranice medzi vrstvami definuj ako `typing.Protocol`, nie ako abstraktnú triedu.
  Implementácia sa nemusí importovať do modulu, ktorý ju konzumuje.

## Funkcie a moduly

- Funkcia sa vojde na obrazovku (~40 riadkov). Ak nie, chýba jej pomocná funkcia.
- Maximálne 3 úrovne vnorenia. Používaj early return namiesto `else`.
- Argumenty: max 4. Viac → frozen dataclass s pomenovanými poliami.
- Žiadne boolean flagy v signatúre, ktoré prepínajú správanie (`load(path, tessellate=True)`).
  Radšej dve funkcie.

## Chyby

- Vyhadzuj typované chyby z `src/pssim/domain/errors.py`, nie generický `Exception`.
- **Nikdy** neprehltni výnimku naprázdno. Buď ju spracuj, alebo prepošli s kontextom
  (`raise ConfigError(...) from exc`).
- Validuj vstup na hranici systému (`config/`, `io/`, `cad/`), nie hlboko v doméne.
  `domain/` môže predpokladať, že dáta sú platné a v správnych jednotkách.
- Chyba pri jednom signáli **nesmie zhodiť render loop**. `viz/` musí prežiť
  chýbajúci alebo neplatný signál — zobrazí posledný známy stav a označí ho ako zastaraný.

## Vlákna a asyncio

- `asyncio` beží **len** v `io/`. Nikde inde `async def`.
- Zdieľaný stav medzi vláknami existuje na jedinom mieste: `io/store.StateStore`.
  Nepridávaj druhý. Ak potrebuješ zdieľať niečo ďalšie, rozšír store.
- Lock drž čo najkratšie: pod lockom sa kopírujú dáta, nepočíta sa.
- Žiadne `time.sleep()` v produkčnom kóde mimo `io/replay.py`.

## Panda3D

- Panda3D typy (`NodePath`, `LVector3`, `LQuaternion`, `Geom`) sa nesmú objaviť
  v signatúrach mimo `viz/`.
- Žiadna práca v render tasku, ktorá sa dá spraviť raz pri stavbe scény.
- Načítanie assetu nikdy synchrónne počas behu — cez `loader.loadModel(..., blocking=False)`
  alebo pred prvým frame.

## Komentáre

- Komentár vysvetľuje **prečo**, nikdy **čo**. `# zvýš i o 1` je šum.
- Netriviálne rozhodnutie → jedna veta prečo, ideálne s odkazom na `docs/architecture.md`
  (napr. `# viď R4: asyncua nemôže bežať v Panda3D task manageri`).
- Žiadne `TODO` bez menovca. Žiadny zakomentovaný mŕtvy kód — zmaž ho, git si to pamätá.

## Závislosti

- Nové závislosti **len po schválení**. Napíš, prečo nestačí stdlib alebo už prítomná knižnica.
- `domain/` importuje **výhradne stdlib**. Ani numpy, ani pydantic.
- Ťažké importy (`OCP`, `panda3d`, `PySide6`) nikdy na module level v `cli.py` —
  importuj ich vnútri príkazu, ktorý ich potrebuje. Inak `pssim --help` trvá sekundy.

## Zmeny existujúceho kódu

- Napodobňuj vzor, ktorý v okolí už je, aj keby si to sám napísal inak.
  Konzistencia je cennejšia než tvoja preferencia.
- Nerefaktoruj nesúvisiaci kód „popri tom". Ak vidíš problém, spomeň ho, ale nemeň.
- Zachovaj verejné signatúry, ak nemáš výslovný pokyn ich meniť.

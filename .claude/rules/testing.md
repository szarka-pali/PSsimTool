---
paths:
  - "tests/**"
description: Konvencie pre písanie testov
---

# Testy

## Štruktúra

- `tests/unit/` — bez I/O, bez siete, **bez otvorenia okna, bez OPC UA**.
  Celé musí bežať do 5 s.
- `tests/integration/` — proti `pssim mock-server`. Označ `@pytest.mark.integration`.
  Spúšťa sa `uv run pytest -m integration`.
- Jeden testovací súbor zrkadlí jeden modul:
  `src/pssim/domain/kinematics.py` → `tests/unit/domain/test_kinematics.py`.
- Fixtures a factories: `tests/factories.py`. Nevytváraj testovacie dáta ručne inline.

## Ako píšeme testy

- Názov testu opisuje **správanie, nie implementáciu**:
  `test_prismatic_joint_posunie_po_osi`, nie `test_joint_transform_returns_tuple`.
- Vzor arrange / act / assert, oddelený prázdnym riadkom.
- **Jedno tvrdenie na test**, ak to nie je nezmyselné. Radšej päť malých testov než jeden veľký.
- Mockuj **len hranicu systému** (OPC UA klient, čas, súborový systém).
  Nikdy nemockuj vlastnú doménovú logiku — ak to test potrebuje, návrh je zlý.

## Špecifiká tohto projektu

- **Čas nikdy neber z `time.monotonic()` v teste.** Interpolácia a store berú čas
  ako argument (`sample_all(at_time=...)`) presne preto, aby sa dal v teste zadať.
  Ak píšeš kód, ktorý si čas berie sám, je to chyba návrhu — nahlás ju.
- **Čísla porovnávaj s toleranciou:** `pytest.approx(..., abs=1e-9)`. Kinematika je float
  aritmetika, presná rovnosť je náhoda.
- **Jednotky testuj explicitne.** Ku každému prevodu (mm→m, deg→rad, `scale`/`offset`
  z YAML) musí existovať test s konkrétnym číslom. Toto je v tomto projekte
  najpravdepodobnejší tichý bug.
- **Hraničné hodnoty kĺbov** testuj vždy: pod dolným limitom, presne na limite,
  nad horným limitom, kĺb bez limitov.
- **Interpoláciu** testuj aj na degenerovaných vstupoch: prázdny buffer, jediná vzorka,
  vzorky s rovnakou časovou známkou, dopyt na čas pred prvou a po poslednej vzorke.
- **Vlákna:** `StateStore` testuj aj súbežným zápisom a čítaním z viacerých vlákien.
  Bez `sleep()` — použi `threading.Barrier`.
- Panda3D a OCP sa v `tests/unit/` **neimportujú**. Ak by test niektorý potreboval,
  patrí do `tests/integration/`.

## Čo je zakázané

- Testy, ktoré len opakujú implementáciu (`assert mock.called_once()` ako jediné tvrdenie).
- `sleep()` na synchronizáciu — použi barrier, event alebo explicitný čas ako argument.
- Testy závislé od poradia spustenia alebo od zdieľaného globálneho stavu.
- Preskakovanie (`skip`) bez komentára s dôvodom.
- Testy načítavajúce reálne STEP súbory z `models/` — tie v repozitári nie sú.
  Použi generovanú geometriu alebo malý fixture v `tests/data/`.

## Pri pridávaní funkcionality

1. Najprv napíš test, ktorý **padne** a zachytáva požadované správanie.
2. Ukáž mi, že padá, **a prečo padá** — padajúci test, ktorý padá na `ImportError`,
   nedokazuje nič.
3. Až potom implementuj.
4. Spusti `uv run pytest tests/unit -q` a ukáž výstup.

Pri oprave bugu vždy najprv **reprodukčný test**. Bug bez regresného testu sa nepovažuje
za opravený.

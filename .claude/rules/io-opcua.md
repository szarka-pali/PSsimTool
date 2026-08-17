---
paths:
  - "src/pssim/io/**"
  - "tests/integration/**"
description: Pravidlá pre vrstvu zdrojov dát — OPC UA, vlákna, časové známky
---

# Vrstva `io/` — dáta z PLC

Táto vrstva je jediné miesto, kde beží asyncio a kde existuje viac ako jedno vlákno.
Chyby tu sa prejavia ako „občas to sekne" alebo „raz za hodinu to skočí" — teda
najhoršie laditeľné chyby v celom projekte. Preto sú pravidlá prísne.

## Vláknový model (nemeniť bez diskusie)

```
vlákno B: asyncio.run() → asyncua Client → subscription handler → StateStore.put()
vlákno A: Panda3D task  → StateStore.sample_all(at_time) → čítanie
```

- Panda3D task manager **nie je** asyncio loop. Awaituje Panda3D futures.
  Preto asyncua nemôže bežať v ňom. Viď `docs/architecture.md` R4.
- Vlákno B **nikdy** nesiahne na Panda3D ani na scénu. Zapisuje výhradne do `StateStore`.
- Vlákno A **nikdy** nevolá asyncua ani nič blokujúce.
- Nový zdroj dát implementuje `DataSource` z `base.py` a nič viac.

## Časové známky

- Používaj **`SourceTimestamp`** z OPC UA (čas, kedy hodnota vznikla v PLC),
  nie `ServerTimestamp` a už vôbec nie lokálny čas príchodu.
- Ak `SourceTimestamp` chýba (niektoré servery ho neposielajú), použi `ServerTimestamp`
  a **zaloguj to raz** — nie pri každej notifikácii.
- Prevod na internú škálu (`float` sekundy, monotónne) rob v jednom mieste
  (`_to_monotonic`). Offset medzi PLC časom a lokálnym monotónnym časom odhadni
  pri prvej vzorke a **drž ho konštantný** — inak sa interpolácia rozpadne.
- Hodinky PLC nie sú synchronizované s tvojimi. Nepredpokladaj to.

## Subscriptions, nie polling

- Vždy `create_subscription` + `subscribe_data_change`. Žiadny `read()` v cykle.
- `publishing_interval` a `sampling_interval` sú konfigurovateľné, nie hardcoded.
- Počítaj s tým, že server ti vráti **iný** interval, než si žiadal — je to jeho právo.
  Prečítaj revidovanú hodnotu z odpovede a použi ju na výpočet `render_delay`.
- `queue_size` a `deadband` nastavuj vedome. Deadband na polohovom signáli
  vyhladí drobný pohyb, ktorý možno práve chceš vidieť.

## Odolnosť

- Odpadnutie spojenia je **normálny stav**, nie výnimka. Reconnect s exponenciálnym
  backoffom, strop 30 s. Zaloguj prvý pokus a potom každý desiaty, nie každý.
- Po reconnecte sa subscription **musí obnoviť** — nodeIDs si drž, nespoliehaj sa
  na to, že klient to spraví za teba.
- Ak signál neprichádza dlhšie ako `stale_after_s`, označ ho ako zastaraný.
  Scéna to zobrazí, ale **nesmie** kvôli tomu prestať renderovať ani zamrznúť.
- Nikdy `raise` z callbacku subscription — výnimka v handleri môže zabiť celý loop.
  Zaloguj a pokračuj.

## Bezpečnosť a prostredie

- Endpoint URL, používateľ, cesty k certifikátom: **len z konfigurácie alebo prostredia**,
  nikdy v kóde a nikdy v `machines/*.yaml` (tie sú verzované).
- Adresy PLC zákazníka nepatria do repozitára ani do commit správ.
- Zápis do PLC neimplementuj bez výslovného pokynu. Ak ho dostaneš, musí byť
  za explicitným prepínačom a otestovaný **len** proti mock serveru.

## Testovanie

- `StateStore`, interpolácia a `ReplaySource` sa testujú v `tests/unit/` — sú čisté
  a čas berú ako argument.
- `OpcUaSource` sa testuje v `tests/integration/` proti `mock_server.py`.
  Nikdy proti reálnemu stroju v automatizovanom teste.
- Reprodukcia chyby z prevádzky = `recordings/*.jsonl` + `ReplaySource`, nie hádanie.

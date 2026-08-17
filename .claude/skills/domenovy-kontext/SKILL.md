---
name: domenovy-kontext
description: Doménový model a slovník PSsimTool — kĺb (joint), kinematický reťazec, signál, definícia stroja, tesselácia, deflection, stale dáta, jednotky. Použi vždy, keď zadanie obsahuje doménový pojem, alebo pri práci s domain/, config/, alebo s definíciou stroja v machines/*.yaml.
---

# Doménový kontext PSsimTool

## Slovník

| Pojem | Znamená | Nezamieňať s |
|---|---|---|
| **Machine** (stroj) | celá simulovaná zostava: geometria + kinematika + viazané signály | STEP súbor — ten je len geometria |
| **Joint** (kĺb) | jeden stupeň voľnosti: `parent` uzol, `child` uzol, os, typ, limity | uzol assembly — kĺb je väzba *medzi* uzlami |
| **Node** (uzol) | prvok CAD assembly tree, identifikovaný stabilnou cestou `base/portal/Part1` | OPC UA node — to je adresa premennej v PLC |
| **Signal** (signál) | jedna hodnota z PLC v čase, s vlastnou históriou vzoriek | OPC UA node — node je adresa, signál je tok hodnôt |
| **Binding** (väzba) | mapovanie `joint ↔ OPC UA node` vrátane `scale` a `offset` | joint samotný — ten o PLC nič nevie |
| **JointPose** | výsledok kinematiky: posun + (os, uhol) pre jeden kĺb | transformácia scény — tú skladá Panda3D z hierarchie |
| **Sample** (vzorka) | `(source_time, value)` — hodnota s časom vzniku v PLC | čas prijatia — ten je nepoužiteľný |
| **StateStore** | thread-safe držiteľ posledných vzoriek všetkých signálov | scéna — tá je len konzument |
| **Deflection** | tolerancia tesselácie: lineárna (mm) a uhlová (rad) | LOD — to je iná úroveň optimalizácie |
| **Stale** (zastaraný) | signál, ktorý neprišiel dlhšie ako `stale_after_s` | chýbajúci — ten sa nikdy neobjavil |
| **Render delay** | zámerné spozdenie vzorkovania, aby sa interpolovalo, nie extrapolovalo | latencia — tá je nechcená |

## Invarianty, ktoré nesmie porušiť žiadna zmena

1. **Vnútri systému je jedna jednotka: metry a radiány.** Konverzia sa deje výhradne
   na hranici (`config/loader.py`, `cad/`, `io/`). Ak nájdeš násobenie `0.001`
   v `domain/` alebo `viz/`, je to bug.
2. **Čas je vždy `SourceTimestamp` z PLC**, prevedený raz na internú monotónnu škálu
   v sekundách (`float`). Offset PLC↔lokálny čas sa určí pri prvej vzorke a **nemení sa**.
3. **`domain/` importuje výhradne stdlib.** Žiadny numpy, pydantic, panda3d, asyncua, OCP.
4. **Kĺb nikdy nemení hodnotu sám.** Hodnota prichádza zvonku. Kinematika je čistá funkcia
   `(joint, value) → JointPose`. Žiadny stav, žiadna pamäť predchádzajúceho snímku.
5. **Kinematický reťazec je strom, nikdy graf.** Každý uzol má najviac jedného rodiča.
   Cyklus a viacnásobný rodič sú `ConfigError` pri načítaní, nie runtime problém.
6. **Chýbajúci alebo zastaraný signál nikdy nezhodí render.** Zobrazí sa posledná známa
   hodnota, vizuálne označená ako zastaraná.
7. **Cache je zahoditeľná.** Nič, čo sa nedá znovu vyrobiť z `models/` + `machines/`,
   do `assets/cache/` nepatrí.
8. **Do PLC sa nezapisuje.** Aplikácia je čitateľ.
9. **Kĺb sa pohybuje relatívne k polohe z CAD.** STEP určuje, kde diel je v nule;
   hodnota z PLC pridáva pohyb na vrch. Nikdy neprepisuj CAD polohu — diel by
   pri prvej hodnote skočil do počiatku rodiča.
10. **Geometria sa kľúčuje podľa definície dielu, nie podľa cesty uzla.** Ten istý
    diel použitý desaťkrát má jeden mesh súbor a desať uzlov naň ukazuje.

## Typy kĺbov

| Typ | Hodnota signálu | Efekt |
|---|---|---|
| `prismatic` | posun v metroch | translácia po `axis` |
| `revolute` | uhol v radiánoch | rotácia okolo `axis` |
| `fixed` | ignoruje sa | žiadny pohyb, len pevný offset v hierarchii |

`axis` je jednotkový vektor v súradnicovom systéme **rodiča**, nie v globálnom.
Neznormalizovaná os je `ConfigError` — nie tichá normalizácia, lebo dĺžka vektora
by inak nenápadne škálovala pohyb.

## Životný cyklus hodnoty signálu

```
PLC premenná
  │ OPC UA subscription, SourceTimestamp
  ▼
raw hodnota (jednotky PLC: mm, stupne, inkrementy)
  │ binding: value * scale + offset        ← JEDINÉ miesto konverzie
  ▼
Sample(source_time_s, value)  v metroch / radiánoch
  │ StateStore.put()  (vlákno B, pod lockom)
  ▼
SignalBuffer — ring buffer posledných N vzoriek
  │ sample_at(t = now - render_delay)  → lineárna interpolácia
  ▼
hodnota kĺbu
  │ domain/kinematics.joint_pose(joint, value)  → clamp na limity
  ▼
JointPose(translation, rotation_axis, rotation_angle_rad)
  │ viz/ → NodePath.setPos() / setQuat()
  ▼
obrazovka
```

Každý krok tohto reťazca má vlastný test v `tests/unit/`. Keď niečo nesedí,
lokalizuj to podľa tohto zoznamu — nehľadaj od obrazovky dozadu.

## Stavy zdroja dát

```
DISCONNECTED ──► CONNECTING ──► CONNECTED ──► DEGRADED
     ▲               │              │             │
     └───────────────┴──────────────┴─────────────┘
                   (reconnect, exponenciálny backoff, strop 30 s)
```

- `CONNECTED` — spojenie žije a všetky signály sú svieže.
- `DEGRADED` — spojenie žije, ale aspoň jeden signál je `stale`. **Renderuje sa ďalej.**
- Prechod do `DISCONNECTED` **nezmaže** dáta v `StateStore` — scéna zostane stáť
  na poslednom známom stave, označená ako zastaraná.

Implementácia: `src/pssim/io/base.py` (`SourceStatus`). Každý nový stav pridaj **aj tam**,
inak sa HUD a `DEGRADED` logika obídu.

## Podrobnosti

Ak potrebuješ hlbšie detaily, prečítaj až podľa potreby:

- @referencie/opcua-mapovanie.md — schéma `machines/*.yaml`, typy OPC UA, prevody jednotiek,
  nastavenia subscription a známe zvláštnosti serverov
- @referencie/step-import.md — postup OpenCASCADE volaní, štruktúra XCAF dokumentu,
  formát cache metadát, známe patológie reálnych STEP súborov

<!--
Súbory v podadresároch skillu sa NEnačítajú automaticky. Claude ich prečíta,
až keď to bude podľa instrukcií vyššie potrebovať. To je pointa progresívneho
odhaľovania: popis skillu (1 riadok) je v kontexte vždy, telo skillu pri jeho
vyvolaní, referencie až na požiadanie.
-->

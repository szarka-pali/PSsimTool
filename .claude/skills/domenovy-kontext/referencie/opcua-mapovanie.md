# OPC UA mapovanie a schéma definície stroja

Referenčný dokument. Načítaj ho, keď pracuješ na `config/schema.py`, `config/loader.py`,
`io/opcua_source.py` alebo keď pridávaš/opravuješ `machines/*.yaml`.

## Kompletná schéma `machines/*.yaml`

```yaml
machine: priklad                  # povinné, unikátny identifikátor
description: "Portálový paletizátor"

# --- geometria ----------------------------------------------------------
step_file: models/priklad.step    # povinné, cesta relatívna ku koreňu repozitára
units: mm                         # mm | m | in — jednotky STEP súboru
tessellation:
  linear_deflection_mm: 0.5       # menšie = jemnejšie = viac trojuholníkov
  angular_deflection_rad: 0.35

# --- pripojenie ---------------------------------------------------------
# POZOR: endpoint sem patrí len ak je to lokálny mock. Reálne endpointy
# idú do prostredia (PSSIM_OPCUA_ENDPOINT) — machines/*.yaml je verzovaný.
source:
  endpoint: opc.tcp://localhost:4840/pssim/
  publishing_interval_ms: 50      # čo si žiadame; server môže vrátiť iné
  stale_after_s: 1.0              # bez novej vzorky → signál je stale
  render_delay_ms: 100            # default: 2× revidovaný publishing interval

# --- kinematika ---------------------------------------------------------
joints:
  - name: os_x
    parent: base                  # stabilná cesta uzla v assembly tree
    child: portal
    type: prismatic               # prismatic | revolute | fixed
    axis: [1, 0, 0]               # jednotkový vektor v súradnicách rodiča
    limits: [0.0, 2.5]            # v metroch (prismatic) / radiánoch (revolute)
    origin:                       # pevný offset kĺbu voči rodičovi, voliteľné
      xyz: [0, 0, 0.15]           # metry
      rpy: [0, 0, 0]              # radiány
    signal:
      node: "ns=2;s=Axes.X.ActPos"
      scale: 0.001                # raw * scale + offset → metry/radiány
      offset: 0.0
```

## Prevody jednotiek — konkrétne hodnoty

| PLC posiela | Chceme | `scale` | `offset` |
|---|---|---|---|
| mm | m | `0.001` | `0.0` |
| µm | m | `1e-6` | `0.0` |
| stupne | rad | `0.017453292519943295` | `0.0` |
| 0.001° (typické pre servo) | rad | `1.7453292519943296e-05` | `0.0` |
| inkrementy enkodéra, 4096/ot. | rad | `2*pi/4096 = 0.001533980787885` | `0.0` |
| mm s nulou v strede zdvihu ±1250 | m, nula na kraji | `0.001` | `1.25` |

`offset` sa aplikuje **po** `scale`, teda `value * scale + offset`. Toto poradie je
zafixované — nemeň ho, existujúce YAML by sa tichým spôsobom rozbili.

## Typy OPC UA a čo s nimi

| OPC UA typ | Python | Poznámka |
|---|---|---|
| `Double`, `Float` | `float` | bežný prípad pre polohy |
| `Int16/32/64`, `UInt*` | `int` | typické pre inkrementy enkodéra — `scale` je povinný |
| `Boolean` | `bool` | pre `visible`/`active` vlastnosti, nie pre kĺby |
| `String` | `str` | len na zobrazenie v HUD, nikdy ako hodnota kĺbu |
| `DateTime` | `datetime` | UTC, tz-aware. Naivný datetime je bug |
| pole (`Double[]`) | `list[float]` | zatiaľ nepodporované — rozbaľ na jednotlivé signály v PLC |

Ak signál viazaný na kĺb nie je numerický, je to `ConfigError` pri prvom prijatí,
nie `TypeError` v kinematike.

## Nastavenia subscription

```python
sub = await client.create_subscription(
    period=publishing_interval_ms,  # ms
    handler=handler,
)
handle = await sub.subscribe_data_change(
    nodes,
    queuesize=4,  # >1: pri zaostávaní dostaneme aj medziľahlé vzorky
)
```

Na čo si dať pozor:

- **Server smie revidovať intervaly.** Skutočné hodnoty sú v odpovedi
  `CreateSubscriptionResponse` / `MonitoredItemCreateResult`. `render_delay` počítaj
  z **revidovanej** hodnoty, nie zo žiadanej.
- **`queuesize=1` zahodí medziľahlé vzorky.** Pre polohové signály to znamená,
  že pri rýchlom pohybe dostaneš len koncové body a interpolácia bude „skratkovať".
- **Deadband** (`DataChangeFilter`) šetrí sieť, ale na polohovom signáli vyhladí
  drobný pohyb, ktorý možno práve chceš vidieť. Default: žiadny deadband.
- **Jedna subscription pre všetky signály.** N subscriptions = N× režijná záťaž
  na serveri a rozsypané časovanie medzi signálmi.

## Známe zvláštnosti serverov

| Server | Zvláštnosť |
|---|---|
| Siemens S7-1500 OPC UA | `SourceTimestamp` má rozlíšenie cyklu OB, nie ms. Vzorky prichádzajú „po skokoch". Nepovažuj rovnaké časy za chybu. |
| Beckhoff TwinCAT | Minimálny `publishing_interval` je zviazaný s task cycle time. Žiadosť o 10 ms na 50 ms tasku vráti 50 ms. |
| Codesys | Niektoré verzie neposielajú `SourceTimestamp` vôbec → fallback na `ServerTimestamp`. |
| KEPServerEX (gateway) | `SourceTimestamp` je čas gateway, nie PLC. Pre presné časovanie nepoužiteľné. |
| Prosys Simulation Server | Vhodný na manuálne testovanie, ale generuje ideálne dáta — nezachytíš ním problémy s časovaním. |

Ak zistíš ďalšiu zvláštnosť pri reálnom stroji, **dopíš ju sem** vrátane verzie firmware.
Toto je jediné miesto, kde sa taká informácia neztratí.

## Bezpečnosť

Zatiaľ neimplementované, ale rozhranie na to je pripravené. Keď sa to bude robiť:

- `SecurityPolicy#Basic256Sha256` + `SignAndEncrypt` je rozumný default.
- Klientský certifikát a kľúč: cesty z prostredia (`PSSIM_OPCUA_CERT`, `PSSIM_OPCUA_KEY`),
  súbory v `certs/` (negitované, v `deny` pravidlách).
- Serverový certifikát nikdy neakceptuj automaticky — trust store je explicitný.
- Prihlasovacie údaje výhradne z prostredia. Nikdy v `machines/*.yaml`.

## Diagnostika, keď „to nefunguje"

Poradie, v ktorom to overuj — zhora dolu, každý krok má vlastný nástroj:

1. **Existuje endpoint?** `uv run pssim probe opc.tcp://...` vypíše endpointy a security policies.
2. **Existuje node?** `uv run pssim probe <endpoint> --browse ns=2` vypíše dostupné nody.
3. **Prichádzajú dáta?** `uv run pssim record ... --verbose` vypisuje každú notifikáciu.
4. **Sú v správnych jednotkách?** Porovnaj raw hodnotu zo záznamu s hodnotou po `scale`.
   Stroj tisíckrát väčší/menší = zabudnutý alebo dvojitý `scale`.
5. **Hýbe sa kĺb?** Ak hodnota chodí a diel sa nehýbe, chyba je v mapovaní uzlov
   (`parent`/`child` cesta neexistuje alebo mieri na iný diel).
6. **Hýbe sa správne?** Zlá os alebo znamienko. Skús `axis: [-1, 0, 0]`.

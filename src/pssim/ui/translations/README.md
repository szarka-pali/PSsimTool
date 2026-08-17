# Preklady UI

Zdrojový jazyk je **angličtina** — texty sú tak napísané priamo v kóde. Tento
adresár obsahuje preklady do ostatných jazykov.

## Súbory

| Prípona | Čo to je | Verzovať? |
|---|---|---|
| `.ts` | XML s textami a prekladmi, edituje sa | **áno** |
| `.qm` | skompilovaná binárka, číta ju appka | **áno** — inak by si každý musel inštalovať Qt nástroje |

## Pridanie jazyka

Extrakcia textov zo zdrojákov do `.ts`:

```bash
uv run pyside6-lupdate src/pssim/ui/main_window.py src/pssim/ui/placement_dialog.py src/pssim/ui/loader.py -ts src/pssim/ui/translations/pssim_sk.ts
```

Preklad: otvor `.ts` v Qt Linguist (`uv run pyside6-linguist`), alebo ho uprav
ručne — je to čitateľné XML.

Kompilácia na `.qm`:

```bash
uv run pyside6-lrelease src/pssim/ui/translations/pssim_sk.ts
```

Nakoniec pridaj kód jazyka do `LANGUAGES` v `src/pssim/ui/i18n.py`. Bez toho ho
appka ponúkať nebude, aj keby `.qm` existoval.

## Po zmene textov v kóde

`lupdate` spusti znovu — doplní nové texty a tie, ktoré sa zmenili, označí ako
`type="unfinished"`. Staré preklady sa nezahodia.

## Na čo nezabudnúť

- Nové texty pre používateľa **vždy** obaľ do `self.tr()` (v `QObject`)
  alebo `QCoreApplication.translate("Kontext", "text")` (na module level).
  Neobalený text sa do `.ts` nedostane a nikdy sa nepreloží.
- Nespájaj vety z kúskov (`tr("Loaded") + " " + name`) — v inom jazyku môže byť
  poradie iné. Použi zástupné znaky: `tr("Loaded {0}").format(name)`.
- Logy sa **neprekladajú**. Sú pre vývojára, nie pre používateľa.

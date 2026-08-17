# Claude Code pre code-driven AI development

Príručka k tomu, ako nastaviť repozitár tak, aby v ňom agent pracoval predvídateľne —
a aby sa to isté nastavenie automaticky prenieslo na celý tím.

---

## Časť 1 — Základná myšlienka

**Kontext je kód.** Všetko, čo agent potrebuje vedieť o projekte, žije v repozitári,
prechádza code review a má git históriu. Nie v hlave seniora, nie v Notione,
nie v chate, ktorý sa zavretím okna stratí.

Z toho vyplývajú tri praktické dôsledky:

1. **Konfigurácia agenta je verzovaná.** Keď niekto zistí, že model opakovane robí tú istú
   chybu, oprava nie je „napíš mu to do promptu" — je to commit do `.claude/rules/`.
   Oprava platí odteraz pre všetkých.

2. **Overenie je automatizované, nie posudzované.** Agent musí mať príkaz, ktorý mu povie
   *áno/nie*: testy, linter, typová kontrola, build. Bez toho pracuje naslepo a ty s ním.
   Toto je jediná najdôležitejšia vec z celej príručky.

3. **Deterministické veci riešia hooky, nie instrukcie.** „Vždy naformátuj kód" v `CLAUDE.md`
   je rada, ktorú model občas vynechá. Hook sa spustí vždy.

Rozdiel medzi tímom, ktorému Claude Code šetrí čas, a tímom, ktorý ho preň opravuje,
je takmer vždy v tomto: má repozitár zabudované overovanie, alebo nie.

---

## Časť 2 — Prehľad súborov

```
projekt/
├── CLAUDE.md                       ← vždy v kontexte. Krátky. Príkazy + hranice.
├── .mcp.json                       ← externé nástroje (GitHub, DB, browser)
├── .worktreeinclude                ← čo skopírovať do nového worktree
├── .gitignore                      ← pridaj .claude/settings.local.json
└── .claude/
    ├── settings.json               ← oprávnenia + hooky (verzované, tímové)
    ├── settings.local.json         ← osobné (NEverzované)
    ├── rules/                      ← modulárne pravidlá, načítané podľa cesty
    │   ├── code-style.md
    │   ├── testing.md
    │   └── git-workflow.md
    ├── agents/                     ← subagenti s vlastným kontextom
    │   ├── code-reviewer.md
    │   ├── explorer.md
    │   └── test-writer.md
    ├── skills/                     ← workflow postupy + dlhé referencie
    │   └── nova-funkcia/SKILL.md
    └── hooks/                      ← skripty, ktoré bežia vždy
        ├── format-and-lint.sh
        └── block-dangerous-bash.sh
```

### Kľúčová tabuľka: čo patrí kam

Toto je najčastejšia chyba pri nastavovaní — všetko sa nasype do `CLAUDE.md`,
ten narastie na 600 riadkov a model ho prestane rešpektovať.

| Typ informácie | Kam | Kedy sa načíta | Cena za kontext |
|---|---|---|---|
| Build/test príkazy, hranice architektúry, čo needitovať | `CLAUDE.md` | vždy, pri štarte | **vysoká** — každá session |
| Štýl kódu pre jazyk, pravidlá pre testy | `.claude/rules/*.md` s `paths:` | až keď sa dotkne matchujúceho súboru | nízka |
| Opakovaný postup (implementuj funkciu, oprav bug, release) | `.claude/skills/*/SKILL.md` | popis vždy, telo pri vyvolaní | veľmi nízka |
| Dlhá referencia (doména, protokol, legacy systém) | skill + podadresár `referencie/` | až na požiadanie | takmer nulová |
| Špecializovaná rola s vlastným kontextom | `.claude/agents/*.md` | pri delegovaní | izolované okno |
| Nemenná záruba (formátovanie, blokovanie príkazov) | hook v `settings.json` | pri udalosti | **nulová** |
| Oprávnenia, čo smie bez opýtania | `.claude/settings.json` | vždy | nulová |

**Pravidlo:** ak sa informácia použije v menej ako polovici sessions, nepatrí do `CLAUDE.md`.

---

## Časť 3 — `CLAUDE.md` v detaile

Načíta sa pri každom štarte. Preto: **cieľ pod 150 riadkov**, nikdy nad 200.
Nad tou hranicou model prestáva pravidlá dodržiavať — nie preto, že by ich „nevidel",
ale preto, že v mori 400 riadkov sa dôležité stráca medzi nedôležitým.

### Hierarchia (načítava sa všetko, od najvšeobecnejšieho)

| Umiestnenie | Účel | Verzovať |
|---|---|---|
| `/etc/claude-code/CLAUDE.md` (Linux) | firemné pravidlá, spravuje IT | — |
| `~/.claude/CLAUDE.md` | tvoje osobné preferencie, všetky projekty | ne |
| `./CLAUDE.md` alebo `./.claude/CLAUDE.md` | pravidlá projektu | **áno** |
| `./CLAUDE.local.md` | tvoje poznámky k tomuto projektu | ne (gitignore) |
| `./podadresar/CLAUDE.md` | pravidlá pre monorepo balíček | **áno** |

V monorepe funguje vnorenie: `packages/api/CLAUDE.md` sa načíta až keď Claude siahne
na súbor v `packages/api/`. Toto je správny spôsob, ako riešiť monorepo — nie jeden
gigantický súbor na roote.

### Čo do neho písať

Test užitočnosti riadku: **„zistil by to Claude z kódu za 5 sekúnd?"** Ak áno, vymaž ho.

Patrí sem:

- **Príkazy.** Ako sa spustí build, testy (rýchle vs. všetky), lint, typecheck.
  Toto je najcennejší obsah celého súboru.
- **Explicitná veta o overení.** Napr.: *„Po každej zmene spusti `make lint && make test-unit`.
  Ak neprejde, oprav to pred tým, než ohlásiš hotovo."*
- **Hranice architektúry**, ktoré z kódu nie sú zjavné („doména neimportuje framework").
- **Čo sa needituje.** Generovaný kód, lock files, prod infra.
- **Referenčný vzor.** „Modul `src/domain/orders/` je náš etalón — napodobňuj ho."
- **Čo robiť pri nejasnosti.** Spýtať sa? Zvoliť si a poznamenať? Toto naozaj funguje.

Nepatrí sem:

- Výpis štruktúry adresárov generovaný `tree` (Claude si ju prečíta sám).
- Zjavnosti typu „projekt používa TypeScript".
- Dokumentácia knižníc — odkáž na URL alebo daj do skillu.
- Vecí, čo sa menia každý týždeň (aktuálny sprint, kto na čom robí).
- Dlhé eseje o filozofii kódu. Píš pravidlá, nie manifest.

### Praktický tip

`/init` vygeneruje prvú verziu automaticky. Ber ju ako **koncept, nie výsledok** —
zvyčajne je príliš dlhá a príliš popisná. Preškrtaj ju na polovicu.

Potom to udržuj živé: kedykoľvek agent urobí chybu, ktorú by pravidlo zachytilo,
pridaj ten jeden riadok. Po mesiaci máš súbor, ktorý zachytáva skutočné bolestivé
miesta projektu, nie tie, ktoré si na začiatku tipoval.

---

## Časť 4 — `.claude/rules/` — modulárne pravidlá

Rozdiel proti `CLAUDE.md` je jeden a je dôležitý:

```yaml
---
paths:
  - "tests/**"
  - "**/*.test.ts"
---
```

Pravidlo s `paths:` sa načíta **až keď** Claude čítá alebo edituje matchujúci súbor.
Pravidlá pre DB migrácie ťa teda nestoja nič, kým nerobíš migrácie.
Bez `paths:` sa načíta vždy — má rovnakú prioritu ako `CLAUDE.md`, len je to tematicky rozdelené.

Praktické rozdelenie, ktoré sa osvedčuje:

| Súbor | `paths:` | Obsah |
|---|---|---|
| `code-style.md` | `src/**` | pomenovanie, veľkosť funkcií, chyby, komentáre |
| `testing.md` | `tests/**` | štruktúra testov, čo mockovať, čo je zakázané |
| `git-workflow.md` | *(žiadne)* | branch, commity, PR — platí vždy |
| `migrations.md` | `migrations/**` | nikdy needituj zmergovanú migráciu, ... |
| `api-contract.md` | `src/api/**` | verziovanie, breaking changes, chybové kódy |

To isté funguje v `~/.claude/rules/` pre pravidlá platné vo všetkých tvojich projektoch.

---

## Časť 5 — Skills: postupy a dlhé referencie

Skill má dve úplne odlišné použitia a obe sú užitočné.

### A) Workflow skill — zakódovaný postup

Namiesto toho, aby si pri každej funkcii písal ten istý dlhý prompt, napíšeš ho raz.
Potom stačí `/nova-funkcia pridaj storno objednávky`.

Dobrý workflow skill má **fázy a stop-body**:

```
Fáza 1  Pochopenie — nič needituj, nájdi analogický modul, prečítaj ho
Fáza 2  Plán — napíš plán v danom formáte  →  STOP, čakaj na schválenie
Fáza 3  Testy najprv — ukáž, že padajú a PREČO padajú
Fáza 4  Implementácia — najmenšia zmena, ktorá testy rozsvieti
Fáza 5  Uzavretie — celá sada, review, commit, zhrnutie čo nie je pokryté
```

Ten stop-bod po fáze 2 je najcennejší riadok. Schváliť plán trvá 30 sekúnd;
zahodiť 200 riadkov nesprávneho kódu trvá hodinu a stojí náladu.

### B) Reference skill — dlhý materiál, ktorý sa načíta až keď treba

Toto je odpoveď na otázku *„kam dám 800 riadkov popisu našej domény?"*.

```
.claude/skills/domenovy-kontext/
├── SKILL.md              ← slovník, invarianty, stavové prechody (~100 riadkov)
└── referencie/
    ├── api-protokol.md   ← 500 riadkov, načíta sa až keď treba
    └── legacy.md         ← 900 riadkov, načíta sa až keď treba
```

Trojúrovňové odhaľovanie:

1. **Popis skillu** (jeden riadok frontmatteru) — v kontexte vždy. Podľa neho sa Claude
   rozhodne, či skill vôbec otvoriť. Preto ho napíš ako *kedy toto použiť*, nie *čo to je*.
2. **Telo `SKILL.md`** — načíta sa pri vyvolaní.
3. **Súbory v podadresároch** — až keď si ich Claude vyžiada podľa instrukcií v tele.

Takto môžeš mať v repozitári 3000 riadkov doménových znalostí, ktoré ťa v bežnej
session nestoja nič.

> Poznámka: `.claude/commands/*.md` a `.claude/skills/*/SKILL.md` robia dnes to isté —
> oboje vytvorí `/nazov`. Pre nové veci používaj `skills/`, lebo znesie podadresáre a referencie.

---

## Časť 6 — Subagenti: izolácia kontextu

Subagent dostane **vlastné kontextové okno** a späť vráti len zhrnutie.
Presne to je jeho hodnota, nie „paralelizmus".

Použi ho vtedy, keď platí: *veľa čítania → málo výstupu*.

| Agent | Načo | Čo mu zakázať |
|---|---|---|
| `explorer` | prehľadať 40 súborov a odpovedať na otázku | editáciu (`tools: Read, Grep, Glob`) |
| `code-reviewer` | nezávislá revízia diffu | editáciu — má reportovať, nie opravovať |
| `test-writer` | doplniť testy | zmenu produkčného kódu |

Dve veci, ktoré rozhodujú o tom, či agent bude užitočný:

**1. `description` určuje, kedy ho Claude použije.** Píš ho ako spúšťač, nie ako titul.
Nie *„agent na revíziu kódu"*, ale *„Použi po dopísaní funkcionality, keď treba nezávislý
pohľad na správnosť a bezpečnosť. Nič needituje."*

**2. Presne definovaný formát výstupu.** Agent bez predpísaného výstupu vráti tri odstavce
vaty. Predpíš mu šablónu — a povedz mu, čo má robiť, keď nič nenájde
(*„napíš to priamo a uveď, čo si skontroloval; nevymýšľaj nálezy"*).

Užitočná kombinácia pre reviewera: `model: opus` na kritické hodnotenie
a zákaz editačných nástrojov, aby nemal možnosť „popri tom" niečo prepísať.

---

## Časť 7 — Oprávnenia a hooky: záruby, nie rady

### Oprávnenia (`.claude/settings.json`)

Cieľ nie je maximálna bezpečnosť — je to **odstránenie klikania na potvrdzovanie**
pri veciach, ktoré sú zjavne bezpečné, aby ti zostala pozornosť na tie, čo bezpečné nie sú.

```json
"permissions": {
  "allow": ["Bash(make *)", "Bash(git diff *)", "Edit(src/**)"],
  "ask":   ["Bash(git push *)", "Bash(npm install *)"],
  "deny":  ["Read(**/.env*)", "Edit(src/generated/**)", "Bash(git push --force*)"]
}
```

Čo treba vedieť o syntaxi:

- `Bash(make *)` — prefixový match; mezera pred `*` znamená hranicu slova.
- `Read(**/.env)` — gitignore-štýl glob; `**` prechádza adresáre.
- `Read(~/.ssh/**)` — `~/` je domovský adresár, `//cesta` je absolútna cesta od roota.
- `WebFetch(domain:*.internal.firma.sk)` — obmedzenie na domény.
- `mcp__github__*` — všetky nástroje z MCP servera.
- Zložené príkazy (`a && b`) sa vyhodnocujú **po častiach** — každá musí prejsť samostatne.

Poradie a zlučovanie: pravidlá sa **zlučujú** cez všetky úrovne (firemné → projekt → lokálne),
ale `deny` má vždy prednosť. Čo je zakázané v projektových settings, nikto si lokálne nepovolí.

**Nepoužívaj `--dangerously-skip-permissions` na svojom stroji.** Má miesto v CI
alebo v kontejneri, kde ti ani úplne odviazaný agent nemá čo pokaziť.
Na lokále to znamená, že sa zbavíš presne tej kontroly, ktorá ťa má chrániť.
Namiesto toho investuj 20 minút do dobrého `allow` zoznamu — dosiahneš 90 % pohodlia
bez toho rizika.

### Hooky

Hook je shell skript, ktorý sa spustí pri udalosti. Rozdiel proti instrukcii v `CLAUDE.md`:
**hook sa vykoná vždy**, model ho nemôže vynechať ani „zabudnúť".

Užitočné udalosti:

| Udalosť | Kedy | Typické použitie |
|---|---|---|
| `PreToolUse` | pred volaním nástroja | zablokovať nebezpečný príkaz (exit 2) |
| `PostToolUse` | po volaní nástroja | naformátovať a nalintovať zmenený súbor |
| `UserPromptSubmit` | pri odoslaní promptu | pridať kontext (aktuálny branch, ticket) |
| `Stop` | keď chce Claude skončiť | vynútiť spustenie testov pred ukončením |
| `SessionStart` | na začiatku session | vypísať stav gitu, pripomenúť pravidlá |

Kontrakt: JSON na stdin, `exit 0` = pokračuj, `exit 2` = **zablokuj** (text na stderr
sa vráti Claudovi ako dôvod), iné nenulové = varovanie bez blokovania.

Dva hooky, ktoré sa vyplatia takmer v každom projekte:

1. **Formátovanie po každej editácii.** Diff prestane obsahovať šum a prestaneš
   opakovať „naformátuj to".
2. **Blokovanie deštruktívnych príkazov** na základe stavu, ktorý sa vzorom vyjadruje
   ťažko — napr. „push je zakázaný, ak je aktuálny branch `main`".

Nechápaj hooky ako bezpečnostnú hranicu proti zlomyseľnému aktérovi. Sú to poistky
proti nešťastnej náhode. Skutočnú ochranu rieš oprávneniami a prostredím.

---

## Časť 8 — Práca so session: čo naozaj mení výsledok

### Plánuj skôr, než sa začne písať

`Shift+Tab` prepína režimy oprávnení; **plan mode** je najužitočnejší. Claude v ňom
len čítá a navrhne plán. Schváliš, alebo ho pošleš späť.

Pri čomkoľvek, čo sa dotkne viac ako dvoch-troch súborov, sa to vypláca vždy.
Model, ktorý začne písať skôr než pochopí zadanie, netvorí kód — tvorí ti prácu.

### Kontext je vyčerpateľný zdroj

- `/clear` **medzi nesúvisiacimi úlohami.** Dokončil si funkciu a ideš na iný bug?
  `/clear`. Zvyšky predchádzajúcej úlohy kvalitu len zhoršujú.
- `/compact` keď je konverzácia dlhá, ale téma pokračuje. Môžeš dať instrukciu,
  čo si má ponechať: `/compact ponechaj rozhodnutia o návrhu a zoznam zmenených súborov`.
- `/context` ukáže, čím je kontext zaplnený. Keď ti to príde príliš, pozri sa sem —
  často je vinník práve prerastený `CLAUDE.md`.
- **Prieskum deleguj na subagenta.** „Kde sa spracúvajú webhooky?" nech prehľadá
  `explorer` a vráti tri riadky — nie tvoja hlavná session, ktorá si tým naplní pol okna.

### Overenie namiesto opisu

Toto je rozdiel medzi promptom, ktorý funguje, a promptom, ktorý nefunguje:

> ❌ „pridaj validáciu hesla"

> ✅ „Do `src/auth/validate.py` pridaj `validate_password`. Musí odmietnuť: prázdne,
> kratšie ako 12 znakov, bez čísla, zo zoznamu najčastejších hesiel. Prijať: čokoľvek ostatné.
> Testy do `tests/unit/auth/test_validate.py`, vzor si vezmi z `test_validate_email.py`.
> Spusti `make test-unit` a ukáž výstup."

Rozdiel nie je v dĺžke. Je v tom, že druhý prompt obsahuje **kritérium úspechu,
ktoré si vie agent sám skontrolovať**. Pri prvom sa dozvieš, či to funguje, až ty sám.

### Keď to ide zle

- `Esc` — zastav ho hneď, ako vidíš, že ide nesprávnym smerom. Nečakaj na dokončenie.
- `Esc Esc` alebo `/rewind` — vráť kód aj konverzáciu na predchádzajúci bod.
  Pozor: **zmeny vykonané cez bash sa nesledujú** (`rm`, `mv`), tie rewind nevráti. Commituj často.
- **Po druhej neúspešnej korekcii `/clear` a preformuluj zadanie.** Tretie „nie, myslel som..."
  v tej istej konverzácii takmer nikdy nepomôže — kontext je už zamorený nesprávnymi pokusmi.
  Napíš zadanie znova, obohatené o to, čo si sa medzitým dozvedel.

### Paralelná práca

`claude --worktree feat-storno` vytvorí izolovaný git worktree. Dve session na dvoch
funkciách si navzájom nešliapu po súboroch. `.worktreeinclude` zabezpečí,
že sa do nového worktree skopíruje `.env` a podobné negitované súbory.

### Užitočné príkazy

| Príkaz | Načo |
|---|---|
| `/init` | vygeneruj prvú verziu `CLAUDE.md` |
| `/context` | čím je zaplnený kontext |
| `/clear`, `/compact` | reset / kompresia konverzácie |
| `/rewind` | vráť kód alebo konverzáciu späť |
| `/code-review` | revízia diffu vo vlastnom kontexte |
| `/security-review` | kontrola bezpečnostných problémov |
| `/permissions` | prehľad a úprava oprávnení |
| `/agents`, `/skills` | čo je k dispozícii |
| `/mcp` | stav MCP serverov |
| `/model`, `/effort` | model a úroveň uvažovania |
| `/usage`, `/cost` | spotreba |

---

## Časť 9 — Zavedenie v tíme

Nerob to naraz. Postupnosť, ktorá funguje:

**Týždeň 1 — základ.** `/init`, preškrtaj výsledok na polovicu, doplň sekciu s príkazmi
a explicitnú vetu o overení. Commituj. Toto samo o sebe dá 70 % efektu.

**Týždeň 2 — oprávnenia.** Pozri sa, čo najčastejšie potvrdzuješ, a dopĺň to do `allow`.
Zároveň napíš `deny` na to, čo sa nikdy nemá stať. Commituj.

**Týždeň 3 — prvý hook.** Formátovanie po editácii. Diffy sa vyčistia.

**Týždeň 4 — reviewer a prvý skill.** Agent `code-reviewer` a workflow skill pre postup,
ktorý v tíme robíte najčastejšie.

**Priebežne.** Zaveď zvyk: keď agent zopakuje tú istú chybu druhýkrát, nie je to jeho chyba,
ale chýbajúci riadok v `rules/`. Kto na to príde, ten to commitne.

### Čo verzovať

| Verzuj | Negitni |
|---|---|
| `CLAUDE.md` | `.claude/settings.local.json` |
| `.claude/settings.json` | `CLAUDE.local.md` |
| `.claude/rules/`, `agents/`, `skills/`, `hooks/` | `.claude/worktrees/` |
| `.mcp.json`, `.worktreeinclude` | `.claude/agent-memory-local/` |

Keď kolega naklonuje repozitár, jeho agent sa chová rovnako ako tvoj. To je celý zmysel.

---

## Časť 10 — Anti-patterny

| Anti-pattern | Prečo je to problém | Namiesto toho |
|---|---|---|
| `CLAUDE.md` na 500 riadkov | model prestane pravidlá dodržiavať | rozdeľ do `rules/` s `paths:` |
| Žiadny testovací príkaz | agent nemá ako zistiť, či to funguje | aspoň jeden rýchly `make test-unit` |
| `--dangerously-skip-permissions` na lokále | zbavíš sa presne tej kontroly, čo ťa chráni | dobrý `allow` zoznam |
| Jedna session na celý deň | kontext sa zamorí, kvalita klesá | `/clear` medzi úlohami |
| „Rob to správne" ako pravidlo | nevykonateľné, model si to vysvetlí ako chce | konkrétne pravidlá + linter |
| Rovnaký dlhý prompt písaný stále odznova | plytvanie a nekonzistentnosť | workflow skill |
| Prieskum v hlavnej konverzácii | 40 prečítaných súborov v kontexte | subagent, vráti zhrnutie |
| Commit až na konci veľkej zmeny | nie je kam sa vrátiť | commit po každom prejdenom kroku |
| Instrukcia „vždy naformátuj" | model to niekedy vynechá | hook |
| Pravidlá len v hlave seniora | nefunguje pre nikoho iného | commit do `rules/` |
| Doména celá v `CLAUDE.md` | 800 riadkov v každej session | reference skill |

---

## Časť 11 — Kontrolný zoznam

Základ:

- [ ] `CLAUDE.md` existuje, má pod 200 riadkov, obsahuje príkazy na build/test/lint
- [ ] Je v ňom explicitná veta *„po zmene spusti X; ak neprejde, oprav to"*
- [ ] Je v ňom, čo sa **needituje** (generovaný kód, lock files, prod infra)
- [ ] Existuje rýchly testovací príkaz, ktorý beží do niekoľkých sekúnd
- [ ] `.claude/settings.json` má `allow` na časté bezpečné príkazy
- [ ] `.claude/settings.json` má `deny` na tajomstvá a deštruktívne operácie
- [ ] `.claude/settings.local.json` je v `.gitignore`

Ďalšia úroveň:

- [ ] Pravidlá rozdelené do `.claude/rules/` s `paths:`
- [ ] Hook na formátovanie po editácii
- [ ] Agent `code-reviewer` bez editačných práv
- [ ] Workflow skill pre najčastejší postup v tíme
- [ ] Doménová referencia ako skill, nie v `CLAUDE.md`
- [ ] `.mcp.json` bez natvrdo zapísaných tokenov (len `$PREMENNÁ`)
- [ ] `.worktreeinclude` pre paralelnú prácu

Prevádzka:

- [ ] Tím vie, že opravou opakovanej chyby je commit do `rules/`, nie lepší prompt
- [ ] Pri väčších zmenách sa používa plan mode
- [ ] `/clear` medzi nesúvisiacimi úlohami je zvyk

---

## Zdroje

Aktuálna dokumentácia — schémy a kľúče sa vyvíjajú, pri detailoch si over stav:

- [Prehľad](https://code.claude.com/docs/en/overview) ·
  [Quickstart](https://code.claude.com/docs/en/quickstart) ·
  [Best practices](https://code.claude.com/docs/en/best-practices)
- [Memory / CLAUDE.md](https://code.claude.com/docs/en/memory) ·
  [Settings](https://code.claude.com/docs/en/settings) ·
  [Permissions](https://code.claude.com/docs/en/permissions)
- [Skills](https://code.claude.com/docs/en/skills) ·
  [Subagenti](https://code.claude.com/docs/en/sub-agents) ·
  [Hooks](https://code.claude.com/docs/en/hooks)
- [MCP](https://code.claude.com/docs/en/mcp) ·
  [Worktrees](https://code.claude.com/docs/en/worktrees) ·
  [CLI reference](https://code.claude.com/docs/en/cli-reference)

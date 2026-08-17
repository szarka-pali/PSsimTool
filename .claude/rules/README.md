# .claude/rules/

Modulárna alternatíva k jednému veľkému `CLAUDE.md`.

Rozdiel:

- **`CLAUDE.md`** — načíta sa **vždy**, pri každom štarte session. Preto krátky.
- **`rules/*.md` bez `paths:`** — načíta sa tiež vždy (rovnaká priorita ako `CLAUDE.md`),
  len je to tematicky rozdelené do súborov.
- **`rules/*.md` s `paths:`** — načíta sa **až keď** Claude siahne na súbor,
  ktorý matchuje glob. Toto je ten dôvod, prečo rules existujú: pravidlá pre OPC UA
  ťa nestoja kontext, kým nerobíš na `io/`.

## Čo je v tomto projekte

| Súbor | `paths:` | Kedy sa načíta |
|---|---|---|
| `git-workflow.md` | — | vždy |
| `code-style.md` | `src/**` | pri práci na produkčnom kóde |
| `testing.md` | `tests/**` | pri práci na testoch |
| `io-opcua.md` | `src/pssim/io/**`, `tests/integration/**` | pri práci na zdrojoch dát |
| `cad-import.md` | `src/pssim/cad/**` | pri práci na importe geometrie |

Dlhý referenčný materiál (doménový slovník, mapovanie OPC UA signálov, poznámky k API
OpenCASCADE) **nie je** v rules — je v `.claude/skills/domenovy-kontext/`, lebo skill
sa načíta až keď je relevantný a jeho referencie až na požiadanie.

## Ako to rozdeliť pri pridávaní ďalších pravidiel

| Obsah | Kam |
|---|---|
| Príkazy na build/test, hranice architektúry, čo needitovať | `CLAUDE.md` |
| Štýl kódu pre konkrétny jazyk alebo vrstvu | `rules/*.md` s `paths:` |
| Pravidlá pre testy | `rules/testing.md` |
| Dlhý referenčný materiál (protokol, doména, cudzie API) | **skill**, nie rule |

Podadresáre sú povolené a na načítanie nemajú vplyv — sú len na organizáciu.
Rovnaká štruktúra funguje aj v `~/.claude/rules/` pre pravidlá platné vo všetkých projektoch.

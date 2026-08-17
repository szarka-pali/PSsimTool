---
name: nova-funkcia
description: Riadený postup implementácie novej funkcionality — od zadania cez plán a testy po commit. Použi, keď treba pridať novú funkciu, endpoint, modul alebo väčšiu zmenu správania.
---

# Nová funkcionalita: $ARGUMENTS

Postupuj po fázach. **Po fáze 2 sa zastav a nechaj si plán schváliť.**
Neposúvaj sa dopredu, kým predchádzajúca fáza nie je hotová.

## Fáza 1 — Pochopenie (nič needituj)

1. Zisti, kde v codebase zmena patrí. Nájdi **najbližší analogický existujúci modul**
   a prečítaj ho celý — to je tvoj vzor.
2. Nájdi, kto bude nový kód volať, a čo bude volať on.
3. Prečítaj existujúce testy analogického modulu.
4. Napíš, čo si zistil: dotknuté súbory, existujúci vzor, ktorý budeš nasledovať.

**Ak je zadanie nejednoznačné, spýtaj sa TERAZ.** Konkrétne otázky, nie „chceš, aby som pokračoval".
Typicky nejasné: chybové stavy, spätná kompatibilita, správanie pri prázdnom vstupe,
kto to smie volať, čo sa má logovať.

## Fáza 2 — Plán (nič needituj)

Napíš plán v tomto formáte:

```
Cieľ:        jedna veta, merateľná.
Nemení sa:   čo výslovne zostáva ako je (verejné API, schéma, ...).
Kroky:
  1. [súbor] čo tam pridám/zmením
  2. ...
Testy:       konkrétne prípady, ktoré budú dokazovať, že to funguje.
Riziká:      čo sa môže pokaziť inde v systéme.
Otvorené:    rozhodnutia, ktoré potrebujem od človeka.
```

Ak plán presahuje ~8 krokov alebo ~5 súborov, navrhni rozdelenie na samostatné PR.

**STOP. Čakaj na schválenie plánu.**

## Fáza 3 — Testy najprv

Napíš testy z fázy 2. Spusti ich. **Ukáž, že padajú, a prečo padajú** —
padajúci test, ktorý padá z nesprávneho dôvodu (import error, typo), nič nedokazuje.

## Fáza 4 — Implementácia

- Rob **najmenšiu** zmenu, ktorá testy rozsvieti. Žiadna funkcionalita „do zásoby".
- Po každom logickom kroku spusti rýchle testy a lint.
- Ak zistíš, že plán bol zlý, **zastav sa a povedz to** — neimprovizuj potichu iný návrh.

## Fáza 5 — Uzavretie

1. Spusti celú testovaciu sadu, lint a typovú kontrolu. Ukáž výstup.
2. Prejdi `git diff` a odstráň: debug výpisy, zakomentovaný kód, nesúvisiace zmeny formátovania.
3. Nechaj to skontrolovať agentom `code-reviewer` a nálezy vyrieš alebo vysvetli, prečo ich ignoruješ.
4. Commituj podľa `.claude/rules/git-workflow.md`.
5. Zhrnutie: čo je hotové, čo nie je pokryté, čo si zámerne nechal na neskôr.

---
name: oprav-bug
description: Systematický postup ladenia a opravy chyby — reprodukcia, izolácia príčiny, regresný test, oprava. Použi pri hlásení bugu, padajúcom teste, chybe v logu alebo nesprávnom výstupe.
---

# Bug: $ARGUMENTS

Pravidlo, ktoré neobchádzaj: **najprv reprodukcia, potom až oprava.**
Oprava bez reprodukcie je hádanie a v polovici prípadov opraví niečo iné.

## 1. Reprodukcia

- Zisti presné podmienky: vstup, stav systému, verzia, prostredie.
- Napíš **padajúci test**, ktorý bug zachytáva. Spusti ho, ukáž výstup.
- Ak sa bug nedá reprodukovať, **povedz to** a napíš, aké informácie potrebuješ.
  Nepokračuj na základe domnienky, čo asi je zlé.

## 2. Izolácia príčiny

- Zúž to na najmenší kus kódu, kde sa správanie mení. Použi logovanie alebo
  `git bisect`, nie čítanie kódu odhora nadol.
- Odpovedz na otázku: **prečo** to zlyhá, nie len kde. „Tu je `None`" nie je príčina —
  príčina je, kto tam to `None` pustil a prečo.
- Ak sú príčiny dve, hlás obe.

## 3. Rozsah škody

Predtým než opravíš, zisti:

- Je ten istý vzor **použitý inde**? (`Grep`) Ak áno, uveď všetky miesta.
- Ide o dátovú chybu, ktorá už zanechala nekonzistentné dáta v DB?
- Existuje dôvod, prečo je kód taký, aký je (starý ticket, zámerné správanie)?

## 4. Oprava

- Oprav **príčinu, nie symptóm**. Ak pridávaš `if x is None`, spýtaj sa, prečo je `x` None.
- Najmenšia možná zmena. Nerefaktoruj popri tom.
- Test z bodu 1 musí prejsť, **všetky ostatné testy musia stále prechádzať**.

## 5. Uzavretie

- Regresný test zostáva v repozitári natrvalo.
- Commit správa obsahuje: čo bolo zlé, prečo, a ako to teraz funguje.
- Ak si našiel podobné výskyty inde a neopravil si ich, **explicitne ich vypíš**.

## Antipatterny, ktorým sa vyhni

- Skúšanie náhodných zmien, kým to prestane padať.
- Rozšírenie `try/except`, aby chyba zmizla z logu.
- Úprava testu, aby prešiel, namiesto opravy kódu.
- Oprava troch nesúvisiacich vecí v jednom commite.

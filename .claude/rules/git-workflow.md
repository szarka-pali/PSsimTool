---
description: Ako pracujeme s gitom (platí vždy)
---

# Git workflow

## Branch

- Nikdy nepracuj priamo na `main`. Ak si na nej, najprv vytvor branch.
- Názov: `typ/kratky-popis`, napr. `feat/opcua-subscription`, `fix/mm-na-metre-dvojity-prevod`.
  Typy: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.

## Commity

- **Malé a atomické.** Jedna logická zmena = jeden commit. Refaktor a nová funkcia sú dva commity.
- Formát správy: Conventional Commits. Scope je názov vrstvy (`domain`, `io`, `cad`, `viz`, `config`, `ui`).
  ```
  feat(io): pridaj OPC UA subscription so source timestampmi

  Prečo: polling v render loope spôsoboval prepady fps pri >20 signáloch.
  Viď docs/architecture.md R4.
  ```
- Prvý riadok max 72 znakov, v rozkazovacom spôsobe, bez tečky na konci.
- Telo commitu vysvetľuje **prečo**, nie čo — diff už hovorí čo.
- Commituj **po každom prejdenom kroku**, nie raz na konci veľkej zmeny.
  Dáva mi to body, kam sa dá vrátiť.
- Ak si písal kód proti API knižnice, ktoré si musel overiť (`asyncua`, `OCP`),
  napíš do tela commitu **ako si ho overil**. Ušetrí to ďalšiemu človeku hodinu.

## Čo nikdy

- `git push --force` na zdieľaný branch.
- `git commit --amend` na už pushnutý commit.
- Commit s neprejdeným `ruff check` alebo `pytest tests/unit`.
- Commit obsahujúci `.env`, certifikáty, adresy PLC zákazníka alebo tokeny.
- Commit **CAD súborov** (`models/**`), **tesselovaných meshov** (`assets/cache/**`)
  alebo **záznamov** (`recordings/**`). Sú veľké a generované alebo dôverné.
  Záznamy z reálneho stroja môžu obsahovať údaje zákazníka.
- `git add .` bez toho, aby si sa najprv pozrel na `git status`.

## PR

- Popis PR: čo, prečo, ako to otestovať. Odkaz na ticket.
- Ak sa zmena týka správania voči PLC, uveď, **proti čomu si to overil**
  (mock server / záznam / reálny stroj a aký).
- Ak PR presiahne ~400 riadkov diffu, navrhni rozdelenie skôr, než ho otvoríš.

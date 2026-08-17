"""Doménová vrstva — čistá logika bez externých závislostí.

Tento balík importuje **výhradne stdlib**. Žiadny numpy, pydantic, panda3d,
asyncua ani OCP. Dôvod je praktický: kinematiku a interpoláciu treba testovať
bez otvárania okna a bez PLC. Viď CLAUDE.md a docs/architecture.md.
"""

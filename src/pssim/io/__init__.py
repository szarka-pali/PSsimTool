"""Vrstva zdrojov dát.

Jediné miesto v projekte, kde beží asyncio a kde existuje viac ako jedno vlákno.
Pravidlá sú v `.claude/rules/io-opcua.md` — prečítaj si ich pred zmenou.
"""

from pssim.io.base import DataSource, SourceStatus
from pssim.io.store import StateStore

__all__ = ["DataSource", "SourceStatus", "StateStore"]

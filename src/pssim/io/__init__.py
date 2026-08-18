"""The data source layer.

The only place in the project where asyncio runs and where more than one thread
exists. The rules are in `.claude/rules/io-opcua.md` — read them before changing
anything here.
"""

"""CAD geometry import.

The rules are in `.claude/rules/cad-import.md`, the sequence of OpenCASCADE calls in
`.claude/skills/domenovy-kontext/referencie/step-import.md`.

`OCP` is a heavy import (hundreds of MB) — it is imported inside functions, never at
module level, so that the unit tests and `pssim --help` do not pay for loading it.
"""

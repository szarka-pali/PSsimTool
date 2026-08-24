# PSsimTool

## What this is

A desktop application for **real-time 3D machine simulation**. It loads machine geometry
from CAD files (STEP at minimum), assembles a kinematic hierarchy from it according to a
machine definition, and drives the moving parts with **live values from a PLC over OPC UA**
— positions, rotations and object properties.

The user is an engineer commissioning a machine, or a PLC programmer who needs to see what
the control program is actually doing. This is not a game and not a physics simulator:
**the data comes from the PLC, the application only displays it.** It computes no dynamics
of its own.

System boundary: the application is an **OPC UA client**. It contains no PLC logic and does
not edit CAD. It writes to the PLC in exactly one case — a sensor's own reading, and only when
*Allow writing* has been deliberately turned on (see `docs/architecture.md` R19). That switch
is off by default and the write path is tested **exclusively** against `pssim mock-server`.

## Commands

The project runs on **Python 3.12** (not newer — `panda3d` and `cadquery-ocp` have no wheels
for 3.13+). `uv` fetches the right version itself.

| Purpose | Command |
|---|---|
| Install dependencies | `uv sync --all-extras` |
| Run the desktop app | `uv run pssim ui` |
| Run against a live PLC | `uv run pssim run machines/example.yaml` |
| Mock PLC (second terminal) | `uv run pssim mock-server` |
| **Tests (fast)** | `uv run pytest tests/unit -q` |
| Tests against the mock PLC | `uv run pytest -m integration` |
| STEP import tests | `uv run pytest -m cad` (needs `uv sync --extra cad`) |
| Window tests (headless) | `uv run pytest -m ui` (needs `uv sync --extra ui`) |
| Tests (all, slow) | `uv run pytest` |
| Lint + format | `uv run ruff format . && uv run ruff check --fix .` |
| Type check | `uv run pyright` |
| Import a STEP file into the cache | `uv run pssim import-step <file.step>` |
| Build a distribution | `uv run python setup_dist.py bdist_apps` |

> After every code change run `uv run ruff check . && uv run pytest tests/unit -q`.
> If it does not pass, fix it before reporting back to me.

## Structure

```
src/pssim/
  domain/       pure logic: machine model, kinematics, interpolation, units, errors
                DOES NOT IMPORT panda3d, asyncua, pydantic or OCP
  config/       pydantic schemas for the YAML machine definitions + loader into the domain model
  io/           data sources: OPC UA client, replay, mock server, thread-safe state store
  cad/          STEP import → tessellation → cache; knows nothing about Panda3D
  viz/          Panda3D: scene, mapping joints onto NodePaths, HUD
  ui/           PySide6 shell (window, trees, property grid) — hosts the viz viewport
  cli.py        entry points (typer)
machines/       YAML machine definitions (versioned)
models/         input CAD files — LARGE BINARIES, do not edit, do not commit
assets/cache/   GENERATED tessellated meshes — do not edit by hand, deleting them is fine
recordings/     recorded data streams for replay (not committed)
tools/          helper scripts (STEP fixture generator)
tests/unit/     fast, no I/O, no window, no OPC UA
tests/integration/  markers `integration` (mock PLC) and `cad` (OpenCASCADE)
tests/data/     small fixture files — versioned, unlike models/
docs/           architecture and decisions
```

## Language

**Write everything in English.** Chat replies, commit messages, code, comments,
docstrings, log messages, user-facing UI strings and documentation.

The reason is token cost: Slovak tokenizes badly (measured on this project's own
prose: 1.77× the tokens of the same text in English, and the same holds for
identifiers), and everything written here is re-read on every turn. English is
also the norm for code and keeps the project readable for anyone who joins later.

One thing stays as it is: UI strings must still go through `tr()` — see
`src/pssim/ui/translations/README.md`. English is the source language, other
languages are translations.

## Conventions that hold without exception

- **Dependencies point inwards only.** `domain/` imports nothing from `viz/`, `io/`, `cad/`,
  `ui/` or any external framework. If data has to reach the domain, pass it as an argument.
- **Units: the scene is in metres and radians.** CAD is typically in mm, a PLC often sends
  mm and degrees. Conversion happens **at the boundary** (`config/loader.py`, `cad/`, `io/`),
  never in `domain/` and never in `viz/`. See `src/pssim/domain/units.py`.
- **Time: always the `SourceTimestamp` from OPC UA**, not the local arrival time. Internally
  seconds as `float` on a monotonic scale. Conversion in `io/`.
- **Never touch OPC UA in the render loop.** Data arrives through subscriptions into
  `io/store.StateStore`; the render thread only reads an interpolated snapshot from it.
- **Errors:** raise the typed errors from `src/pssim/domain/errors.py`, not a bare `Exception`.
- **Logging:** `structlog` through `pssim.observability.get_logger()`, never `print()`.
- **Panda3D objects never leave `viz/`.** No `NodePath`, `LVector3` or `LQuaternion` in
  signatures outside `viz/`. The domain returns a `JointPose` (axis + angle / offset) and
  viz translates it.
- **New dependencies only after approval.** Write down why the stdlib or a library already
  present is not enough.

## What NOT to do

- Do not edit: `assets/cache/**`, `models/**`, `uv.lock`, `.env*`, `recordings/**`
- Do not run: anything that **writes** to the OPC UA server of a real machine. Test writes
  exclusively against `pssim mock-server`.
- Do not change the YAML machine definition format in `config/schema.py` incompatibly —
  existing `machines/*.yaml` must stay loadable. Add a migration path when changing it.
- Do not add numpy or pydantic to `domain/` "for convenience" — it is testable precisely
  because there is nothing like that in there.
- Do not add comments that merely repeat the code.

## When you are not sure

- If a task allows two reasonable readings, **ask** before implementing.
- If more than about 5 files need changing, write a plan first and have it approved.
- Patterns worth imitating:
  - `src/pssim/domain/kinematics.py` — this is what a pure, fully tested domain layer looks like
  - `src/pssim/io/base.py` — this is how we define a boundary to the outside world
    (a Protocol, not an ABC)
- **Do not write code against a library API you have not verified.** `asyncua` and `OCP` have
  large and unintuitive APIs. If you are unsure about a call, check it in a REPL or in the
  documentation and write that into the commit message.

## Further context

@docs/architecture.md

# PSsimTool

Real-time 3D machine simulation. Geometry from CAD (STEP), motion from a PLC over **OPC UA**.

This is not a physics simulator — positions and rotations arrive from the control system and
the application displays them. Its purpose is to make visible what the PLC program is
actually doing.

## Quick start

```bash
uv sync --all-extras
```

The project requires **Python 3.12** — neither `panda3d` nor `cadquery-ocp` have wheels for
3.13+. `uv` downloads the right version itself; the system Python does not need touching.

### The desktop application

```bash
uv run pssim ui
```

Opens a window with the main menu. No PLC, no machine definition needed.

**The UI is in English.** The source strings are written in English in the code and wrapped
in `tr()`, so they can be translated without touching any logic. Another language is chosen
with a switch:

```bash
uv run pssim ui --lang sk
```

The list of languages and how to add another one is in
[src/pssim/ui/translations/README.md](src/pssim/ui/translations/README.md).
Only English is compiled so far — `--lang sk` fails until a translation exists. A language
choice in the menu will come later; it will hook into `ui/i18n.install_translator()`.

| Menu | Item | What it does |
|---|---|---|
| `File` | `Exit` (Ctrl+Q) | quits the application |
| `Open` | `Open 3D file…` (Ctrl+O) | opens a STEP file and displays it |

The import runs **in the background** — the window stays responsive while it does. A large
assembly takes minutes to tessellate; the result is cached in `assets/cache/`, so opening it
a second time is instant. Once loaded, the camera centres on the model automatically.

A file without a machine definition is assumed to be in **millimetres** (`ASSUMED_UNITS` in
`ui/loader.py`) — most mechanical CAD is.

### Toolbar and standard views

The **View** button opens a menu with seven orientations; the button icon always shows which
one you are currently in.

| View | Shortcut | | View | Shortcut |
|---|---|---|---|---|
| Isometric | Ctrl+1 | | Right | Ctrl+5 |
| Front | Ctrl+2 | | Top | Ctrl+6 |
| Back | Ctrl+3 | | Bottom | Ctrl+7 |
| Left | Ctrl+4 | | **Fit to view** | Ctrl+0 |

Switching the view **keeps the zoom** and the point of interest — only the angle changes.
`Fit to view` centres the camera back on the whole model.

The icons are drawn at run time (`ui/icons.py`); there are no binary assets in the
repository. Each icon projects the axes with the very camera that will be used after the
click, so it cannot show something different from what actually happens.

### Several models at once

Every `Open` **adds** a model, it does not replace the previous one. The list is in the
**Models** panel on the left; it shows the name, the part count, and marks a model that has
been moved or rotated with an asterisk (the full placement is in the tooltip).

The same file can be opened repeatedly — a machine legitimately contains ten identical
parts. The copies become `bolt`, `bolt (2)`, `bolt (3)`…

**Every action applies to the selected model.** The selected model is outlined in the scene
with an orange box, so it is clear which one a change will affect.

| With no selection | With a model selected |
|---|---|
| `Placement…` and `Remove` are **disabled** | both work on the selected model |
| `Ctrl+0` frames **all** models | `Ctrl+0` frames the selected model |

Disabled, not a silent no-op: when there is nothing to move, the button should say so.

### Renaming and the tree context menu

Right-clicking the model tree acts on the row under the cursor, which becomes the
selection first — so the action can never land on a different model than the one
pointed at.

| On a model row | On empty space |
|---|---|
| `Add Model…`, `Rename…`, `Placement…`, `Remove` | `Add Model…` only |

The model actions are **left out** on empty space rather than greyed out: the
selection survives a click into the void, so a disabled `Rename` would be
claiming there is no model while the toolbar still has one.

`Rename…` (also **F2**, and `Model → Rename…`) opens a one-field dialog with the
current name in it. Two rules, both tested without a window because they live in
the registry:

- A blank name is refused — an unnamed row has nothing to click on.
- A name already in use gets the same counter suffix a repeated file gets
  (`gantry (2)`), and the status bar says so, because it is not what was typed.

The name is **display only**. Model ids do not change, so the renderer and an
open placement dialog keep pointing at the same model. A project stores the
selection by name (R13), so the next save records the new one.

### Placing a model

`Model → Placement…` (Ctrl+M) opens a dialog with translation in X/Y/Z and rotation about
each axis **for the selected model**. Values are entered in **millimetres and degrees**, as
is usual in CAD; `domain/placement.py` does the conversion to internal metres and radians.

Its purpose is to let a model sit where it belongs: a CAD file has its origin wherever the
designer left it, and that need not be the point you want to measure against. With several
models it is also the way to arrange them relative to each other.

- A change takes effect **immediately** — otherwise the values are entered blind.
- The dialog is bound to **one model** and names it in the title. Changing the selection
  closes it, so edits cannot reach a different model.
- `Cancel` restores the state as it was when the dialog opened.
- The dialog is **non-modal**, so the scene can be rotated while typing.
- Rotation happens about the **model origin**, not about its centre of mass.
- The cross at the origin does not move — it is the reference models are placed against.
- The camera stays put; if the model leaves the frame, `Ctrl+0` brings it back.

### Saving the scene as a project

A project remembers the whole scene: which models are loaded, where each one sits,
which one is selected and where the camera is looking.

| Action | Shortcut |
|---|---|
| `File → Save Project` | Ctrl+S |
| `File → Save Project As…` | |
| `File → Open Project…` | Ctrl+Shift+O |
| `File → Open Recent` | last 10 projects |
| `File → Close All` | empties the scene |

Files are `*.pssim` — plain JSON, **in millimetres and degrees**, so it can be read
and checked without converting anything:

```json
{
  "version": 1,
  "models": [
    {
      "name": "gantry",
      "file": "models/gantry.step",
      "placement": { "x_mm": 300.0, "y_mm": -50.0, "rotate_z_deg": 90.0 }
    }
  ],
  "selected": "gantry",
  "camera": { "distance_mm": 431.3, "azimuth_deg": 90.0, "elevation_deg": 0.0 }
}
```

- **No geometry is stored** — a project is a list of references. The same STEP file
  behind ten projects is still one file and one cache entry.
- Model paths are **relative when the model sits inside the project's folder**, so a
  project plus its `models/` subfolder can be moved or handed to a colleague as one
  unit. A model kept elsewhere is stored absolute, because it does not travel with
  the project.
- Models load **one at a time** — the importer writes into a shared cache, so two at
  once would race. Selection and camera are restored once the last one is in.
- Files the project refers to but cannot find are reported **once**, and everything
  still on disk loads anyway.
- A project written by a newer build is **refused**, not half-read.

Details and reasoning: `docs/architecture.md` R13.

### The cartesian cross

At the origin of the model's coordinate system there is a cross with the axes **X red,
Y green, Z blue** and labels — the same convention as in CAD tools. It helps you stay
oriented while rotating, especially with symmetrical parts.

Its size follows the size of the model (a quarter of its radius), so it is legible on a
single part and on a whole production line alike.

### Mouse control of the scene

| Input | Action |
|---|---|
| middle button + drag | orbit around the model |
| **Shift** + middle + drag | pan |
| right button + drag | pan |
| left button + drag | orbit |
| wheel | zoom in / out |

The convention is taken from CAD tools (SolidWorks, Fusion, Inventor). The bindings live in
one place — `viz.orbit.drag_action()`.

The camera never rolls sideways and the elevation is clamped short of the poles, so the image
never flips. Zoom is multiplicative rather than additive: a wheel step is small when you are
close in and large when zoomed out.

### Demo without a PLC and without your own CAD

`machines/demo.yaml` runs on test geometry and a simulated PLC, so it works right after
cloning. First import the geometry into the cache:

```bash
uv run pssim import-step tests/data/fixture.step --machine machines/demo.yaml
```

Then, in one terminal, the simulated PLC:

```bash
uv run pssim mock-server
```

and the application in another:

```bash
uv run pssim run machines/demo.yaml
```

A window opens with a gantry that moves according to the values from the mock server.

### With your own machine

Copy `machines/example.yaml`, adjust the node paths and OPC UA nodes, and import the
geometry (takes minutes, the result is cached):

```bash
uv run pssim import-step models/stroj.step --machine machines/stroj.yaml
```

## How it works

```
.step ──► cad/ (OpenCASCADE) ──► assets/cache/*.npz ──┐
                                                       ├──► viz/ (Panda3D)
PLC ──► io/ (asyncua) ──► StateStore ──► domain/ ──────┘
```

`machines/*.yaml` is the bridge between them: it says which node of the CAD assembly is which
joint, and which OPC UA node drives it.

```yaml
machine: example
step_file: models/example.step
units: mm
joints:
  - name: axis_x
    parent: base
    child: portal
    type: prismatic
    axis: [1, 0, 0]
    limits: [0.0, 2.5]
    signal:
      node: "ns=2;s=Axes.X.ActPos"
      scale: 0.001        # the PLC sends mm, the scene is in metres
```

Details and the reasoning behind the decisions:
**[docs/architecture.md](docs/architecture.md)**.
Domain glossary and signal mapping: `.claude/skills/domenovy-kontext/`.

## Development

| Purpose | Command |
|---|---|
| Fast tests | `uv run pytest tests/unit -q` |
| All tests | `uv run pytest` |
| Lint + format | `uv run ruff format . && uv run ruff check --fix .` |
| Type check | `uv run pyright` |
| Record data from a PLC | `uv run pssim record machines/example.yaml -o recordings/run.jsonl` |
| Replay a recording | `uv run pssim replay recordings/run.jsonl machines/example.yaml` |

`ruff check` and `pytest tests/unit` must pass before a commit.

### How the tests are split

- `tests/unit/` — no I/O, no window, no OPC UA. Runs in a few seconds.
- `tests/integration/` with the `integration` marker — against the mock OPC UA server
  (`uv run pytest -m integration`).
- `tests/integration/` with the `cad` marker — STEP import, requires `uv sync --extra cad`
  (`uv run pytest -m cad`).
- `tests/integration/` with the `ui` marker — window and menus, requires `uv sync --extra ui`
  (`uv run pytest -m ui`). They run headless through `QT_QPA_PLATFORM=offscreen`; no window
  opens.

The test STEP file `tests/data/fixture.step` is versioned (50 kB). It is generated by
`uv run python tools/make_step_fixture.py` — it deliberately contains duplicate part names,
a rotated part and a part without a colour.

## Implementation status

| Part | State |
|---|---|
| `domain/` — machine model, kinematics, interpolation, units, time | done, 163 unit tests |
| `config/` — YAML schema and loader | done, covered by tests |
| `io/store`, `io/replay`, `io/recorder`, `io/timebase` | done, covered by tests |
| `io/opcua_source`, `io/mock_server` | **verified by integration tests** against the mock server; not against a real PLC yet |
| `cad/cache`, `cad/mesh`, stable node paths, unit scaling | done, covered by tests |
| `cad/step_import` — reading STEP through OpenCASCADE | **verified** against `tests/data/fixture.step` (35 tests): assembly tree, names, colours, units, rotations, geometry, cache |
| `viz/scene_builder`, `viz/transforms` | done, covered by tests (no Panda3D) |
| `viz/mesh_loader`, `viz/app` — scene and render loop | **verified** by headless tests of the whole chain (29 tests) and by real runs |
| `viz/orbit`, `viz/orbit_control` — camera control and views | done (unit + integration tests) |
| `viz/axes` — the cartesian cross | done, covered by tests |
| `viz/embed` + `ui/viewport` — Panda3D in a QWidget | done, verified by a real run |
| `domain/placement` — moving and rotating a model, unit conversion | done, covered by tests |
| `ui/i18n` — translation mechanism | done; no Slovak translation exists yet |
| `ui/model_registry` — model collection and selection | done (54 unit tests) |
| `ui/model_tree` — the Models panel, selection, context menu | done |
| `config/project` — the project format (`*.pssim`) | done, covered by tests |
| `ui/project_controller` — the order models of a project load in | done, covered by tests |
| `ui/recent_files` — the recent-projects list | done, covered by tests |
| `ui/` — window, menu, toolbar, placement, several models, projects, renaming | done (229 tests) |
| `ui/` — a tree of **parts** inside a model, property grid, HUD | not implemented |

The whole chain **STEP → cache → scene → value from the PLC → part position** is covered by
tests in `tests/integration/test_viz_scene.py`, which run without opening a window.

## Repository layout

```
src/pssim/          source code (see docs/architecture.md for the layers)
machines/           YAML machine definitions — versioned
models/             input CAD files — not committed (large binaries)
assets/cache/       generated meshes — not committed, disposable
recordings/         recorded data streams — not committed
docs/               architecture and decisions
tests/              unit + integration
.claude/            Claude Code configuration (versioned, shared with the team)
```

## Claude Code configuration

`.claude/` and `CLAUDE.md` are part of the repository — after cloning, Claude Code behaves
the same for everyone. An explanation of how it is put together and why is in
[docs/claude-code-prirucka.md](docs/claude-code-prirucka.md).

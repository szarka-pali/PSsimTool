# PSsimTool architecture

This document describes **why** the system is split the way it is. The concrete conventions
live in `CLAUDE.md` and `.claude/rules/`.

## Data flow

```
   PLC (OPC UA server)                          .step file
          │                                          │
          │ subscription (MonitoredItem)             │ once, offline
          ▼                                          ▼
  ┌───────────────────┐                     ┌─────────────────────┐
  │ io/opcua_source   │                     │ cad/step_import     │
  │ thread B, asyncio │                     │ OCP: STEPCAF reader │
  └─────────┬─────────┘                     │  → tessellation     │
            │ put(signal, value, t)         │  → assembly tree    │
            ▼                               └──────────┬──────────┘
  ┌───────────────────┐                                │ .npz (vertices)
  │ io/store          │  latest value + ring buffer    ▼
  │ StateStore (lock) │                     ┌─────────────────────┐
  └─────────┬─────────┘                     │ assets/cache/       │
            │ sample_all(t)                 └──────────┬──────────┘
            ▼  thread A, 60 fps                        │
  ┌───────────────────────────────────────────────────┐│
  │ viz/app: Panda3D task                             ││
  │  1. read a snapshot from StateStore                │◄┘
  │  2. domain/kinematics: value → JointPose          ││
  │  3. NodePath.setPosQuat()                         ││
  └───────────────────────────────────────────────────┘
```

`machines/*.yaml` ties these two worlds together: it says which **assembly node** is which
**joint**, and which **OPC UA node** drives it.

## The layers and why they are separated

| Layer | Responsibility | May import |
|---|---|---|
| `domain/` | machine model, kinematics, interpolation, units, errors | stdlib only |
| `config/` | YAML schema, validation, translation into `domain` | `domain`, pydantic, yaml |
| `io/` | data sources and their lifecycle, thread-safe store | `domain`, `config`, asyncua |
| `cad/` | STEP → mesh, cache | `domain`, OCP, trimesh, numpy |
| `viz/` | Panda3D scene and render task | everything except `ui` |
| `ui/` | PySide6 shell | everything |

The reason for keeping `domain/` strictly dependency-free is practical, not ideological:
**kinematics and interpolation have to be testable without opening a window and without a
PLC.** That is 90 % of the logic that can go wrong, and at the same time 100 % of what can
be tested in milliseconds.

## Key decisions

### R1 — STEP is read through OpenCASCADE (`cadquery-ocp`), not through a converter

Panda3D cannot read STEP. The alternatives were FreeCAD headless (heavy, GUI dependencies),
`gmsh` (aimed at FEM meshes, does not preserve the assembly) and `assimp` (no STEP support).

We use **`STEPCAFControlReader` + `XCAFDoc`**, not `STEPControl_Reader`. The difference
matters: the CAF version yields an **assembly tree with names, transformations and colours**.
Without it there is nothing to map joints onto and parts would have to be identified by hand
from their geometry.

### R2 — Tessellation happens offline and the result is cached

Tessellating an assembly of thousands of parts takes tens of seconds to minutes. The cache
key is a hash of (STEP file content + tessellation parameters + `IMPORTER_VERSION`). The
cache lives in `assets/cache/` and is **fully disposable** — deleting it only means one slow
start, never data loss.

### R2b — The cached geometry format is `.npz`, not glTF

`cad/` must know nothing about Panda3D, so it cannot write `.bam` into the cache. The
original intention was glTF via `trimesh`, but that would have added two moving parts
(`trimesh` when writing, the `panda3d-gltf` loader plugin when reading) to a path that has
to be dependable.

The format is `.npz` instead: vertices, normals and indices as numpy arrays. `numpy` is in
the project already, `viz/` builds a `Geom` straight from it through `copyDataFrom` (one
block copy instead of `GeomVertexWriter` row by row), and the whole format can be tested in
`tests/unit/` without OpenCASCADE and without Panda3D.

The price: the mesh cannot be opened in Blender. For looking at it, the original STEP is
there.

**Geometry is keyed by part definition**, not by node path. The same part used ten times has
one file in the cache and ten nodes pointing at it. Without that, an assembly with a thousand
screws would have a thousand copies of the same screw in the cache.

### R2c — A joint moves relative to the placement from CAD

A node has two placements: the one from the CAD assembly and the one dictated by the joint.
They compose — CAD decides where the part sits at zero, the joint adds movement on top.

The alternative (the joint overwrites the placement) would mean the part jumps to its
parent's origin on the first value from the PLC, and the machine definition would have to
duplicate in `origin:` what is already in the STEP file.

### R3 — Units: metres and radians, converted at the boundary

CAD gives millimetres, a PLC gives whatever (mm, degrees, encoder increments). If the
conversion happens in several places, sooner or later something gets multiplied twice and a
part flies off into space. Hence: every input is converted **once**, in `config/loader.py`
(`scale`, `offset` in the YAML) or in `cad/` (STEP units). Inside the system there is one
unit.

### R4 — OPC UA runs in its own thread with its own asyncio loop

The Panda3D task manager supports `async def` tasks, but it awaits **Panda3D futures**, not
asyncio ones. An asyncua client cannot run in it.

Hence: thread B runs `asyncio.run()` with the asyncua client and writes notifications into
`StateStore` under a lock. Thread A (Panda3D) only **reads** from it. What we share is the
`latest value + a short ring buffer`, not a queue — a queue would grow whenever rendering
fell behind and old data would be displayed.

### R5 — Interpolation is mandatory, not optional

An OPC UA subscription realistically delivers data every 20–100 ms; we render at 60 fps.
Without interpolation the motion is jerky. `domain/interpolation.py` keeps a short history of
`(source_time, value)` per signal and samples it at `now - render_delay`, where `render_delay`
is a deliberate small delay (default 2× the publishing interval) so that it interpolates
between two known points rather than extrapolating.

**Limit of usability:** if a PLC axis changes position faster than OPC UA can publish it,
interpolation will not save you — it will smooth out motion that is not actually happening.
That is when a different transport is needed (see R6).

### R6 — The data source sits behind an interface; OPC UA is only the first implementation

`io/base.py` defines `DataSource` (a Protocol). Implementations: `OpcUaSource`,
`ReplaySource`, `MockSource`. If OPC UA turns out to be too slow for fast axes, an
`AdsSource` (Beckhoff, `pyads`) or `S7Source` (`python-snap7`) can be added without touching
`viz/` or `domain/`.

### R7 — Recording and replay from the start

`pssim record` writes the data stream into JSONL, `pssim replay` plays it back through the
same `DataSource` interface. Without it there is no developing without hardware and no
reproducing a fault that happened once, at a customer's machine.

### R8 — No physics until there is a reason

Data from a PLC is kinematics — positions are given, not computed. A physics engine would
solve nothing here and would introduce non-determinism. If collision detection is needed
later, `panda3d.bullet` is part of Panda3D and convex hulls can be pulled out of OCC.

### R9 — Shell in PySide6, viewport in Panda3D

DirectGUI cannot handle trees, docking and property grids at the level a CAD-like tool
needs. Panda3D can render into a parent window handle, so it can be embedded in a `QWidget`.
`viz/` is therefore designed to work standalone as well (`pssim run --no-ui`) — for debugging
and for tests.

The boundary is held by `viz/embed.EmbeddedRenderer`: Panda3D on the inside, only numbers and
a `CadAssembly` on the outside. `ui/viewport.py` merely holds it and forwards Qt events to
it, so `ui/` does not import Panda3D at all.

Three things that surprise you when embedding, and that cost time to find:

1. **The render loop does not belong to Panda3D.** `base.run()` takes over control and Qt
   freezes. A `QTimer` ticks and calls `taskMgr.step()`.
2. **Window size is in physical pixels.** Qt counts in logical ones; at 125 % Windows
   scaling the difference is 1.25× and shows up as a black band on the right and bottom.
   The conversion is in `ui/viewport._device_size()`.
3. **The mouse goes to the Panda3D window, not to the Qt widget.** Camera control therefore
   cannot live in `mousePressEvent()` — it is in `viz/orbit_control.py`, on top of Panda3D
   events.

### R9b — The camera orbits, it is not free

`viz/orbit.OrbitCamera` keeps its state spherically: point of interest, distance, azimuth,
elevation. The alternative (a free camera with a quaternion) behaves worse when inspecting a
model — it loses "up" and the user easily gets lost in it.

Elevation is clamped short of the poles (`lookAt` loses its reference there and the image
flips) and the camera never rolls sideways. Zoom is multiplicative so that a wheel step
matches the current zoom level.

The whole of the maths is a **pure function** in `viz/orbit.py` with no Panda3D. The reason
is the same as for `domain/`: "the model spins strangely" is otherwise a bug you can only
debug by eye. The Panda3D part (`viz/orbit_control.py`) only supplies numbers from the mouse.

The built-in trackball is **not used** (`base.disableMouse()`) — its controls are
unintuitive and it cannot be told what to orbit around.

### R9c — Standard views have a single source of truth

`viz/orbit.STANDARD_VIEWS` maps a view name to `(azimuth, elevation)`. Everything else is
derived from it: `viz/camera.view_direction()` computes the direction vector for
`pssim screenshot`, `ui/main_window` turns it into menu entries, and `ui/icons` draws the
icons by projecting the axes with that same camera.

Previously the definition of "what the front view is" existed in two places (angles for the
interactive camera, vectors for the screenshot). Pairs like that drift apart over time and
nobody notices the difference until they start wondering why `--view front` in a screenshot
looks different from `Ctrl+2` in the application.

`top` and `bottom` use the **clamped** elevation, not exactly `±pi/2`: at the pole `lookAt`
loses its "up" reference and the image flips.

### R10 — Placing a model is a transform of the root, not a change to the geometry

Moving and rotating a model (`Model → Placement…`) is applied to the **root `NodePath`**, not
to the vertices in the cache. The cache therefore stays tied exclusively to the STEP file
content and the tessellation parameters — two different placements of the same model do not
produce two copies of the geometry.

The consequences that follow, and that are deliberate:

- Rotation is about the **model origin**, not about the centre of mass. That is what someone
  expects when they type "rotate 90° about Z".
- The cross at the origin does not move — it is the reference the model is placed against.
- A placement survives loading another file; it is applied before the camera frames the
  scene, so the camera aims where the model actually ends up.

**Units are converted in `domain/placement.py`, not in the dialog.** The UI is another system
boundary and the same rule applies to it as to `config/` and `io/` (see R3): the user types
millimetres and degrees, the scene runs in metres and radians, the conversion happens once
and has tests. Six fields times two directions is plenty of opportunity for a typo.

### R11 — Translations through Qt, English as the source language

User-facing text is written **in English** in the code and wrapped in `tr()`. The
translations are `.ts`/`.qm` files; the mechanism is `ui/i18n.py`.

Why Qt rather than a dictionary of our own: Qt already solves fallback to the source text,
plurals, extraction tooling and — above all — **translation of Qt's own dialogs**:
`QFileDialog`, the `OK`/`Cancel` buttons. A homegrown implementation would leave those
standard elements in English and the UI would be half translated.

Two rules follow from that:

- **Formatting messages does not belong in `domain/`.** Previously `domain/placement.py`
  returned a sentence for the status bar; the domain has no way of knowing what language the
  application runs in, and must not import Qt. Moved to `ui/labels.py`.
- **Qt's standard buttons are not overwritten.** Hardcoded text would stay in English when
  the language is switched, while the rest of the dialog would be translated.

Logs are **not translated** — they are for the developer, not the user.

### R12 — Multiple models: the state lives in a registry, not in the scene or in the widget

The models are held by `ui/model_registry.ModelRegistry` — a pure, boringly untestable
collection with no Qt and no Panda3D. `ui/model_tree` **renders** it,
`viz/embed.EmbeddedRenderer` **draws** it, but neither of them owns it.

Why not keep the state in the tree widget, where most people would put it: the interesting
logic is unique names when the same file is opened repeatedly, and what happens to the
selection after a model is deleted. Both can be got wrong and both can be tested in the
registry without a window.

Keying is by **generated `model_id`**, not by file path: the same file may be opened several
times. The displayed name is only for looking at and may repeat, so nothing is ever keyed by
it.

One rule follows that is easy to break: **the selection must be synchronised both ways.**
A click in the tree goes into the registry, but the selection also changes from code (the
neighbouring model after a deletion), and then it has to be reflected back into the tree —
otherwise a highlighted row stays behind that the application no longer works with. Only a
real run caught this; the unit tests did not.

Highlighting in the scene is a **wireframe box**, not a colour change: models have their own
colours from the STEP file, so tinting would be invisible on some and misleading on others.

### R13 — A project is JSON in mm and degrees, and models load one at a time

The project file (`*.pssim`) records what the scene consists of: the models, their
placements, which one is selected, and where the camera is. It does **not** contain
geometry — a project is a list of references, so the same STEP file behind ten projects is
still one file and one cache entry.

Three decisions inside that are worth the words:

**JSON, not YAML.** The file is written by the application, never by hand, so YAML's
flexibility buys nothing and costs a diff: two saves of the same scene must differ only where
the scene differs. `config/schema.py` keeps YAML because machine definitions *are* written by
hand; this one is not.

**Millimetres and degrees, including the camera.** The same boundary rule as R3, and the file
is the boundary. The numbers in the file are the numbers the user typed into the Placement
dialog, which means a project can be read and checked without converting anything mentally.
Conversion happens once, in `config/project.py` and `ui/project_controller.py`, and has tests
in both directions.

**Model paths are relative only when the model lives inside the project's folder.** Then the
project and its `models/` subfolder move or get shared as a unit. A model kept anywhere else
is stored absolute: it does not travel with the project, and a chain of `..` segments back out
to it breaks the moment the project file alone is moved — where an absolute path still opens.

Not stored: the camera's zoom limits (they follow from the size of the scene, and the scene is
whatever was just loaded), window geometry, and the cache directory.

`selected` names the model, it does not identify it. Model ids are generated per session (R12)
and would mean nothing after a reload; the name is what the user sees in the tree, so it is
what survives.

Loading is a **queue, not a loop**: `ui/project_controller.ProjectLoader` starts one import,
waits for `on_import_finished`, then starts the next. The importer writes into a shared cache,
so two at once would race. Missing files are collected by `plan_load` and reported **once** —
a project referencing five moved files must not mean five modal dialogs — and everything still
on disk loads anyway. Selection and camera are applied at the end, once every model is in.

Format version is refused **forward, not backward**: a file written by a newer build is an
error rather than something half-read, because silently ignoring a section the user configured
is worse than saying no.

## Performance

A STEP assembly typically has hundreds to thousands of parts, which is an unaffordable number
of draw calls if approached naively. So when building the scene:

- parts that are **neither** a joint nor a descendant of one → `flattenStrong()` into a single Geom
- repeated parts (screws, conveyor rollers) → `instanceTo()`
- distant groups → `LODNode`
- moving parts stay separate `NodePath`s — those cannot be flattened

`viz/scene_builder.py` does the split into static and moving according to the joint definitions.

## What is deliberately unsolved

| Thing | State |
|---|---|
| Writing to the PLC | out of scope, reading only |
| IK / trajectory planning | out of scope, the PLC supplies finished positions |
| Collisions | deferred, see R8 |
| CAD formats other than STEP | STEP is the minimum; IGES/JT/glTF can be added in `cad/` |
| Several machines in one scene | the data model allows it, the scene builder does not yet |
| OPC UA security (certificates) | the interface is ready, the configuration is not implemented |

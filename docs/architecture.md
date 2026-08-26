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

| Layer     | Responsibility                                          | May import                    |
| --------- | ------------------------------------------------------- | ----------------------------- |
| `domain/` | machine model, kinematics, interpolation, units, errors | stdlib only                   |
| `config/` | YAML schema, validation, translation into `domain`      | `domain`, pydantic, yaml      |
| `io/`     | data sources and their lifecycle, thread-safe store     | `domain`, `config`, asyncua   |
| `cad/`    | STEP → mesh, cache                                      | `domain`, OCP, trimesh, numpy |
| `viz/`    | Panda3D scene and render task                           | everything except `ui`        |
| `ui/`     | PySide6 shell                                           | everything                    |

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

### R3 — Shell in PySide6, viewport in Panda3D

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

### R3b — The camera orbits, it is not free

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

### R3c — Standard views have a single source of truth

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

### R4 — Placing a model is a transform of the root, not a change to the geometry

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

### R5 — Translations through Qt, English as the source language

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

### R6 — Multiple models: the state lives in a registry, not in the scene or in the widget

The models are held by `ui/model_registry.ModelRegistry` — a pure, boringly untestable  
collection with no Qt and no Panda3D. `ui/model_tree` **renders** it,  
`viz/embed.EmbeddedRenderer` **draws** it, but neither of them owns it.

Why not keep the state in the tree widget, where most people would put it: the interesting  
logic is unique names when the same file is opened repeatedly, and what happens to the  
selection after a model is deleted. Both can be got wrong and both can be tested in the  
registry without a window.

Keying is by **generated `model_id**`, not by file path: the same file may be opened several  
times. The displayed name is only for looking at and may repeat, so nothing is ever keyed by  
it.

One rule follows that is easy to break: **the selection must be synchronised both ways.**  
A click in the tree goes into the registry, but the selection also changes from code (the  
neighbouring model after a deletion), and then it has to be reflected back into the tree —  
otherwise a highlighted row stays behind that the application no longer works with. Only a  
real run caught this; the unit tests did not.

Highlighting in the scene is a **wireframe box**, not a colour change: models have their own  
colours from the STEP file, so tinting would be invisible on some and misleading on others.

### R7 — A project is JSON in mm and degrees, and models load one at a time

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

### R8 — Units: metres and radians, converted at the boundary

CAD gives millimetres, a PLC gives whatever it likes (mm, degrees, encoder increments), and
the user types millimetres and degrees. Inside the system there is exactly one unit:
**metres and radians**.

Conversion happens **once**, at each boundary — `config/loader.py` (`scale`, `offset` from the
YAML), `config/project.py` (the project file), `cad/` (STEP units), `io/` (signal scaling) and
`domain/placement.py` for what the dialogs collect. Never in `domain/` beyond those
converters, and never in `viz/`.

The rule is this blunt because the failure mode is silent: if a conversion lives in two
places, sooner or later a number is scaled twice and a part flies off into space, with nothing
in the log to say so. `domain/units.py` holds the constants so there is one spelling of
`MM_TO_M`.

Every conversion has a test with a concrete number, in both directions. Six placement fields
times two directions is plenty of opportunity for a typo.

### R9 — Joints carry models, and movement composes with the CAD placement

A joint (an axis or a trajectory) belongs to the **scene**, not to a model. It can be carried
by another joint, and models are bound *to* it — which is what lets a rail carry a carriage
that carries a rotation axis. The hierarchy is made of joints; models are its leaves.

**An axis and a trajectory are described differently, because they are different
shapes.** A trajectory is two points: a start and a far end, both of which mean
something. An axis is a **centre point**, a **direction** and an **init rotation** —
the angle that counts as zero. Only the direction of that vector is read, so
`(0,0,1)` and `(0,0,100)` are the same axis.

An earlier version defined an axis by two points as well, deriving the direction as
`normalize(target - origin)`. That reads plausibly and is wrong in practice: an axis
has no length, so the second point invited coordinates that looked meaningful and
were not, and the marker drawn to that point made two identical axes look wildly
different. The direction is now typed directly and the marker's length comes from the
scene.

The init rotation is part of the **motion**, not of the mounting frame: it shifts
where zero is, which is a different question from where a bound model sits. Limits
clamp the value and never the offset, or a joint limited to a few degrees could not
sit at its own zero. An axis therefore has no initial coordinate system at all —
`alignment` is read for a trajectory only, where a path genuinely needs a mounting
frame along it.

The consequences that shape the code:

- **A node has two placements and they compose.** CAD decides where a part sits at zero, the
  joint adds movement on top (`viz/scene.py` keeps the CAD placement as `base_transforms`).
  The alternative — the joint overwriting the placement — would make a part jump to its
  parent's origin on the first value from the PLC, and the machine definition would have to
  duplicate in `origin:` what the STEP file already says.
- **A bound model is seated by an anchor**, a point *and* a direction in its own local frame,
  so it can be attached by a real mounting face rather than by wherever the designer left the
  CAD origin.
- **A trajectory has an initial coordinate system**, `ModelJoint.alignment`, expressed
  relative to the joint's own tangential frame — so its `Z` runs *along* the path, not
  upwards. Identity means "at the start, tangential to it", which is the sensible default.
  The panel labels say which axis is which, because the frame's own axes are not the world's.
- **Joint names are unique across the whole registry**, not just among siblings. That is what
  makes a name usable as a cross-reference in the project file (R7).

The pose maths is a pure function in `domain/model_joints.py`; `viz/` only turns the result
into `NodePath` transforms.

### R10 — Data from the PLC runs in its own thread with its own asyncio loop

The Panda3D task manager supports `async def`, but it awaits **Panda3D** futures, not asyncio
ones, so an asyncua client cannot live inside it.

Hence: thread B runs `asyncio.run()` with the asyncua client and writes notifications into
`io/store.StateStore` under a lock. Thread A (Panda3D) only **reads** an interpolated snapshot.
What is shared is the *latest value plus a short ring buffer*, not a queue — a queue would grow
whenever rendering fell behind and the picture would show old data.

`StateStore` is the **only** shared mutable state between threads. If something else needs
sharing, extend the store rather than adding a second one.

### R11 — Interpolation is mandatory, not optional

An OPC UA subscription realistically delivers every 20–100 ms; we draw at 60 fps. Without
interpolation the motion is visibly stepped.

`domain/interpolation.py` keeps a short history of `(source_time, value)` per signal and
samples it at `now - render_delay`, where `render_delay` is a deliberate small lag (default
2× the revised publishing interval) so that it interpolates between two known points instead
of extrapolating past the last one.

**Where this stops helping:** if an axis moves faster than OPC UA can publish it, interpolation
will smooth over motion that never happened. That is the point at which a different transport
is needed (R12), not a better filter.

Time is always the **`SourceTimestamp`** from the PLC, never local arrival time, and the
offset to the local monotonic clock is estimated once and held constant — a drifting offset
takes interpolation apart.

### R12 — The data source sits behind an interface; OPC UA is only the first implementation

`io/base.py` defines `DataSource` as a `typing.Protocol`, not an abstract base class, so an
implementation does not have to import the module that consumes it.

Implementations today: `OpcUaSource`, `ReplaySource`, `MockSource`. If OPC UA turns out to be
too slow for fast axes, an `AdsSource` (Beckhoff, `pyads`) or `S7Source` (`python-snap7`) can
be added without touching `viz/` or `domain/`.

### R13 — Recording and replay from the start

`pssim record` writes the stream to JSONL; `pssim replay` plays it back through the same
`DataSource`. Without it there is no developing without hardware, and no reproducing a fault
that happened once, at a customer's machine.

Recordings are **not committed** — they are large and may contain customer data.

### R14 — No physics; collision detection is a warning, not a simulation

Data from a PLC is kinematics: positions are given, not computed. A physics engine would solve
nothing here and would make the picture non-deterministic.

What does exist is a **collision warning**. `domain/collision.py` compares axis-aligned
bounding boxes and `viz/` outlines whatever overlaps in red — **both** models, whether either
is selected or not, which is what makes it read as "these two". Nothing responds, nothing
stops moving, there are no contact forces — the PLC still decides where everything is and the
outline only says "look here".

Three properties of it are deliberate and worth knowing:

- **The comparison is per part, not per model.** One box around a whole assembly is useless:
  a carriage inside a frame overlaps the frame's box wherever it stands, so the warning would
  never clear. Each model's parts are measured once at load; the check is a sweep along X.
- **The error only goes one way.** A box always contains its part, so a real collision is
  never missed; a diagonal or concave part can raise a false alarm. For a warning that is the
  right direction to be wrong in.
- **It runs when asked, not by itself** (`viz/embed.check_collisions`, from
  `Scene → Check Collisions`). The answer is only interesting at a moment the user picked,
  and a check that runs by itself has to be cheap enough to run always, which caps how good
  it can be allowed to get. On demand it can afford the per-part comparison — measured at
  ~26 ms for two 1052-part assemblies.
- **The answer is dropped the moment anything moves.** The check records each model's world
  matrix and `step()` compares them, so a red outline never describes a scene that has since
  changed. A matrix walk per model per frame is cheap enough that correctness is free; a
  dirty flag set by hand would have to be remembered in every method that can move
  something, and the one that got forgotten would leave red on a model sitting somewhere it
  no longer collides.

**Nothing is excluded on principle.** Two parts bolted to the same axis are reported as
readily as two that genuinely crashed: from geometry alone the two are indistinguishable, and
suppressing one class of overlap would mean choosing which real collisions never to mention.

Sharpening this means real geometry — convex hulls from OpenCASCADE fed to `panda3d.bullet`,
which ships with Panda3D. That is a later step, not an oversight.

### R15 — What is drawn is per-item state, and it is saved

A model carries `is_visible`, `show_axes`, `color` and `highlight_color`; a joint carries
`show_axes`, `show_name` and `color`; the scene carries the name switch, the origin cross's
own switch, and the three sizes below. They live in the registries (R6), the renderer is told about them,
and the project file stores them (R7) — hiding a housing to see what is behind it, or
colouring an axis to tell it from its neighbour, is a deliberate act rather than an accident
of the session.

Four decisions inside:

- **A model has two colours, and they are different things.** `color` is the part itself;
  `highlight_color` is the outline drawn around it when selected. Both exist because a model
  whose body has been recoloured still needs an outline that can be told apart from it — and
  from the collision red.
- **One outline system, not two.** `viz/embed._refresh_outlines` is the only thing that draws
  a box round a model: red and thick when the model is colliding, its own highlight colour
  otherwise, and a coordinate cross alongside when it is the selected one. Collision wins
  over selection, because a warning must not be hidden by a colour the user chose.
- **Sizes are stated, not derived** — one cross size and one text size for the whole scene,
  plus one for the origin cross alone (`cross_size_mm`, `text_size_mm` and
  `origin_cross_size_mm` in the file, all three in `Scene → Sizes…`).

  Every size used to come from something different, and derived sizes cannot agree. The
  origin cross was sized from the scene radius, a selected model's from that model's own
  radius, a joint's from its span; the X/Y/Z glyphs were `arm_length × 0.25`. Measured, that
  put 100 mm letters on the origin cross next to 3.1 mm ones on a joint cross — a 32×
  spread — and left the three crosses ranging over 100× in size.

  The label size was worse than inconsistent, it was broken: `make_joint_label` treated its
  argument as a span to multiply by 0.09 and clamped it at 50 mm first, so **every requested
  size from 1 to 50 mm drew the same 4.5 mm of text** and the setting appeared to do nothing.
  A height parameter now means a height.

  What this costs: a scene at a very different scale wants the numbers changed once, where
  the old rule guessed. That is the trade uniformity buys, and guessing is what produced the
  problem.

  The joint *marker* still scales with the scene — it is geometry showing where an axis runs,
  not an annotation.
- **The origin cross is sized and hidden on its own** (`origin_cross_size_mm`,
  `show_origin_cross`, `Scene → Origin Cross`). It is the scene's reference rather than an
  annotation on any one item: it is the thing everything else is placed against, it is the
  only cross with nothing to belong to, and it is the one most often in the way once a model
  sits on top of it. Sharing the item size would mean choosing between a readable origin and
  readable joints, which is the disagreement the shared size was introduced to end.

  The model and joint crosses stay on the shared setting. Their X/Y/Z letters take the shared
  text size too, origin cross included — one text size in the scene, whatever the cross it
  labels.
- **A colour override is `None`, not a default colour.** A model's colours come from the STEP
  file, per part, and once overwritten there is nothing to reconstruct them from — so "no
  override" has to be a state of its own. `NodePath.setColor` on the root wins over the
  per-part colours and `clearColor` hands them back, which is exactly the pair of operations
  this needs, and it is what makes **Reset Colour** possible. Absent and opaque white must
  never collapse into each other in the file, or a reload would flatten every model nobody had
  recoloured.
- **Names are switched at two levels**, one scene-wide and one per joint, and a label is drawn
  only when both are on. One switch would not do: turning every name off to clear a crowded
  view must not forget which individual ones were already silenced.
- **A joint's marker and label are rebuilt to change colour, not restyled.** A `LineSegs`
  colour and a `TextNode` string are fixed when the geometry is created, so
  `viz/embed._rebuild_joint_marker` is the one place that builds both — `add_joint`,
  `update_joint`, a colour change and a name toggle all go through it.
- **Labels are billboarded** (`setBillboardPointEye`). Without it the text is edge-on and
  unreadable from half the orbit.

And two that hiding brought:

- **Hiding is purely visual.** A hidden model still blocks sensors and still collides. It is
  how you look *inside* an assembly, not how you take a part out of it. Its own outlines
  disappear with it, because they hang off the model root; whatever it collided with still
  shows one, which is the honest picture.
- **A hidden row stays selectable.** It is dimmed with the palette's disabled colour rather
  than disabled outright — otherwise there would be no way to turn it back on.

Selection is shown with a **wireframe box plus a coordinate cross**, never a tint on the
model: models carry their own colours from the STEP file, so tinting would be invisible on
some and misleading on others.

### R16 — Sensors are geometry against bounding boxes, and they are mounted

A sensor answers "is something there, and how far", from the same per-part world boxes the
collision check builds (R14). One notion of where the parts are, so a sensor can never
disagree with the warning outline about what is touching what — and sharpening the boxes
sharpens both at once.

Seven kinds in three families, and the split is deliberate:

| kind | shape | reads |
| --- | --- | --- |
| `BEAM` | point + direction + range | 0/1, blocked or not |
| `INDUCTIVE` | point + direction + range | 0/1, presence |
| `TOF` | point + direction + range | distance, plus a valid flag |
| `LASER_DISTANCE` | point + direction + range | distance, plus a valid flag |
| `ENCODER_INC` | mounted on a rotation axis | counts, accumulating past a turn |
| `ENCODER_ABS` | mounted on a rotation axis | counts within one turn |
| `PROXIMITY` | point + half extent | 0/1, a box zone |

`BEAM`/`INDUCTIVE` and `TOF`/`LASER_DISTANCE` are pairs whose maths is **identical**. They are
separate kinds anyway, because the kind is how the machine is documented: a photoelectric
sensor is not an inductive one on the drawing, even where the simulation cannot tell them
apart. Collapsing them would save a branch nobody has and lose the label somebody needs.

The decisions worth the words:

- **A reading is a value plus a validity flag**, one `SensorReading` for every kind. A
  distance sensor with nothing in range reports the range with `is_valid=False`, which is a
  different statement from reporting `0.0` — and `0.0` is what "something is touching the
  lens" means. Collapsing the two would make the most alarming reading indistinguishable from
  the most ordinary one.
- **An encoder reports counts, not radians**, with `counts_per_revolution` alongside. That is
  what a PLC receives from real hardware, and converting at the boundary (R8) is this
  project's habit everywhere else. `ENCODER_ABS` wraps within a turn, `ENCODER_INC` does not —
  that difference *is* the difference between the two devices, and 370° reading 10° on one and
  370° on the other is the test that pins it.
- **A sensor is mounted, and its coordinates are local to the mount.** `origin` and
  `direction` are in the frame of the model or joint carrying it, so a sensor bolted to a
  carriage rides the carriage for free rather than needing its numbers rewritten whenever the
  carriage moves. A geometric sensor normally mounts on a model; an encoder wants a joint —
  and specifically a joint's **move** node, or it would never see the axis turn.
- **A sensor does not see what it is mounted on.** A bracket-mounted sensor would otherwise
  read blocked by its own bracket forever. This is a real limitation and stated as one: a
  genuine self-occlusion is a mounting error, not something worth simulating.
- **The maths is pure** (`domain/sensors.py`, stdlib only) and the ray test exists once —
  `ray_distance_to` returns the entry distance and the 0/1 kinds are callers of it. Two copies
  of a slab intersection would drift, and the one that drifted would be the one nobody looks
  at.
- **Every sensor is re-read every frame**, in `viz/embed._evaluate_sensors`. Any model's
  placement can affect any sensor, so a "which sensors care about this model" index would have
  to be right everywhere a model can move; recomputing costs no more, because the real expense
  — rebuilding a marker — is already gated on the reading actually changing. The dock and the
  panel are pulled from that, in `MainWindow.refresh_sensor_readings`, wherever something can
  move: a joint value or a placement.

- **What a sensor is saying is worded per family** (`ui/labels.describe_state`), not with one
  pair of words for all seven kinds. A beam with nothing crossing it is *Clear*; a rangefinder
  in the same condition is *Out of range*, which is a statement about the sensor's reach rather
  than about an empty space in front of it; an encoder gets a dash and no colour at all,
  because `is_active` is `False` for every encoder forever and any word there would describe a
  failure to detect that was never attempted.

  One pair of words was what shipped first, and it read as "nothing is in the way" on a
  rangefinder and as "this encoder is not seeing anything" on a device that looks for nothing.
  Three phrasings are the cost of not saying either of those. The tooltip carries the sentence
  behind the word, so a surprising row can be read rather than guessed at.

- **A sensor's fields exist once** (`ui/sensor_fields.SensorFields`) and are used twice: by the
  modal `ui/sensor_dialog.py` that places one, and by the `Sensor` group in
  `ui/properties_panel.py` that edits one already in the scene. Seven kinds with per-kind field
  groups is too much to keep in step in two copies — and the two consumers differ only in
  *when* they read the fields, which is a signal, not a second widget. Hence both a `sensor`
  property that raises on a half-finished definition (the dialog's OK catches it) and a
  `sensor_if_valid()` that reports `None` for it (the panel is typed into through invalid
  states constantly, and must not throw out of a slot).

- **The properties dock shows one subject.** A model, a joint or a sensor — selecting in any of
  the three trees clears the other two, and `MainWindow._refresh_properties` renders whichever
  is left. The alternative, stacking a sensor's fields under a model's, is what made an earlier
  version look like the click had not registered.

  A sensor's live State and Reading sit in the same group but are refreshed on their own path
  (`set_sensor_reading_silently`): a reading arrives whenever anything moves, and re-filling the
  editable fields at that rate would make the panel unusable.

The `variable` each sensor carries is recorded and nothing publishes it yet: the point of it
is that a PLC binding has somewhere to go when writing arrives (R12), not that one exists.

### R17 — The menu bar is split by subject, and the icons are drawn

`File`, `Models`, `Geometry`, `Sensors`, `Scene`. Split by **what you are working on**, not by
verb: an `Edit` menu would hold fifteen unrelated entries with no way to tell which of them
applied to whatever is selected, because almost every action in this application is
per-item-type.

`Geometry` is the axes and trajectories. They are neither a model nor a sensor, which is
exactly why they had nowhere to go and were reachable only from the model tree's context menu.
`Bind To…` and `Variables…` stay under `Models` even though both name a joint: you attach *a
model* to an axis, and the values window belongs to the model whose chain is being driven.

Three rules the labels follow:

- **Everything that creates something starts `Add`**, and keeps its noun so it reads from the
  menu bar: `Add 3D Model…`, `Add Axis…`, `Add Trajectory…`, `Add Sensor…`. `Insert 3D Model…`
  was the one entry using a different verb from the rest of the application.
- **Below the separator the leaves drop the noun.** The menu title already says what the
  subject is, so `Sensors → Edit…` beats `Sensors → Edit Sensor…`.
- **A leaf says what it does from its path alone.** That is what the two `Scene` submenus are
  for: `Sizes…` on its own does not say what it sizes, `Scene → Crosses and Labels → Sizes…`
  does.

Only `Models → Remove` carries `Delete`. Three Remove entries bound to the same key would be
an ambiguous shortcut and Qt would fire none of them.

**Icons are drawn at run time** (`ui/icons.py`), never shipped as files. There is nothing to
license and nothing to keep in step with the code, the result adapts to the display's DPI, and
the pen colour comes from the running palette — a fixed grey cannot be legible on a light theme
and on a dark one both. Every sensor kind gets its own drawing, the identical-maths pairs
included, because the kind is how the machine is documented (R16) and a picture is the shortest
way to read which part it is. They are cached per kind and size: a tree row asks on every
refresh.

What a test can hold is that an icon is not blank and not a copy of its neighbour. How it
*looks* is for a real run.

### R18 — Settings that outlive a session live outside the project

A `*.pssim` says what the scene *is*. Two other kinds of state outlive a session and are not
that, and both go in the application's settings (`ui/settings.py`, `QSettings`):

- **How wide each table's columns are.** R7 already decided window geometry is not scene
  content, and a column width is the same kind of thing — it is about this user's screen.
- **The OPC UA endpoint and the variable-to-tag mapping.** A project then carries no
  addresses and can be handed to anyone. The price is real and was chosen deliberately: the
  mapping does not travel with the scene, so opening a colleague's project means assigning
  the tags again.

Three properties of the implementation:

- **The dataclasses are pure and Qt is imported inside `SettingsStore`.** `tests/unit/` must
  stay free of a window and run in seconds; importing PySide6 there costs more than the whole
  suite does.
- **A settings file is outside data.** Every read validates and falls back to a default —
  anything at all can be in there, including a hand edit. A zero column width is dropped (a
  collapsed column has no handle left to drag back), and only a stored `True` turns writing
  on: a corrupted setting must never be what enables it.
- **The store is injected**, as `RecentProjects` takes its `QSettings`. The default one is the
  user's own, and closing a window writes to it, so every test points at a temp file.

Columns are `Interactive`, not `Stretch` or `ResizeToContents`: both of those compute the
width themselves and take the drag handle away with it. The saved layout is applied only when
its length matches the table's column count — a layout from a build with different columns
would otherwise put every width against the wrong column.

### R19 — A project's variables are bound to OPC UA tags, and writing is a switch

Every joint and sensor carries a `variable` (R16), and until now the name went nowhere. The
Variables tab is the list of them, each with the tag it reads from, its value and its state.

The variables are **derived, not stored**: they are whatever the scene currently mentions.
Renaming an axis's variable leaves the old tag behind, which is the honest outcome — the tag
was assigned to a name and that name is gone. A joint's variable can never be empty (the
domain refuses it), a sensor's can.

**A joint reads and a sensor writes.** The PLC decides where the machine is; a sensor's
reading is something this application produces. `config/binding.SignalBinding` is the Protocol
that lets `OpcUaSource` take either without a branch — `JointBinding` keeps `joint_name` and
gains `signal` as a property, because renaming the field would change a versioned format for
nothing.

**Writing is off by default and the switch is checked in the source**, not only in the dialog.
With it off the write pump is never created, so a value reaching the outbox by mistake has
nothing that could carry it out. It is exercised **exclusively** against `pssim mock-server`,
which grew two writable nodes for the purpose — see `.claude/rules/io-opcua.md`.

The outbox itself lives in `StateStore` because R10 says that is the only mutable state shared
between threads and anything else that needs sharing extends it. A dict keyed by signal, not a
queue, for the reason the inbox is not one: a value offered on every frame is written once.

Three more decisions:

- **Browsing is a separate module** (`io/opcua_browser.py`). A browse is a one-off question
  with an answer — connect, walk, disconnect — where a source is a long-lived subscription
  that reconnects for ever, and folding request/response into that loop would complicate the
  one piece here that must never get stuck. Namespace 0 is skipped: it is the OPC UA standard
  address space, and including it buries the three nodes somebody wants under a hundred they
  do not.
- **`ui/connection_controller.py` is the thread boundary.** A `QTimer` on the UI thread takes
  a snapshot of the store; nothing there ever calls asyncua, exactly as the renderer works
  (R10). `poll` takes the time as an argument rather than reading the clock, so staleness is
  testable without sleeping. It holds a `DataSource`, not an `OpcUaSource` — R12 exists so a
  replay can take its place, and `use_source` is that seam.
- **A disconnected variable keeps its last value and says `Disconnected`.** The scene goes on
  drawing that value (R10); a row that blanked would contradict the viewport, and one that
  still said `Online` would contradict itself. *Why* it is disconnected is a separate
  question, and R20 is where the answer lives.

### R20 — A connection is discovered, then attempted, and every step of it is recorded

An endpoint and a publishing interval is what a mock server needs. A PLC asks for more: a
security policy, a mode, often a user name and a password — and when any of it is wrong it
answers with a status code and closes the channel. The first version of this could say only
*Disconnected*, which is the state, not the reason.

So a connection is now three things in the order they happen, and the dialog has one tab for
each: **discover** what the server offers, **attempt** one of those offers, **browse** what
turned out to be behind it.

**Discovery opens no session** (`io/opcua_security.discover_endpoints`, through asyncua's
`connect_and_get_server_endpoints`). That is what makes it usable as a first step: a server
that will refuse the credentials still says what it wants, and the answer is `EndpointOffer`
rows — policy, mode, the server's own `SecurityLevel`, and the `UserIdentityTokens` it accepts.

That last field is what pays for the whole tab. **A server that does not list `Anonymous`
refuses an anonymous session**, and that refusal used to arrive with nothing to read. Choosing
an offer now greys out the authentication it does not accept, so the case is visible before
connecting rather than diagnosed after.

The rest of the decisions:

- **Our own spellings, not asyncua's.** `SecurityMode` and `TokenType` are `StrEnum`s of ours,
  and `SecurityPolicy` states the name and the URI separately — the URI for
  `Aes128Sha256RsaOaep` reads `#Aes128_Sha256_RsaOaep`, so deriving one from the other would be
  wrong in exactly one case. It is also what keeps `ua.MessageSecurityMode` out of `ui/` and out
  of the settings file, where an `Invalid` or a `None_` would eventually be written.
- **The server's certificate comes from the discovery answer.** Trust on first use, and stated
  as such: it is what makes a secure connection possible without demanding a file the user has
  not been given. It is *not* certificate validation and is not claimed to be.
- **Our own certificate is generated once and reused**, in `%LOCALAPPDATA%/PSsimTool/pki/`,
  through `Client.setup_self_signed_certificate` — which stamps the client's `application_uri`
  into it. The lower-level `cert_gen` helper does not, and a server then warns about the
  mismatch on every connection. That call returns `(certificate, key)`, the reverse of its
  arguments; unpacking it the other way round fails much later, inside `load_certificate`.
- **The password is never stored.** `ui/settings.py` has no field for it, which is the
  enforcement rather than a convention: there is nowhere for it to be written by accident. It is
  typed once per session and held in `MainWindow`, or comes from `PSSIM_OPCUA_PASSWORD` for an
  unattended run. QSettings is a plain-text INI. The user *name* is remembered, because a
  machine does not change its mind between mornings.
- **A stored policy is validated on the way in**, like every other setting (R18). A name this
  build cannot speak would otherwise fail inside `set_security`, with a message about asyncua
  rather than about the file it came out of.

**The diagnostics log is the second half of the answer.** `io/opcua_diagnostics` records each
step — discover, select, certificate, channel, session, subscribe, write — with its outcome and,
on a failure, the OPC UA status code. `BadUserAccessDenied` *is* the answer to "why not", and it
was previously nowhere on screen. `Communication → Diagnostics…` opens the log without reopening
the connection dialog, because that question is asked long after the dialog was closed.

Two properties of the log that were got wrong first and are deliberate now:

- **It is append-only, not cleared per attempt.** A source reconnects for ever (R12), so
  clearing on each try wiped the failure explaining the last one about half a second after
  recording it. A bounded history is the guard instead.
- **`last_error` is `None` whenever the status is `CONNECTED`.** With an append-only log, a
  failed first attempt otherwise goes on being reported after a later attempt succeeded.

**Browsing after connecting is a live session, expanded lazily** (`io/opcua_browse_session.py`),
which is UaExpert's model and the only usable one: a PLC address space runs to thousands of
nodes and reading all of it to show one folder is not a wait anybody accepts. The session owns a
thread and an asyncio loop of its own for the same reason `OpcUaSource` does (R10), and
`asyncio.run_coroutine_threadsafe` is the bridge in.

The one-shot `io/opcua_browser.browse_variables` **stays**. The two answer different questions:
"walk the whole thing and hand me a list" is what `pssim probe` and a flat chooser want, and it
is already tested. What they must not do is disagree about which nodes are bindable, so
`NUMERIC_TYPES` is defined once and `BrowseNode.is_numeric` reads it.

One tree widget serves both dialogs (`ui/opcua_browse_tree.py`), with one difference: the tag
chooser calls `require_numeric()` and greys out what cannot drive a joint, the address-space
viewer does not. Nothing is being picked in the viewer, so a `String` node greyed out there
would be a judgement on a node nobody asked about.

**The mock server can refuse.** `--secure` gives it a certificate and a
`Basic256Sha256/SignAndEncrypt` endpoint beside the open one; `--require-user USER:PASSWORD`
adds a `UserManager` and offers only `UserName`, so anonymous is not on the list. Without those
the security half would be written rather than tested, and this is the only server this project
may point at (`.claude/rules/io-opcua.md`).

**Where the connection stands is a permanent widget**, on the right of the status bar
(`ui/connection_status.py`). Not a `showMessage` call: a message is wiped by the next
one, and the connection's state is not a message about something that just happened.
`DEGRADED` is not "Connected" with a footnote either — the link is alive, a signal has
stopped arriving, and the scene goes on drawing the old value (R10), which looks like
nothing being wrong. The reason is the tooltip and a click opens the log, because a
status code does not fit on one line beside the state.

`pssim probe <endpoint>` is tab one on the command line: it prints the offers before trying
anything, then connects with `--policy`, `--sign-only` and `--user`, and prints the diagnostics
log whether or not it got in. There is no `--password` — one typed on a command line lands in
the shell's history.

### R21 — A field of a structure is a node plus a path, not a node

The address-space browse stopped at a variable. On a mock server made of scalars
that is invisible; on a real PLC, whose address space is mostly structures, it means
every interesting value is behind a dead end. The stop was a written decision —
`has_children=kind is NodeKind.OBJECT` — taken to keep a tree of leaves from looking
like a tree of folders.

What makes opening it awkward rather than obvious is that **a server has no node for
a field**. `Struct.AxisState` is one node holding one `ExtensionObject`; `Position.X`
does not exist as anything. Two facts, both established in a REPL against a live
server before any of this was designed, rule out the alternatives:

- `subscribe_data_change` in asyncua leaves `IndexRange` null deliberately ("then the
  entire array is returned"), so a single array element cannot be subscribed to.
- asyncua's own server ignores `IndexRange` on a plain read as well: asking for `'1'`
  of a four-element array returned all four, with a good status code.

So a tag is a **node id plus a path** — `Position.X`, `Limits[1]`,
`Drive.Axes[2].Actual` — and the path is applied to the value where the unit
conversion already happens: at the boundary, once (R8). `io/opcua_path.py` is that
path, and it is **pure** — stdlib only, no asyncua — because it is the one half of
this feature testable in milliseconds, and the half where a mistake binds a joint to
the wrong number in silence.

The path is text rather than a structure because it goes into a settings file: one
spelling, readable by whoever opens the file, and nothing new in a versioned format.

**Three sources of children, tried in that order**, because only the first has real
nodes in it:

1. **the address space** — a PLC that exposes its struct members as `HasComponent`
   children needs nothing else, and it costs one request either way;
2. **the `DataType`'s `StructureDefinition`** — metadata, so a struct opens before
   anything has ever been written into it, which is most of a freshly started PLC;
3. **the value** — for an array, and only to learn how many elements there are.

The decisions worth the words:

- **A struct or an array is a `container`: openable, and never bindable.** Both
  halves matter. `Double[4]` reports its type as `Double`, so without the flag the
  array itself would look like a perfectly good tag and would hand a joint a list.
- **In the tree a container is dimmed, not disabled.** It is the one row here that
  *must* be opened — it is where the wanted field lives — while still reading as
  something that cannot itself be bound. Disabling it would rest on Qt letting a
  click through to the expander of a disabled row, which is not a thing to build a
  feature's central interaction on. The palette's disabled colour, so it is legible
  on a light theme and a dark one (R17); the same choice R15 made for a hidden
  model's row, for the same reason.
- **One notification feeds several signals.** `Position.X` and `Position.Y` are two
  signals reading two places in one arriving struct, so the source keeps a **list**
  of bindings per node. The dict keyed by node id that preceded it kept only the last
  of them, and three fields of one struct are still one monitored item.
- **`load_data_type_definitions()` is what makes a struct decode**, and it is called
  lazily: by the source only when some binding actually has a path, and by the browse
  session only before it first has to read a value. It is a round trip and a code
  generation, and every setup that existed before this reads plain scalars.
- **A path that does not fit costs one signal.** The other fields of the same struct
  still arrive and the subscription stays up. A field the server renamed is a
  configuration problem, not a reason to stop reading the machine.
- **A path is validated on the way out of the settings file** (R18). A malformed one
  would otherwise reach `resolve_value` on the source's thread and be reported once
  per notification, at the publishing rate, about a file nobody is looking at.

Three properties of the wire that are not guessable and cost time to find:

- **`ValueRank` is the array test, and it is reliable.** `-1` is Scalar; every other
  value is an array, `0` ("one or more dimensions") included. A four-element `Double`
  array reported rank `0` and left `ArrayDimensions` unset entirely — which is why
  the element count comes from the value and from nowhere else.
- **`ua.datatype_to_varianttype` is silently wrong for a custom type.** It reads the
  numeric identifier and ignores the namespace, so `ns=2;i=1` came back as
  `VariantType.Boolean`. Namespace 0 only; a custom type is asked for its own
  definition instead.
- **It is `node.session`, not `node.server`**, in asyncua 2.x. The old name raises an
  `AttributeError` that a `try` around a browse turns into an empty folder, which is
  exactly how it presented.

The mock server grew a struct, a nested struct, an array inside it and a bare array,
because none of this is testable otherwise and it is the only server this project may
point at. `Struct.AxisState.Position` tracks the same axes as the scalar nodes, so
reading through a path and reading the plain node must give the same number — that is
what pins the extraction rather than merely exercising it.

One test had to be moved out of process to be worth anything. An array *inside* a
struct needs the generated classes to decode the value it is counting; run in the
same process, the mock server has already registered those same classes on the
process-wide `ua` module, so the test passed whether or not the session ever loaded
them. It runs through `pssim probe --path` in a subprocess now, and it failed there
first.

**Not done, deliberately:** `machines/*.yaml` has no `path`. `JointBinding` carries
one and `io/` reads it, but wiring it into the versioned machine definition needs its
own migration story, and validating it in `config/schema.py` would mean `config/`
importing `io/`, which `tests/unit/test_layer_boundaries.py` forbids. The tag route —
which is what the address-space browser feeds — is complete.

## Performance

A STEP assembly typically has hundreds to thousands of parts, which is an unaffordable number  
of draw calls if approached naively. So when building the scene:

- parts that are **neither** a joint nor a descendant of one → `flattenStrong()` into a single Geom
- repeated parts (screws, conveyor rollers) → `instanceTo()`
- distant groups → `LODNode`
- moving parts stay separate `NodePath`s — those cannot be flattened

`viz/scene_builder.py` does the split into static and moving according to the joint definitions.

## What is deliberately unsolved

| Thing                          | State                                                              |
| ------------------------------ | ------------------------------------------------------------------ |
| Writing to the PLC             | sensor values only, behind a switch that is off by default (R19)   |
| IK / trajectory planning       | out of scope, the PLC supplies finished positions                  |
| Collisions                     | bounding-box **warning** only (R14); real contact geometry deferred |
| CAD formats other than STEP    | STEP is the minimum; IGES/JT/glTF can be added in `cad/`           |
| Several machines in one scene  | the data model allows it, the scene builder does not yet           |
| OPC UA certificate validation  | the server's certificate is trusted on first use (R20)            |
| A struct path in `machines/*.yaml` | the tag route is complete; the versioned format is not (R21)   |
| Packaging                      | `setup_dist.py` does not exist; `pssim write-icon` is what it needs |

---
name: domenovy-kontext
description: The PSsimTool domain model and glossary — joint, kinematic chain, signal, machine definition, tessellation, deflection, stale data, units. Use whenever a task mentions a domain term, or when working with domain/, config/, or a machine definition in machines/*.yaml.
---

# PSsimTool domain context

## Glossary

| Term | Meaning | Do not confuse with |
|---|---|---|
| **Machine** | the whole simulated assembly: geometry + kinematics + bound signals | the STEP file — that is geometry only |
| **Joint** | one degree of freedom: a `parent` node, a `child` node, an axis, a type, limits | an assembly node — a joint is a link *between* nodes |
| **Node** | an element of the CAD assembly tree, identified by the stable path `base/portal/Part1` | an OPC UA node — that is the address of a variable in the PLC |
| **Signal** | one value from the PLC over time, with its own history of samples | an OPC UA node — the node is an address, the signal is the stream of values |
| **Binding** | the mapping `joint ↔ OPC UA node`, including `scale` and `offset` | the joint itself — that knows nothing about the PLC |
| **JointPose** | the result of kinematics: translation + (axis, angle) for one joint | a scene transform — Panda3D composes that from the hierarchy |
| **Sample** | `(source_time, value)` — a value with the time it came into being in the PLC | the arrival time — that one is useless |
| **StateStore** | the thread-safe holder of the latest samples of all signals | the scene — that is only a consumer |
| **Deflection** | the tessellation tolerance: linear (mm) and angular (rad) | LOD — that is a different level of optimisation |
| **Stale** | a signal that has not arrived for longer than `stale_after_s` | missing — that one never appeared |
| **Render delay** | the deliberate sampling delay, so that it interpolates rather than extrapolates | latency — that one is unwanted |

## Invariants no change may break

1. **Inside the system there is one unit: metres and radians.** Conversion happens
   exclusively at the boundary (`config/loader.py`, `cad/`, `io/`). If you find a
   multiplication by `0.001` in `domain/` or `viz/`, that is a bug.
2. **Time is always the `SourceTimestamp` from the PLC**, converted once onto the internal
   monotonic scale in seconds (`float`). The PLC↔local time offset is determined on the first
   sample and **does not change**.
3. **`domain/` imports stdlib only.** No numpy, pydantic, panda3d, asyncua or OCP.
4. **A joint never changes its value by itself.** The value arrives from outside. Kinematics
   is a pure function `(joint, value) → JointPose`. No state, no memory of the previous frame.
5. **The kinematic chain is a tree, never a graph.** Every node has at most one parent.
   A cycle or multiple parents is a `ConfigError` at load time, not a runtime problem.
6. **A missing or stale signal never brings down the render.** The last known value is shown,
   visually marked as stale.
7. **The cache is disposable.** Nothing that cannot be rebuilt from `models/` + `machines/`
   belongs in `assets/cache/`.
8. **Nothing is written to the PLC.** The application is a reader.
9. **A joint moves relative to the placement from CAD.** The STEP file decides where the part
   sits at zero; the value from the PLC adds movement on top. Never overwrite the CAD
   placement — on the first value the part would jump to its parent's origin.
10. **Geometry is keyed by part definition, not by node path.** The same part used ten times
    has one mesh file and ten nodes pointing at it.

## Joint types

| Type | Signal value | Effect |
|---|---|---|
| `prismatic` | displacement in metres | translation along `axis` |
| `revolute` | angle in radians | rotation about `axis` |
| `fixed` | ignored | no movement, only a fixed offset in the hierarchy |

`axis` is a unit vector in the **parent's** coordinate system, not in the global one.
An unnormalised axis is a `ConfigError` — not silent normalisation, because the length of the
vector would otherwise scale the movement unnoticed.

## The life of a signal value

```
PLC variable
  │ OPC UA subscription, SourceTimestamp
  ▼
raw value (PLC units: mm, degrees, increments)
  │ binding: value * scale + offset        ← THE ONLY place of conversion
  ▼
Sample(source_time_s, value)  in metres / radians
  │ StateStore.put()  (thread B, under the lock)
  ▼
SignalBuffer — a ring buffer of the last N samples
  │ sample_at(t = now - render_delay)  → linear interpolation
  ▼
joint value
  │ domain/kinematics.joint_pose(joint, value)  → clamped to the limits
  ▼
JointPose(translation, rotation_axis, rotation_angle_rad)
  │ viz/ → NodePath.setPos() / setQuat()
  ▼
the screen
```

Every step of this chain has its own test in `tests/unit/`. When something does not add up,
locate it by this list — do not work backwards from the screen.

## Data source states

```
DISCONNECTED ──► CONNECTING ──► CONNECTED ──► DEGRADED
     ▲               │              │             │
     └───────────────┴──────────────┴─────────────┘
                   (reconnect, exponential backoff, capped at 30 s)
```

- `CONNECTED` — the connection is alive and every signal is fresh.
- `DEGRADED` — the connection is alive, but at least one signal is `stale`.
  **Rendering continues.**
- A transition to `DISCONNECTED` **does not clear** the data in `StateStore` — the scene
  stays on the last known state, marked as stale.

Implementation: `src/pssim/io/base.py` (`SourceStatus`). Add every new state **there too**,
or the HUD and the `DEGRADED` logic get bypassed.

## Details

If you need deeper detail, read these only when you actually need them:

- @referencie/opcua-mapovanie.md — the `machines/*.yaml` schema, OPC UA types, unit
  conversions, subscription settings and known server quirks
- @referencie/step-import.md — the sequence of OpenCASCADE calls, the structure of an XCAF
  document, the cache metadata format, known pathologies of real STEP files

<!--
Files in a skill's subdirectories are NOT loaded automatically. Claude reads them only when
the instructions above make it necessary. That is the point of progressive disclosure: the
skill description (1 line) is always in context, the skill body when it is invoked, and the
references only on request.
-->

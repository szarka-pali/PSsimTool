# OPC UA mapping and the machine definition schema

A reference document. Load it when working on `config/schema.py`, `config/loader.py`,
`io/opcua_source.py`, or when adding to or fixing `machines/*.yaml`.

## The complete `machines/*.yaml` schema

```yaml
machine: example                  # required, a unique identifier
description: "Gantry palletiser"

# --- geometry -----------------------------------------------------------
step_file: models/example.step    # required, path relative to the repository root
units: mm                         # mm | m | in — the units of the STEP file
tessellation:
  linear_deflection_mm: 0.5       # smaller = finer = more triangles
  angular_deflection_rad: 0.35

# --- connection ---------------------------------------------------------
# CAREFUL: an endpoint belongs here only if it is a local mock. Real endpoints
# go into the environment (PSSIM_OPCUA_ENDPOINT) — machines/*.yaml is versioned.
source:
  endpoint: opc.tcp://localhost:4840/pssim/
  publishing_interval_ms: 50      # what we ask for; the server may return another
  stale_after_s: 1.0              # no new sample within this → the signal is stale
  render_delay_ms: 100            # default: 2× the revised publishing interval

# --- kinematics ---------------------------------------------------------
joints:
  - name: axis_x
    parent: base                  # the stable node path in the assembly tree
    child: portal
    type: prismatic               # prismatic | revolute | fixed
    axis: [1, 0, 0]               # a unit vector in the parent's coordinates
    limits: [0.0, 2.5]            # in metres (prismatic) / radians (revolute)
    origin:                       # a fixed offset of the joint from the parent, optional
      xyz: [0, 0, 0.15]           # metres
      rpy: [0, 0, 0]              # radians
    signal:
      node: "ns=2;s=Axes.X.ActPos"
      scale: 0.001                # raw * scale + offset → metres/radians
      offset: 0.0
```

## Unit conversions — concrete values

| The PLC sends | We want | `scale` | `offset` |
|---|---|---|---|
| mm | m | `0.001` | `0.0` |
| µm | m | `1e-6` | `0.0` |
| degrees | rad | `0.017453292519943295` | `0.0` |
| 0.001° (typical for a servo) | rad | `1.7453292519943296e-05` | `0.0` |
| encoder increments, 4096/rev | rad | `2*pi/4096 = 0.001533980787885` | `0.0` |
| mm with zero at mid-stroke ±1250 | m, zero at the end | `0.001` | `1.25` |

`offset` is applied **after** `scale`, that is `value * scale + offset`. This order is fixed —
do not change it, existing YAML files would break silently.

## OPC UA types and what to do with them

| OPC UA type | Python | Note |
|---|---|---|
| `Double`, `Float` | `float` | the ordinary case for positions |
| `Int16/32/64`, `UInt*` | `int` | typical for encoder increments — `scale` is mandatory |
| `Boolean` | `bool` | for `visible`/`active` properties, not for joints |
| `String` | `str` | only for display in the HUD, never as a joint value |
| `DateTime` | `datetime` | UTC, tz-aware. A naive datetime is a bug |
| an array (`Double[]`) | `list[float]` | not supported yet — unpack into individual signals in the PLC |

If a signal bound to a joint is not numeric, that is a `ConfigError` on the first value
received, not a `TypeError` inside the kinematics.

## Subscription settings

```python
sub = await client.create_subscription(
    period=publishing_interval_ms,  # ms
    handler=handler,
)
handle = await sub.subscribe_data_change(
    nodes,
    queuesize=4,  # >1: when falling behind we still get the intermediate samples
)
```

What to watch out for:

- **The server is allowed to revise the intervals.** The real values are in the
  `CreateSubscriptionResponse` / `MonitoredItemCreateResult`. Compute `render_delay` from the
  **revised** value, not from the requested one.
- **`queuesize=1` throws away intermediate samples.** For position signals that means fast
  movement gives you only the end points and the interpolation cuts corners.
- **A deadband** (`DataChangeFilter`) saves network traffic, but on a position signal it
  smooths away small movement that may be exactly what you want to see. Default: no deadband.
- **One subscription for all signals.** N subscriptions = N× the overhead on the server and
  timing that drifts apart between signals.

## Known server quirks

| Server | Quirk |
|---|---|
| Siemens S7-1500 OPC UA | `SourceTimestamp` has the resolution of the OB cycle, not of milliseconds. Samples arrive "in jumps". Do not treat identical times as an error. |
| Beckhoff TwinCAT | The minimum `publishing_interval` is tied to the task cycle time. Asking for 10 ms on a 50 ms task returns 50 ms. |
| Codesys | Some versions do not send `SourceTimestamp` at all → fall back to `ServerTimestamp`. |
| KEPServerEX (gateway) | `SourceTimestamp` is the gateway's time, not the PLC's. Useless for accurate timing. |
| Prosys Simulation Server | Good for manual testing, but it generates ideal data — you will not catch timing problems with it. |

If you find another quirk on a real machine, **write it down here**, including the firmware
version. This is the only place where such information does not get lost.

## Security

Not implemented yet, but the interface is ready for it. When it gets done:

- `SecurityPolicy#Basic256Sha256` + `SignAndEncrypt` is a sensible default.
- Client certificate and key: paths from the environment (`PSSIM_OPCUA_CERT`,
  `PSSIM_OPCUA_KEY`), files in `certs/` (not committed, covered by `deny` rules).
- Never accept the server certificate automatically — the trust store is explicit.
- Credentials exclusively from the environment. Never in `machines/*.yaml`.

## Diagnosis when "it does not work"

The order to check things in — top to bottom, each step has its own tool:

1. **Does the endpoint exist?** `uv run pssim probe opc.tcp://...` prints the endpoints and
   security policies.
2. **Does the node exist?** `uv run pssim probe <endpoint> --browse ns=2` prints the available
   nodes.
3. **Is data arriving?** `uv run pssim record ... --verbose` prints every notification.
4. **Is it in the right units?** Compare the raw value from the recording with the value after
   `scale`. A machine a thousand times too large or too small = a forgotten or a doubled
   `scale`.
5. **Does the joint move?** If values arrive and the part does not move, the fault is in the
   node mapping (the `parent`/`child` path does not exist or points at a different part).
6. **Does it move correctly?** Wrong axis or wrong sign. Try `axis: [-1, 0, 0]`.

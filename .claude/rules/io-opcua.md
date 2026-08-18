# The `io/` layer — data from the PLC

This layer is the only place where asyncio runs and where more than one thread exists.
Bugs here show up as "it stutters sometimes" or "once an hour it jumps" — the hardest kind
of bug to debug in the whole project. Hence the strict rules.

## Threading model (do not change without discussion)

```
thread B: asyncio.run() → asyncua Client → subscription handler → StateStore.put()
thread A: Panda3D task  → StateStore.sample_all(at_time) → reading
```

- The Panda3D task manager is **not** an asyncio loop. It awaits Panda3D futures.
  That is why asyncua cannot run inside it. See `docs/architecture.md` R4.
- Thread B **never** touches Panda3D or the scene. It writes exclusively into `StateStore`.
- Thread A **never** calls asyncua or anything blocking.
- A new data source implements `DataSource` from `base.py` and nothing more.

## Timestamps

- Use the **`SourceTimestamp`** from OPC UA (when the value came into being in the PLC),
  not `ServerTimestamp` and certainly not the local arrival time.
- If `SourceTimestamp` is missing (some servers do not send it), fall back to
  `ServerTimestamp` and **log it once** — not on every notification.
- Convert to the internal scale (`float` seconds, monotonic) in one place (`_to_monotonic`).
  Estimate the offset between PLC time and the local monotonic clock on the first sample and
  **keep it constant** — otherwise interpolation falls apart.
- The PLC clock is not synchronised with yours. Do not assume it is.

## Subscriptions, not polling

- Always `create_subscription` + `subscribe_data_change`. No `read()` in a loop.
- `publishing_interval` and `sampling_interval` are configurable, not hardcoded.
- Expect the server to return a **different** interval from the one you asked for — that is
  its right. Read the revised value from the response and use it to compute `render_delay`.
- Set `queue_size` and `deadband` deliberately. A deadband on a position signal smooths away
  small movement, which may be exactly what you wanted to see.

## Resilience

- Losing the connection is a **normal state**, not an exception. Reconnect with exponential
  backoff, capped at 30 s. Log the first attempt and then every tenth, not every one.
- After a reconnect the subscription **must be re-established** — keep the node ids yourself,
  do not rely on the client doing it for you.
- If a signal has not arrived for longer than `stale_after_s`, mark it as stale.
  The scene shows that, but it **must not** stop rendering or freeze because of it.
- Never `raise` from a subscription callback — an exception in the handler can kill the whole
  loop. Log it and carry on.

## Security and environment

- Endpoint URL, user, certificate paths: **only from configuration or the environment**,
  never in code and never in `machines/*.yaml` (those are versioned).
- A customer's PLC addresses do not belong in the repository or in commit messages.
- Do not implement writing to a PLC without an explicit instruction. If you get one, it must
  sit behind an explicit switch and be tested **only** against the mock server.

## Testing

- `StateStore`, interpolation and `ReplaySource` are tested in `tests/unit/` — they are pure
  and take time as an argument.
- `OpcUaSource` is tested in `tests/integration/` against `mock_server.py`.
  Never against a real machine in an automated test.
- Reproducing a fault from the field means `recordings/*.jsonl` + `ReplaySource`, not guessing.

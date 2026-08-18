# Tests

## Structure

- `tests/unit/` — no I/O, no network, **no window, no OPC UA**.
  The whole directory must run in under 5 s.
- `tests/integration/` — against `pssim mock-server`. Mark with `@pytest.mark.integration`.
  Run with `uv run pytest -m integration`.
- One test file mirrors one module:
  `src/pssim/domain/kinematics.py` → `tests/unit/domain/test_kinematics.py`.
- Fixtures and factories: `tests/factories.py`. Do not build test data by hand inline.

## How we write tests

- The test name describes **behaviour, not implementation**:
  `test_prismatic_joint_moves_along_its_axis`, not `test_joint_transform_returns_tuple`.
- Arrange / act / assert, separated by blank lines.
- **One assertion per test** unless that is nonsense. Five small tests beat one big one.
- Mock **only the system boundary** (the OPC UA client, time, the filesystem).
  Never mock your own domain logic — if a test needs that, the design is wrong.

## Specific to this project

- **Never take time from `time.monotonic()` in a test.** Interpolation and the store take
  time as an argument (`sample_all(at_time=...)`) precisely so it can be supplied by a test.
  If you write code that fetches time itself, that is a design flaw — report it.
- **Compare numbers with a tolerance:** `pytest.approx(..., abs=1e-9)`. Kinematics is float
  arithmetic; exact equality is a coincidence.
- **Test units explicitly.** Every conversion (mm→m, deg→rad, `scale`/`offset` from YAML)
  must have a test with a concrete number. This is the most likely silent bug in this project.
- **Joint limits** always get tested: below the lower limit, exactly on it, above the upper
  limit, and a joint with no limits.
- **Interpolation** gets tested on degenerate input too: an empty buffer, a single sample,
  samples with the same timestamp, a query before the first and after the last sample.
- **Threads:** test `StateStore` with concurrent writes and reads from several threads.
  Without `sleep()` — use a `threading.Barrier`.
- Panda3D and OCP are **not imported** in `tests/unit/`. A test that needs either belongs in
  `tests/integration/`.

## Forbidden

- Tests that merely restate the implementation (`assert mock.called_once()` as the only assertion).
- `sleep()` for synchronisation — use a barrier, an event, or explicit time as an argument.
- Tests that depend on execution order or on shared global state.
- Skipping (`skip`) without a comment giving the reason.
- Tests loading real STEP files from `models/` — those are not in the repository.
  Use generated geometry or a small fixture in `tests/data/`.

## When adding functionality

1. First write a test that **fails** and captures the wanted behaviour.
2. Show me that it fails, **and why it fails** — a failing test that fails on `ImportError`
   proves nothing.
3. Only then implement.
4. Run `uv run pytest tests/unit -q` and show the output.

When fixing a bug, always start with a reproducing test. A bug without a regression test is
not considered fixed.

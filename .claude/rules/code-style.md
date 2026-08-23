# Code style

Formatting is settled by `ruff format`, not by discussion — do not worry about indentation
or brackets. These are the rules the linter does not catch.

## Naming

- Functions are **actions** (`sample_signal`, `build_scene`), variables are **things** (`position`).
- **Everything in English** — identifiers, comments, docstrings, logs and user-facing text.
  The reason is in `CLAUDE.md`, section *Language*. Translate old Slovak comments only when
  you touch the file for another reason.
  Keep domain terms in the form given by the glossary (`joint`, `signal`, `deflection`).
- **User-facing text** must be wrapped in `self.tr()` (inside a `QObject`) or
  `QCoreApplication.translate("Context", "text")` (at module level). Unwrapped text never
  reaches the `.ts` file and will never be translated.
  See `src/pssim/ui/translations/README.md`.
- **Logs are not translated** through `tr()` — they are for the developer, not the user.
  But they are written in English, like everything else.
- No abbreviations except the established ones (`id`, `url`, `cad`, `hpr`, `rpy`).
  Not `jnt_cnt` but `joint_count`.
- Booleans start with `is_`/`has_`/`can_` (`is_stale`, `has_limits`).
- If you need `and`/`or` in a name, the function does two things — split it.
- **Units in the name when the type does not make them obvious:** `delay_s`, `interval_ms`,
  `angle_rad`, `length_mm`. This is the most common source of bugs in this project — do not
  save characters on it.

## Types

- Every public function has **full type annotations**. `pyright` runs in `strict` mode on `src/`.
- No `Any` without a comment explaining why. No `# type: ignore` without a stated reason.
- Data carriers: `@dataclass(frozen=True, slots=True)`. Mutable state only where it has an
  owner and a lock (`io/store.py`).
- Define boundaries between layers as a `typing.Protocol`, not an abstract base class.
  The implementation then does not have to be imported into the module that consumes it.

## Functions and modules

- A function fits on a screen (~40 lines). If it does not, it is missing a helper.
- At most 3 levels of nesting. Use early returns instead of `else`.
- Arguments: at most 4. More → a frozen dataclass with named fields.
- No boolean flags in a signature that switch behaviour (`load(path, tessellate=True)`).
  Two functions is better.

## Errors

- Raise the typed errors from `src/pssim/domain/errors.py`, not a bare `Exception`.
- **Never** swallow an exception silently. Either handle it or re-raise it with context
  (`raise ConfigError(...) from exc`).
- Validate input at the system boundary (`config/`, `io/`, `cad/`), not deep in the domain.
  `domain/` may assume the data is valid and in the right units.
- An error in one signal **must not** bring down the render loop. `viz/` has to survive a
  missing or invalid signal — it shows the last known state and marks it as stale.

## Threads and asyncio

- `asyncio` runs **only** in `io/`. No `async def` anywhere else.
- Shared state between threads exists in exactly one place: `io/store.StateStore`.
  Do not add a second one. If you need to share something else, extend the store.
- Hold the lock as briefly as possible: copy data under the lock, do not compute under it.
- No `time.sleep()` in production code outside `io/replay.py`.

## Panda3D

- Panda3D types (`NodePath`, `LVector3`, `LQuaternion`, `Geom`) must not appear in
  signatures outside `viz/`.
- No work in the render task that could be done once while building the scene.
- Never load an asset synchronously at run time — use `loader.loadModel(..., blocking=False)`
  or load before the first frame.

## Comments

- A comment explains **why**, never **what**. `# increment i by 1` is noise.
- A non-trivial decision → one sentence on why, ideally with a reference to
  `docs/architecture.md` (e.g. `# see R10: asyncua cannot run in the Panda3D task manager`).
- No `TODO` without a name attached. No commented-out dead code — delete it, git remembers.

## Dependencies

- New dependencies **only after approval**. Write down why the stdlib or a library already
  present is not enough.
- `domain/` imports **stdlib only**. Not numpy, not pydantic.
- Heavy imports (`OCP`, `panda3d`, `PySide6`) never at module level in `cli.py` — import them
  inside the command that needs them. Otherwise `pssim --help` takes seconds.

## Changing existing code

- Imitate the pattern already present around you, even if you would have written it differently.
  Consistency is worth more than your preference.
- Do not refactor unrelated code "while you are there". If you see a problem, mention it,
  but do not change it.
- Preserve public signatures unless you have an explicit instruction to change them.

# .claude/rules/

A modular alternative to one large `CLAUDE.md`.

The difference:

- **`CLAUDE.md`** — loaded **always**, at every session start. Hence: keep it short.
- **`rules/*.md` without `paths:`** — also loaded always (same priority as `CLAUDE.md`),
  just split into files by topic.
- **`rules/*.md` with `paths:`** — loaded **only when** Claude touches a file matching the
  glob. This is the reason rules exist: the OPC UA rules cost you no context until you
  are working on `io/`.

## What is in this project

| File | `paths:` | When it loads |
|---|---|---|
| `git-workflow.md` | — | always |
| `code-style.md` | `src/**` | when working on production code |
| `testing.md` | `tests/**` | when working on tests |
| `io-opcua.md` | `src/pssim/io/**`, `tests/integration/**` | when working on data sources |
| `cad-import.md` | `src/pssim/cad/**` | when working on geometry import |

Long reference material (the domain glossary, the OPC UA signal mapping, notes on the
OpenCASCADE API) is **not** in rules — it is in `.claude/skills/domenovy-kontext/`, because
a skill loads only when it is relevant and its references only on demand.

## How to split further rules

| Content | Where |
|---|---|
| Build/test commands, architectural boundaries, what not to edit | `CLAUDE.md` |
| Code style for a specific language or layer | `rules/*.md` with `paths:` |
| Rules for tests | `rules/testing.md` |
| Long reference material (a protocol, the domain, a foreign API) | a **skill**, not a rule |

Subdirectories are allowed and have no effect on loading — they are only for organisation.
The same structure works in `~/.claude/rules/` for rules that apply across all projects.

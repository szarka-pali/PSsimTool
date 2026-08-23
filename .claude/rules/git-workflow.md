# Git workflow

## Branch

- Never work directly on `main`. If you are on it, create a branch first.
- Naming: `type/short-description`, e.g. `feat/opcua-subscription`, `fix/mm-to-m-double-conversion`.
  Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.

## Commits

- **Small and atomic.** One logical change = one commit. A refactor and a new feature are two commits.
- Message format: Conventional Commits. The scope is the layer name (`domain`, `io`, `cad`, `viz`,
  `config`, `ui`).
  ```
  feat(io): add OPC UA subscription with source timestamps

  Why: polling in the render loop caused fps drops with more than 20 signals.
  See docs/architecture.md R10.
  ```
- First line at most 72 characters, imperative mood, no full stop at the end.
- The body explains **why**, not what — the diff already says what.
- Commit **after every step that passes**, not once at the end of a large change.
  It gives me points to go back to.
- If you wrote code against a library API you had to verify (`asyncua`, `OCP`),
  write **how you verified it** into the commit body. It saves the next person an hour.

## Never

- `git push --force` to a shared branch.
- `git commit --amend` on a commit that has been pushed.
- A commit with failing `ruff check` or `pytest tests/unit`.
- A commit containing `.env`, certificates, a customer's PLC addresses or tokens.
- Committing **CAD files** (`models/**`), **tessellated meshes** (`assets/cache/**`)
  or **recordings** (`recordings/**`). They are large and either generated or confidential.
  Recordings from a real machine may contain customer data.
- `git add .` without looking at `git status` first.

## Pull requests

- PR description: what, why, how to test it. Link the ticket.
- If the change affects behaviour towards a PLC, state **what you verified it against**
  (mock server / recording / a real machine, and which one).
- If the PR exceeds about 400 lines of diff, propose splitting it before opening it.

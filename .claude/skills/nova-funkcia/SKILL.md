---
name: nova-funkcia
description: A guided procedure for implementing new functionality — from the brief through a plan and tests to the commit. Use when a new feature, endpoint, module or larger behavioural change has to be added.
---

# New functionality: $ARGUMENTS

Work through the phases. **After phase 2, stop and have the plan approved.**
Do not move forward until the previous phase is finished.

## Phase 1 — Understanding (edit nothing)

1. Work out where in the codebase the change belongs. Find the **closest analogous existing
   module** and read all of it — that is your pattern.
2. Find who will call the new code, and what it will call.
3. Read the existing tests of the analogous module.
4. Write down what you found: the files affected, and the existing pattern you will follow.

**If the brief is ambiguous, ask NOW.** Concrete questions, not "shall I carry on".
Typically unclear: error states, backwards compatibility, behaviour on empty input, who is
allowed to call it, what should be logged.

## Phase 2 — The plan (edit nothing)

Write the plan in this format:

```
Goal:        one sentence, measurable.
Unchanged:   what explicitly stays as it is (public API, schema, ...).
Steps:
  1. [file] what I will add/change there
  2. ...
Tests:       the concrete cases that will prove it works.
Risks:       what could break elsewhere in the system.
Open:        decisions I need from a human.
```

If the plan exceeds about 8 steps or about 5 files, propose splitting it into separate PRs.

**STOP. Wait for the plan to be approved.**

## Phase 3 — Tests first

Write the tests from phase 2. Run them. **Show that they fail, and why they fail** — a
failing test that fails for the wrong reason (import error, typo) proves nothing.

## Phase 4 — Implementation

- Make the **smallest** change that turns the tests green. No functionality "in reserve".
- After every logical step run the fast tests and the linter.
- If you find the plan was wrong, **stop and say so** — do not quietly improvise a different
  design.

## Phase 5 — Closing

1. Run the whole test suite, the linter and the type check. Show the output.
2. Go through `git diff` and remove: debug prints, commented-out code, unrelated formatting
   changes.
3. Have it checked by the `code-reviewer` agent and resolve the findings, or explain why you
   are ignoring them.
4. Commit according to `.claude/rules/git-workflow.md`.
5. A summary: what is done, what is not covered, what you deliberately left for later.

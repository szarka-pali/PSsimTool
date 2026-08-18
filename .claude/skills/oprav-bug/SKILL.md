---
name: oprav-bug
description: A systematic procedure for debugging and fixing a fault — reproduction, isolating the cause, a regression test, the fix. Use for a reported bug, a failing test, an error in the log or wrong output.
---

# Bug: $ARGUMENTS

A rule not to be worked around: **reproduce first, fix afterwards.**
A fix without a reproduction is guessing, and half the time it fixes something else.

## 1. Reproduction

- Establish the exact conditions: input, system state, version, environment.
- Write a **failing test** that captures the bug. Run it, show the output.
- If the bug cannot be reproduced, **say so** and write down what information you need.
  Do not carry on from an assumption about what is probably wrong.

## 2. Isolating the cause

- Narrow it down to the smallest piece of code where the behaviour changes. Use logging or
  `git bisect`, not reading the code top to bottom.
- Answer the question **why** it fails, not just where. "There is a `None` here" is not a
  cause — the cause is who let that `None` in and why.
- If there are two causes, report both.

## 3. The extent of the damage

Before you fix it, find out:

- Is the same pattern **used elsewhere**? (`Grep`) If so, list every place.
- Is it a data fault that has already left inconsistent data in the database?
- Is there a reason the code is the way it is (an old ticket, deliberate behaviour)?

## 4. The fix

- Fix the **cause, not the symptom**. If you are adding `if x is None`, ask why `x` is None.
- The smallest possible change. Do not refactor along the way.
- The test from step 1 must pass, and **all the other tests must still pass**.

## 5. Closing

- The regression test stays in the repository permanently.
- The commit message says: what was wrong, why, and how it works now.
- If you found similar occurrences elsewhere and did not fix them, **list them explicitly**.

## Anti-patterns to avoid

- Trying random changes until it stops failing.
- Widening a `try/except` so the error disappears from the log.
- Adjusting the test so it passes, instead of fixing the code.
- Fixing three unrelated things in one commit.

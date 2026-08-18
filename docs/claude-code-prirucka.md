# Claude Code for code-driven AI development

A guide to setting up a repository so that an agent works predictably in it — and so that
the same setup carries over to the whole team automatically.

---

## Part 1 — The basic idea

**Context is code.** Everything the agent needs to know about the project lives in the
repository, goes through code review and has a git history. Not in a senior developer's head,
not in Notion, not in a chat that disappears when the window closes.

Three practical consequences follow:

1. **The agent's configuration is versioned.** When someone notices the model repeatedly
   making the same mistake, the fix is not "write it into the prompt" — it is a commit into
   `.claude/rules/`. From then on the fix applies to everyone.

2. **Verification is automated, not judged.** The agent must have a command that tells it
   *yes/no*: tests, linter, type check, build. Without one it works blind, and so do you.
   This is the single most important thing in the whole guide.

3. **Deterministic things are handled by hooks, not by instructions.** "Always format the
   code" in `CLAUDE.md` is advice the model will occasionally skip. A hook always runs.

The difference between a team that Claude Code saves time for and a team that spends time
correcting it is almost always this: does the repository have verification built in, or not?

---

## Part 2 — Overview of the files

```
project/
├── CLAUDE.md                       ← always in context. Short. Commands + boundaries.
├── .mcp.json                       ← external tools (GitHub, DB, browser)
├── .worktreeinclude                ← what to copy into a new worktree
├── .gitignore                      ← add .claude/settings.local.json
└── .claude/
    ├── settings.json               ← permissions + hooks (versioned, team-wide)
    ├── settings.local.json         ← personal (NOT versioned)
    ├── rules/                      ← modular rules, loaded by path
    │   ├── code-style.md
    │   ├── testing.md
    │   └── git-workflow.md
    ├── agents/                     ← subagents with their own context
    │   ├── code-reviewer.md
    │   ├── explorer.md
    │   └── test-writer.md
    ├── skills/                     ← workflow procedures + long references
    │   └── nova-funkcia/SKILL.md
    └── hooks/                      ← scripts that always run
        ├── format-and-lint.sh
        └── block-dangerous-bash.sh
```

### The key table: what belongs where

This is the most common mistake when setting things up — everything gets poured into
`CLAUDE.md`, it grows to 600 lines and the model stops respecting it.

| Type of information | Where | When it loads | Context cost |
|---|---|---|---|
| Build/test commands, architectural boundaries, what not to edit | `CLAUDE.md` | always, at startup | **high** — every session |
| Code style for a language, rules for tests | `.claude/rules/*.md` with `paths:` | only when a matching file is touched | low |
| A repeated procedure (implement a feature, fix a bug, release) | `.claude/skills/*/SKILL.md` | the description always, the body on invocation | very low |
| A long reference (domain, protocol, legacy system) | a skill + a `referencie/` subdirectory | only on request | almost none |
| A specialised role with its own context | `.claude/agents/*.md` | when delegating | an isolated window |
| An unbreakable guarantee (formatting, blocking commands) | a hook in `settings.json` | on an event | **none** |
| Permissions, what may run without asking | `.claude/settings.json` | always | none |

**Rule of thumb:** if a piece of information is used in fewer than half of your sessions, it
does not belong in `CLAUDE.md`.

---

## Part 3 — `CLAUDE.md` in detail

It loads at every start. Hence: **aim for under 150 lines**, never above 200. Above that
threshold the model stops following the rules — not because it cannot "see" them, but
because in a sea of 400 lines the important gets lost among the unimportant.

### Hierarchy (everything loads, from the most general down)

| Location | Purpose | Version it |
|---|---|---|
| `/etc/claude-code/CLAUDE.md` (Linux) | company rules, managed by IT | — |
| `~/.claude/CLAUDE.md` | your personal preferences, all projects | no |
| `./CLAUDE.md` or `./.claude/CLAUDE.md` | project rules | **yes** |
| `./CLAUDE.local.md` | your notes on this project | no (gitignore) |
| `./subdirectory/CLAUDE.md` | rules for a monorepo package | **yes** |

Nesting works in a monorepo: `packages/api/CLAUDE.md` loads only when Claude touches a file
in `packages/api/`. This is the right way to handle a monorepo — not one gigantic file at
the root.

### What to write in it

The test for whether a line earns its place: **"would Claude work this out from the code in
5 seconds?"** If yes, delete it.

It belongs here:

- **Commands.** How to run the build, the tests (fast vs. all), the linter, the type check.
  This is the most valuable content in the whole file.
- **An explicit sentence about verification.** For example: *"After every change run
  `make lint && make test-unit`. If it does not pass, fix it before reporting back."*
- **Architectural boundaries** that are not obvious from the code ("the domain does not
  import the framework").
- **What must not be edited.** Generated code, lock files, production infrastructure.
- **A reference pattern.** "The module `src/domain/orders/` is our benchmark — imitate it."
- **What to do when something is unclear.** Ask? Choose and note it down? This genuinely works.

It does not belong here:

- A directory listing generated by `tree` (Claude will read it itself).
- Statements of the obvious such as "the project uses TypeScript".
- Library documentation — link to a URL or put it in a skill.
- Things that change every week (the current sprint, who is working on what).
- Long essays about code philosophy. Write rules, not a manifesto.

### A practical tip

`/init` generates a first version automatically. Treat it as a **draft, not a result** —
it is usually too long and too descriptive. Cut it in half.

Then keep it alive: whenever the agent makes a mistake that a rule would have caught, add
that one line. After a month you have a file that captures the project's real sore points,
not the ones you guessed at the beginning.

---

## Part 4 — `.claude/rules/` — modular rules

The difference from `CLAUDE.md` is a single one, and it matters:

```yaml
---
paths:
  - "tests/**"
  - "**/*.test.ts"
---
```

A rule with `paths:` loads **only when** Claude reads or edits a matching file. Rules for DB
migrations therefore cost you nothing until you are doing migrations. Without `paths:` it
always loads — the same priority as `CLAUDE.md`, just split up by topic.

A split that works well in practice:

| File | `paths:` | Content |
|---|---|---|
| `code-style.md` | `src/**` | naming, function size, errors, comments |
| `testing.md` | `tests/**` | test structure, what to mock, what is forbidden |
| `git-workflow.md` | *(none)* | branches, commits, PRs — always applies |
| `migrations.md` | `migrations/**` | never edit a merged migration, ... |
| `api-contract.md` | `src/api/**` | versioning, breaking changes, error codes |

The same works in `~/.claude/rules/` for rules that apply across all your projects.

---

## Part 5 — Skills: procedures and long references

A skill has two completely different uses and both are useful.

### A) A workflow skill — a procedure encoded once

Instead of writing the same long prompt for every feature, you write it once. After that
`/nova-funkcia add order cancellation` is enough.

A good workflow skill has **phases and stop points**:

```
Phase 1  Understand — edit nothing, find an analogous module, read it
Phase 2  Plan — write the plan in the given format  →  STOP, wait for approval
Phase 3  Tests first — show that they fail and WHY they fail
Phase 4  Implementation — the smallest change that turns the tests green
Phase 5  Closing — the whole suite, review, commit, a summary of what is not covered
```

That stop point after phase 2 is the most valuable line. Approving a plan takes 30 seconds;
throwing away 200 lines of wrong code takes an hour and costs you your mood.

### B) A reference skill — long material that loads only when needed

This is the answer to *"where do I put 800 lines describing our domain?"*.

```
.claude/skills/domenovy-kontext/
├── SKILL.md              ← glossary, invariants, state transitions (~100 lines)
└── referencie/
    ├── api-protokol.md   ← 500 lines, loads only when needed
    └── legacy.md         ← 900 lines, loads only when needed
```

Three-level disclosure:

1. **The skill description** (one line of frontmatter) — always in context. Claude decides
   from it whether to open the skill at all. So write it as *when to use this*, not *what it
   is*.
2. **The body of `SKILL.md`** — loads on invocation.
3. **Files in subdirectories** — only when Claude asks for them, following the instructions
   in the body.

This way you can have 3000 lines of domain knowledge in the repository that cost you nothing
in an ordinary session.

> Note: `.claude/commands/*.md` and `.claude/skills/*/SKILL.md` do the same thing today —
> both create a `/name`. For new things use `skills/`, because it tolerates subdirectories
> and references.

---

## Part 6 — Subagents: isolating context

A subagent gets **its own context window** and returns only a summary. That is precisely its
value, not "parallelism".

Use one when this holds: *a lot of reading → little output*.

| Agent | What for | What to forbid it |
|---|---|---|
| `explorer` | search 40 files and answer a question | editing (`tools: Read, Grep, Glob`) |
| `code-reviewer` | an independent review of a diff | editing — it should report, not fix |
| `test-writer` | fill in missing tests | changing production code |

Two things decide whether an agent will be useful:

**1. The `description` decides when Claude uses it.** Write it as a trigger, not as a title.
Not *"an agent for code review"* but *"Use after finishing a feature, when an independent
look at correctness and security is needed. It edits nothing."*

**2. A precisely defined output format.** An agent with no prescribed output returns three
paragraphs of padding. Give it a template — and tell it what to do when it finds nothing
(*"say so directly and state what you checked; do not invent findings"*).

A useful combination for a reviewer: `model: opus` for critical judgement, and no editing
tools, so it has no opportunity to rewrite something "while it is there".

---

## Part 7 — Permissions and hooks: guarantees, not advice

### Permissions (`.claude/settings.json`)

The goal is not maximum security — it is **removing the clicking through confirmations** for
things that are obviously safe, so your attention is left for the things that are not.

```json
"permissions": {
  "allow": ["Bash(make *)", "Bash(git diff *)", "Edit(src/**)"],
  "ask":   ["Bash(git push *)", "Bash(npm install *)"],
  "deny":  ["Read(**/.env*)", "Edit(src/generated/**)", "Bash(git push --force*)"]
}
```

What you need to know about the syntax:

- `Bash(make *)` — a prefix match; the space before `*` means a word boundary.
- `Read(**/.env)` — gitignore-style glob; `**` crosses directories.
- `Read(~/.ssh/**)` — `~/` is the home directory, `//path` is an absolute path from the root.
- `WebFetch(domain:*.internal.company.com)` — restriction to domains.
- `mcp__github__*` — all tools from an MCP server.
- Compound commands (`a && b`) are evaluated **part by part** — each must pass on its own.

Order and merging: rules **merge** across all levels (company → project → local), but `deny`
always wins. What is forbidden in the project settings cannot be re-enabled locally.

**Do not use `--dangerously-skip-permissions` on your own machine.** It has its place in CI
or in a container, where even a completely unleashed agent has nothing to break. Locally it
means giving up exactly the control that is there to protect you. Invest 20 minutes into a
good `allow` list instead — you get 90 % of the convenience without the risk.

### Hooks

A hook is a shell script that runs on an event. The difference from an instruction in
`CLAUDE.md`: **a hook always executes**, the model cannot skip it or "forget".

Useful events:

| Event | When | Typical use |
|---|---|---|
| `PreToolUse` | before a tool call | block a dangerous command (exit 2) |
| `PostToolUse` | after a tool call | format and lint the changed file |
| `UserPromptSubmit` | when a prompt is sent | add context (current branch, ticket) |
| `Stop` | when Claude wants to finish | force the tests to run before finishing |
| `SessionStart` | at the start of a session | print the git state, restate the rules |

The contract: JSON on stdin, `exit 0` = carry on, `exit 2` = **block** (the text on stderr
goes back to Claude as the reason), any other non-zero = a warning without blocking.

Two hooks that pay off in almost every project:

1. **Formatting after every edit.** The diff stops containing noise and you stop repeating
   "format it".
2. **Blocking destructive commands** based on state that is hard to express as a pattern —
   for example "pushing is forbidden if the current branch is `main`".

Do not think of hooks as a security boundary against a malicious actor. They are safeguards
against an unfortunate accident. Real protection comes from permissions and the environment.

---

## Part 8 — Working with a session: what actually changes the outcome

### Plan before anything gets written

`Shift+Tab` switches permission modes; **plan mode** is the most useful one. In it Claude
only reads and proposes a plan. You approve it, or send it back.

For anything touching more than two or three files it always pays off. A model that starts
writing before it understands the task is not producing code — it is producing work for you.

### Context is an exhaustible resource

- `/clear` **between unrelated tasks.** Finished a feature and moving to a different bug?
  `/clear`. Leftovers from the previous task only make the quality worse.
- `/compact` when the conversation is long but the topic continues. You can instruct it what
  to keep: `/compact keep the design decisions and the list of changed files`.
- `/context` shows what the context is filled with. When it feels like too much, look here —
  the culprit is often an overgrown `CLAUDE.md`.
- **Delegate exploration to a subagent.** "Where are webhooks handled?" — let `explorer`
  search and return three lines, rather than your main session filling half its window with it.

### Verification instead of description

This is the difference between a prompt that works and one that does not:

> ❌ "add password validation"

> ✅ "Add `validate_password` to `src/auth/validate.py`. It must reject: empty, shorter than
> 12 characters, without a digit, from the list of most common passwords. Accept: anything
> else. Tests into `tests/unit/auth/test_validate.py`, take the pattern from
> `test_validate_email.py`. Run `make test-unit` and show the output."

The difference is not the length. It is that the second prompt contains **a success criterion
the agent can check itself**. With the first one, you are the one who finds out whether it
works.

### When it is going badly

- `Esc` — stop it the moment you see it going the wrong way. Do not wait for it to finish.
- `Esc Esc` or `/rewind` — take the code and the conversation back to an earlier point.
  Careful: **changes made through bash are not tracked** (`rm`, `mv`); rewind will not undo
  those. Commit often.
- **After the second failed correction, `/clear` and rephrase the task.** A third "no, I
  meant…" in the same conversation almost never helps — the context is already polluted by
  the wrong attempts. Write the task again, enriched with what you have learned in the
  meantime.

### Working in parallel

`claude --worktree feat-storno` creates an isolated git worktree. Two sessions on two
features do not tread on each other's files. `.worktreeinclude` makes sure `.env` and similar
non-committed files get copied into the new worktree.

### Useful commands

| Command | What for |
|---|---|
| `/init` | generate a first version of `CLAUDE.md` |
| `/context` | what the context is filled with |
| `/clear`, `/compact` | reset / compress the conversation |
| `/rewind` | take the code or the conversation back |
| `/code-review` | review a diff in its own context |
| `/security-review` | check for security problems |
| `/permissions` | review and edit permissions |
| `/agents`, `/skills` | what is available |
| `/mcp` | the state of the MCP servers |
| `/model`, `/effort` | model and reasoning level |
| `/usage`, `/cost` | consumption |

---

## Part 9 — Rolling it out in a team

Do not do it all at once. A sequence that works:

**Week 1 — the basics.** `/init`, cut the result in half, add the commands section and the
explicit sentence about verification. Commit. That alone gives 70 % of the effect.

**Week 2 — permissions.** Look at what you confirm most often and add it to `allow`. At the
same time write a `deny` for what must never happen. Commit.

**Week 3 — the first hook.** Formatting after an edit. The diffs get clean.

**Week 4 — a reviewer and the first skill.** A `code-reviewer` agent and a workflow skill for
the procedure your team performs most often.

**Continuously.** Establish the habit: when the agent repeats the same mistake a second time,
it is not its mistake but a missing line in `rules/`. Whoever notices, commits it.

### What to version

| Version it | Do not commit |
|---|---|
| `CLAUDE.md` | `.claude/settings.local.json` |
| `.claude/settings.json` | `CLAUDE.local.md` |
| `.claude/rules/`, `agents/`, `skills/`, `hooks/` | `.claude/worktrees/` |
| `.mcp.json`, `.worktreeinclude` | `.claude/agent-memory-local/` |

When a colleague clones the repository, their agent behaves the same as yours. That is the
whole point.

---

## Part 10 — Anti-patterns

| Anti-pattern | Why it is a problem | Instead |
|---|---|---|
| A 500-line `CLAUDE.md` | the model stops following the rules | split into `rules/` with `paths:` |
| No test command | the agent has no way to tell whether it works | at least one fast `make test-unit` |
| `--dangerously-skip-permissions` locally | you give up exactly the control protecting you | a good `allow` list |
| One session for the whole day | the context gets polluted, quality drops | `/clear` between tasks |
| "Do it properly" as a rule | unenforceable, the model interprets it as it likes | concrete rules + a linter |
| The same long prompt written again and again | waste and inconsistency | a workflow skill |
| Exploration in the main conversation | 40 files read into the context | a subagent that returns a summary |
| Committing only at the end of a large change | there is nowhere to go back to | commit after every step that passes |
| The instruction "always format" | the model sometimes skips it | a hook |
| Rules only in a senior developer's head | works for nobody else | a commit into `rules/` |
| The whole domain in `CLAUDE.md` | 800 lines in every session | a reference skill |

---

## Part 11 — Checklist

The basics:

- [ ] `CLAUDE.md` exists, is under 200 lines, and contains the build/test/lint commands
- [ ] It has the explicit sentence *"after a change run X; if it does not pass, fix it"*
- [ ] It says what **must not be edited** (generated code, lock files, production infra)
- [ ] A fast test command exists that runs in a few seconds
- [ ] `.claude/settings.json` has `allow` for frequent safe commands
- [ ] `.claude/settings.json` has `deny` for secrets and destructive operations
- [ ] `.claude/settings.local.json` is in `.gitignore`

The next level:

- [ ] Rules split into `.claude/rules/` with `paths:`
- [ ] A hook for formatting after an edit
- [ ] A `code-reviewer` agent with no editing rights
- [ ] A workflow skill for the team's most frequent procedure
- [ ] Domain reference as a skill, not in `CLAUDE.md`
- [ ] `.mcp.json` with no hardcoded tokens (only `$VARIABLE`)
- [ ] `.worktreeinclude` for working in parallel

Operations:

- [ ] The team knows that the fix for a repeated mistake is a commit into `rules/`, not a
      better prompt
- [ ] Plan mode is used for larger changes
- [ ] `/clear` between unrelated tasks is a habit

---

## Sources

Current documentation — schemas and keys evolve, so check the state of the details:

- [Overview](https://code.claude.com/docs/en/overview) ·
  [Quickstart](https://code.claude.com/docs/en/quickstart) ·
  [Best practices](https://code.claude.com/docs/en/best-practices)
- [Memory / CLAUDE.md](https://code.claude.com/docs/en/memory) ·
  [Settings](https://code.claude.com/docs/en/settings) ·
  [Permissions](https://code.claude.com/docs/en/permissions)
- [Skills](https://code.claude.com/docs/en/skills) ·
  [Subagents](https://code.claude.com/docs/en/sub-agents) ·
  [Hooks](https://code.claude.com/docs/en/hooks)
- [MCP](https://code.claude.com/docs/en/mcp) ·
  [Worktrees](https://code.claude.com/docs/en/worktrees) ·
  [CLI reference](https://code.claude.com/docs/en/cli-reference)

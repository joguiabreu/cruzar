---
name: grill
description: >-
  The development cycle for Cruzar. Use for ANY task with 3+ steps or that
  touches an AC/ADR. Runs a five-phase grilling loop — extract context from the
  user, self-red-team the draft with a cold-start subagent, defend the plan live
  under the user's grilling (with a code walkthrough of risky paths), finalize
  the survivor as docs/plans/plan_NNN_<slug>.md, then after implementation close
  by grilling the result against the plan. Replaces the old "plan-first HTML" flow.
---

# Grilling loop

The way we run work in this repo, entrance to exit. We think live, in chat; the
plan file is where decisions land as they are made — it is both the running
checkpoint and the final record, so context survives across sessions because it
is a real file on disk.

Read `docs/DEVELOPMENT.md` for the *why*. This file is the *how*.

**Phases 1–4 are planning — do not implement during them.** They end at a
finalized plan. Only start coding after the user replies "address notes,
implement" (or equivalent). Phase 5 runs *after* implementation, to close the loop.

## When to run

- Any task with 3+ steps, or that touches an AC or ADR → full loop, all phases.
- Small, single-surface changes → skip Phase 2's subagent; a quick inline
  self-critique is enough. Still extract context (Phase 1) if anything is
  ambiguous, and still close (Phase 5).
- If a change would touch an ADR or AC, CLAUDE.md already says **stop and ask** —
  that surfaces in Phase 1, not after.

## The plan file is the running checkpoint

Create `docs/plans/plan_NNN_<slug>.md` (next free NNN; `docs/plans/`, never repo
root) at the **start** of the grill and write decisions into it as they are made.
It is a living draft through Phases 1–3, finalized in Phase 4, and updated in
Phase 5 if implementation diverges. One artifact, always the source of truth.

A decision is not real data ("manual overrides win"; "continuation lines attach
to the transaction above") — so the plan never needs real values. The standing
rule still holds: **obviously-fake placeholders only, never transcribe a real
payee/amount/account number** into it. If you need a real figure to *reason*
during the grill, keep it in chat; it never belongs in the decision record.

## Phase 1 — Extraction (I grill the user)

Interrogate the task to drain context only the user holds. Mostly
product/domain, not technical. The goal is a shared understanding concrete enough
to draft against without guessing.

**Start by stating the session objective in one line** ("a plan complete enough
to implement X without asking further"). If the task is vague, sharpen it with
the user *before* asking anything else.

How to ask:

- **Check the source before every question.** SPEC.md, the code, prior
  `docs/plans/*.md`, and earlier answers this session often already hold the
  answer. If so, state what you found and ask the user only to *confirm* — never
  ask what you could read.
- **One question at a time when branch-walking** — when an answer determines the
  next question, ask singly and walk one branch to its leaves before starting the
  next. Batch (a single `AskUserQuestion`) only for 2–4 *genuinely independent*
  forks.
- **Recommend a default + one-line rationale for every question**, so the user
  confirms ("yes" / "no, because…") instead of writing prose. This is the speed
  knob — without it, sessions bloat.
- **Push back on vague or contradictory answers** before moving on. A fuzzy
  answer that becomes a plan decision is a bug you authored.

Cover at least:

- What does the **real input** look like? (statement layout, quirks, locale
  number format, multi-page, wrapped lines)
- What's the **failure they're most afraid of** here?
- **Scope boundary**: what is explicitly *out*?
- Does this touch an **AC or ADR**? If yes — stop and confirm the change with
  them before proceeding (CLAUDE.md rule).
- Any **real-world edge cases** in the domain? (refunds, reversals, multi-
  currency, a period that spans an FX-rate change, zero/negative balances)
- Surface **hidden assumptions and edge cases the user hasn't raised** —
  dependencies, error handling, scale limits, "what happens when X is null." That
  is your job to raise, not theirs to remember.

Write each settled answer into the plan file as you go. Do not move on until the
answers are concrete — guessing here is the failure mode this phase exists to kill.

## Phase 2 — Self-red-team (I try to break my own draft)

With a **thin** draft in the plan file — decisions and justifications, not prose —
red-team it. For AC-touching / 3+-step work, spawn a **cold-start subagent**
(Explore or general-purpose) so the reviewer is genuinely fresh, not anchored on
my reasoning. Give it the draft + `docs/SPEC.md` + `CLAUDE.md` and this checklist:

- **Money**: any `float` on monetary values? Any locale comma-decimal emitted
  instead of `1234.56`? (ADR: money is `Decimal`, international notation only.)
- **Transactions**: does anything merge, dedup, sum, or collapse distinct lines?
  (Continuation lines reassembling a wrapped description are OK; merging is not.)
- **Currency**: any aggregate mixing currencies without period-end conversion?
- **Schema**: any `CREATE`-only change to an existing table instead of a guarded
  `_migrate` step? Would schema-parity (fresh vs upgraded) still pass?
- **LLM**: is output schema-constrained, persisted, and never recomputed? Any
  computation/summing/conversion asked of the LLM? (ADR-1/2/12.)
- **Categorization authority**: does anything overwrite `manual`? (`manual > rule
  > llm`.)
- **Fail-loud**: does any path catch-and-continue on partial data instead of
  marking the file failed and writing nothing? (FX/enrichment is the only
  sanctioned degrade.)
- **PII**: any real value landing in a committable path?
- Unstated assumptions; the cheapest input that breaks the design.

Fold the valid hits into the plan file. Keep a short list of what you tried, what
you folded on, and what survived — you show this to the user in Phase 3.

## Phase 3 — Grill (the user grills me)

Present, in chat:
1. The **plan** (decisions + justifications) — the user can also read the file.
2. **"Here's what I red-teamed"** — what I tried to break, folded on, what
   survived. This raises the floor so the user attacks deep assumptions, not
   things a first pass should catch.
3. A **code walkthrough of the risky paths** this slice touches — parsers,
   anything on money/`Decimal`, categorization authority, schema migrations — at
   a level the user can follow. Product/plumbing changes need only a light touch.
   The walkthrough is pedagogical by intent: grilling doubles as the user
   building a mental model of the code over time.

Then defend or fold, live, point by point, updating the plan file as decisions
change. Iterate in chat until it holds.

## Phase 4 — Finalize

On sign-off, finalize the plan file (it already exists from Phase 1): tidy it to
the record of what survived — decisions and their justifications, the surviving
edge cases, scope boundary — and read prior `.md` plans there first for
continuity. Then STOP; do not implement until told.

Do **not** list anything CLAUDE.md already mandates as a "decision" (e.g.
fixture/oracle sign-off) — propose the obviously-fake fixture table inline at
implementation time instead.

## Phase 5 — Close (the user grills the result)

Runs *after* implementation, before declaring the slice done. We grill on the way
in; this grills on the way out. Tests-green ≠ matches-intent (see
`docs/DEVELOPMENT.md`), so:

- **Self-review the diff against the finalized plan.** Did I build what survived
  the grill, or did I drift? Run `/code-review` (and `/simplify` if warranted) and
  `/verify` to exercise the real behavior — not just the suite.
- **Meet the CLAUDE.md "done" bar**: relevant AC green, `ruff` clean, `pyright`
  clean, full suite run, `README.md` updated. For any network / real-statement
  path, **run `cruzar process` against the real inbox** — green offline tests do
  not prove the real run works (the FX-fetch lesson).
- **Walk the user through the shipped code** — the risky paths, at a level they
  can follow. A walkthrough of real code teaches far more than one of a plan;
  this is where the user's grip on the codebase actually gets built.
- **User grills the result**: "you said X survived — show me where the code does
  X"; "what about the null case we agreed on?"
- **Divergence → update the plan.** If implementation contradicted a plan
  decision, edit `plan_NNN.md` to match what was actually built and why. A stale
  plan is a lie the next slice reads for continuity (same ethos as "SQLite is the
  source of truth, never hand-edit derived state").
- **Retro (one beat).** Did this cycle teach a durable rule → add it to
  `CLAUDE.md` (the "same mistake twice = a new rule" mechanism), or a fact worth
  keeping → write a memory. If nothing, say so and move on.

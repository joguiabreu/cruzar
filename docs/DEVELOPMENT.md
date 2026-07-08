# Development approach — Cruzar

This is *why* we work the way we do. `CLAUDE.md` holds the repo rules; the
`grill` skill holds the step-by-step. This doc is the reasoning behind both, so a
future reader (human or agent) understands the intent, not just the mechanics.

## The premise: green tests are necessary, not sufficient

Cruzar handles real money and real financial data. Two things follow:

- A silent bug is expensive and easy to miss — "everything works" usually means
  "nothing has gone visibly wrong *yet*," which is not the same as *correct*.
- The offline synthetic test suite cannot exercise live-network or real-statement
  paths. It proves the shape is right; it cannot prove the real run works. (The
  FX-fetch crash — green suite, first real fetch died — is the standing lesson.)

So correctness rests on more than the suite: an executable acceptance gate
(AC1–AC23), a human oracle, and a planning process that kills wrong assumptions
*before* code exists.

## Roles: agent drives, human is the oracle

The agent (Claude) does the mechanical work — drafting, red-teaming, coding,
running the suite — and proposes. The human supplies judgment, real-world context
the agent can't see, and sign-off. This is why the human authors/verifies fixture
oracle values and runs the real pipeline before "done": those are oracle duties
that cannot be delegated to the thing being checked.

A weak spot in this model: if the human can't read the code, the oracle role
thins out — approving numbers without being able to check the logic that made
them. The grilling loop's code walkthroughs exist to close that gap over time.

## Spec-driven, AC-gated, vertical slices

- `docs/SPEC.md` is the source of truth for **what** we build. On any behavior
  conflict, SPEC wins.
- Each AC in SPEC has one acceptance test. Work isn't done until the relevant AC
  is green — the AC, not a proxy for it.
- We build in **vertical slices**: each slice ends with its acceptance test
  passing, `ruff` clean, `pyright` clean, full suite run, README updated.

## The grilling loop (replaces plan-first HTML)

We used to write an HTML plan, recommend, and let the user annotate and decide —
one-directional, and the plan arrived already fully formed (easy to nod at, hard
to tear apart). We replaced it with a live, bidirectional grill spanning the whole
cycle, entrance to exit. Five phases (see the `grill` skill for the procedure):

1. **Extraction** — the agent grills the user for product/domain context before
   drafting. Kills the failure mode of planning on guessed assumptions.
2. **Self-red-team** — the agent drafts a thin plan and a cold-start subagent
   tries to break it against the ADRs/ACs. Raises the floor before the user
   spends attention.
3. **Grill** — the user attacks the survivor live, seeing what the red-team
   already caught, plus a walkthrough of the risky code paths. Grilling doubles
   as the user learning the codebase.
4. **Finalize** — on sign-off, the plan file is tidied to the record of what
   survived. Implementation follows.
5. **Close** — after implementation, the user grills the *result* against the
   plan: self-review + `/code-review` + `/verify`, a walkthrough of the shipped
   code, divergences written back into the plan, and a one-beat retro. This is the
   exit ritual symmetric to the entrance — because tests-green ≠ matches-intent.

The plan file is not written at the end — it is created at the *start* and
accumulates decisions as they are made, so it is both the running checkpoint
(surviving context loss across sessions) and the final record. A decision is not
real data, so the plan holds no real values — only obviously-fake placeholders,
as every committable file must.

### Why a grill instead of a review

A design that survives interrogation is load-bearing; one that only survives
polite review is untested. The interrogation surfaces assumptions the author
didn't know they were making — and it does so at the cheapest possible time,
before any code exists.

### Tiered code understanding

Full audit is wasteful; full trust is risky. The pragmatic middle:

- **Understand deeply** the high-blast-radius surfaces — parsers, anything on
  money/`Decimal`, categorization authority (`manual > rule > llm`), schema
  migrations. A bug there costs real money or leaks real data.
- **Trust-but-spot-check** the plumbing (reports, CLI wiring), where the suite
  carries more of the weight.

The Phase 3 walkthrough is the vehicle for building that understanding
incrementally, without a code-reading bootcamp.

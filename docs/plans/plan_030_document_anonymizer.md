# Plan 030 — Document anonymizer (privacy-safe parser-dev sample)

**Goal.** Turn a real statement into a structurally-identical copy with fake values, so a
parser can be developed against it without exposing real data. Runs entirely on the *local*
model; only the anonymized output is ever allowed to leave the machine. This is slice 1 of
[plan 029](plan_029_self_generating_parser.md) and is independently useful (a safe way to
share a statement's *shape*).

## Core design — model classifies, Python generates

The local model does **not** freely rewrite the document (structure would silently drift). It
also does **not** invent replacement strings (then "a comma is a comma" would depend on model
compliance). Instead:

- **The model classifies** each distinct token: `keep` (a structural label the parser keys on —
  `Data`, `Saldo`, headers, column titles) vs `replace` (a real value/PII token), plus a
  **semantic type** for `replace` tokens (`amount` | `date` | `text` | `id`).
- **Python generates** the shape-preserving fake and applies it deterministically. This is the
  same "model proposes structured output → Python applies" shape cruzar already uses in
  `categorize`, `extract`, and `ask`.

### Deterministic force-replace is the safety backbone

A weak local model cannot be trusted to individually catch every figure in a dense document (a
real receipt run classified ~150 amounts and the small model missed most). So Python runs a
**deterministic value pass first**: any token that *looks like a value* (amount, date, time,
NIF/IBAN, masked card, long id run) is force-classified `replace` — overriding the model — using
the same detector the safety gate uses. That makes value scrubbing a Python guarantee, not
model compliance. The model's remaining job is the **non-value PII the regex can't see**
(names, addresses, free text). Consequence, stated honestly: a leak of a *value-shaped* token is
prevented deterministically; a leak of a *name-shaped* token still depends on the model +
human review (the safety gate cannot detect it). Over-scrubbing structural labels is possible
when a weak model mis-tags them `replace` — safe, but it costs the sample some fidelity, and it
improves with a stronger classification model.

Shape preservation is a Python guarantee, per semantic type:

| type | generation | preserves |
|------|-----------|-----------|
| `amount` | digit→random digit, separators/currency untouched | length, `.`/`,` positions, grouping, symbol |
| `date`   | random **valid** date in the same detected format | format (`DD/MM/YYYY`, PT month names, …) |
| `text`   | letter→random letter (case kept), punctuation untouched | length, spacing, casing |
| `id`     | alphanumerics randomized, structure untouched | length, group boundaries |

Extra invariants Python enforces:

- **Stable map:** the same real token → the same fake token throughout (a recurring payee stays
  recurring).
- Balances need **not** reconcile — the parser never does arithmetic and transactions are
  independent; internally-consistent running balances are explicitly not required.

### Two error directions, two gates — by construction

A wrong classification is the only way this fails, and each direction is caught by exactly one gate:

- *value mis-tagged as `keep`* → a real value survives → the **Safety gate** catches it.
- *label mis-tagged as `replace`* → a structural token is corrupted → the **Fidelity gate** catches it.

## The two gates

### Safety gate — deterministic, hard, no LLM

- After applying the map, scan the output for any surviving *source* token (the map's
  replace-side is the exact denylist of real values that must not appear).
- Also scan against the existing `.pii-denylist` — both as a whole phrase (space-insensitive,
  mirroring `.githooks/check_pii.py`) **and** at the word level, so a full-name term also guards
  its individual name tokens (how a name really appears — scattered, not contiguous).
- Any survivor → **abort, emit nothing** (fail loud). Never "best effort, continue." The
  deterministic guard — not an LLM's judgment — is the durable privacy defense.

### Names: value-shaped vs. word-shaped (the account holder)

A value (amount/date/NIF/card/id) is deterministically detectable and always force-replaced. A
**personal name is just a word** — not shape-detectable — so it would otherwise rely on the model,
which can miss it. So the account holder's own identity (their name, address, account numbers,
listed once in the gitignored `.pii-denylist`) is **force-replaced deterministically** at the word
level, and guarded by the safety gate. Only *third-party* names (a one-off counterparty) still
depend on the model plus human review of the sample. An empty `.pii-denylist` is warned about at
run time.

### Fidelity gate — deterministic checks + LLM comparator

- Deterministic: line/row count matches; separator histogram matches (comma/dot counts);
  date-format regex still matches; column x-positions within tolerance (from `pdfplumber` word
  boxes).
- LLM comparator (optional): "does the anonymized output read like a real statement of this
  shape?" — naturalness only, **not** a privacy authority.
- On failure → structured diffs fed back to the generator → **bounded retries** (this is the
  review-loop, made objective).

## Where it lands, and the approval seam

Plan 030 is **purely local**: it produces an artifact and validates it. It sends nothing
anywhere — dispatching to Claude is plan 029's job. The seam between the two is an explicit
human approval.

- **Output location.** The anonymized layout bundle + a gate report are written to gitignored
  `data/parsergen/<institution>/` (e.g. `sample.layout.json` + `gate_report.txt`). **Never
  committed** (see "artifacts" below).
- **What the safety gate guarantees vs what you approve.** The deterministic safety gate is the
  *guarantee* that no real token survived — machine-enforced, not eyeballed. Your approval is a
  different thing: *consent to send* derived financial data off-machine. That consent is a policy
  decision (a posture change for a local-first app), so it's explicit and per-run, not implied by
  the gates passing.
- **The confirm is lightweight.** The tool prints a summary ("gates passed, N tokens replaced,
  line count / separators / geometry preserved, bundle at `<path>`") and stops. Only on an
  explicit yes does plan 029 pick the bundle up. Same "propose → you confirm → proceed" shape as
  plan 028's categorize.

**Flow:** anonymize (local) → gates pass → written to `/data/` → you approve the send → 029
dispatches. It is *not* immediately put to implementation.

## Decisions (settled)

- **D1 — anonymize the pdfplumber layer; emit a layout bundle.** A parser consumes only what
  `pdfplumber` extracts (words + positions), so that is what must be faithful — not the PDF's
  pixels. Output is an **anonymized layout bundle** (text + per-word coordinates + page dims).
  Re-rendering a fresh synthetic PDF from that bundle (reportlab, like the fixture generators) so
  a parser can actually run `parse(pdf_path)` is a **follow-up within the slice**, not the first
  increment. Regenerating a pixel-perfect PDF directly from an LLM is off the table.
- **D2 — anonymized sample is a gitignored dev aid, never the committed fixture.** Two different
  artifacts: (a) the *anonymized sample* — realistic-but-fake, lets Claude see the format, stays
  in gitignored `/data/`; (b) the *committed fixture* — obviously-fake round/sequential numbers,
  hand-authored, my signed oracle (per CLAUDE.md), authored fresh during the parser slice. The
  anonymizer produces (a) only; (a) is **never committed**.
- **D3 — bounded retries; safety failure is terminal.** Fidelity retries up to a small N (2–3),
  then give up with a clear report. A safety-gate failure is *terminal for that document* — never
  retried into a "good enough" pass.

## Placement / non-negotiables

- Lives in a dedicated subpackage `src/cruzar/parsergen/`, imported only by its own CLI
  subcommand and tests. **Never imported by `pipeline.process`** — the offline suite stays
  offline and a normal run gains no local-model dependency.
- Operator command `cruzar anonymize <pdf> -o <out>`, run on demand only.
- Real values never touch committable parts of the repo: the anonymized sample is gitignored; the
  safety gate + fail-loud is the guard.

## Chicken-and-egg (why classification is heuristic)

There is no parser yet, so nothing knows which tokens are values vs structure — that's precisely
why we lean on the local model to classify and on the two gates to catch its mistakes. The "more
powerful, not time-restricted" local model is the right tradeoff: correctness over speed.

## Testing

- Input fixture is a **synthetic, obviously-fake** statement of a fictional bank (per
  conventions) — we anonymize fake data in tests, so no real value is ever involved.
- Deterministic parts (map application, shape/separator/length/date preservation, safety scan,
  fidelity checks) are unit-tested without a model.
- The LLM classifier/comparator is behind the enabled flag and **mocked** in the suite (offline
  stays offline).
- Assertions: structure preserved (row count, separators, date format, geometry) **and** no
  source-side token survives in the output.
- **Not "done" until run for real.** Per CLAUDE.md, offline green ≠ it runs: the slice isn't done
  until `cruzar anonymize` has been run against a real statement with the local model and the
  gates pass. I (the user) run that before it's called done.

## Slice increment order

1. Deterministic core + seam: `bundle.py` (extraction), `anonymize.py` (types, shape-preserving
   generation, apply, Classifier Protocol), `gates.py` (both gates). Offline-tested with a mocked
   classifier.
2. CLI `cruzar anonymize` + concrete Ollama classifier in `llm.py` (behind `llm.enabled`).
3. (Follow-up) PDF re-render from the bundle so a parser can run against it; LLM fidelity
   comparator.

## Risks / posture

- Imperfect scrubbing leaks real data into a committable PR — the cardinal sin. Mitigated by the
  deterministic safety gate + fail-loud, and by never committing the anonymized sample (D2).
- Over-aggressive replacement corrupts the format → parser built against a phantom layout.
  Mitigated by the fidelity gate and by keeping structural labels as `keep`.
- Sending derived financial data off-machine is a posture change — gated by explicit opt-in
  (plan 029 D3); the local model does all scrubbing before anything leaves.

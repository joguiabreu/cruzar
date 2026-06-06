# Cruzar — Slice 2 Plan (Transfer detection — ADR-15, AC21)

## Context

Slice 1 (manual ingest → Spending Detail) is green and shipped
(`docs/plans/plan_001_manual_ingest.md`). It deliberately left `is_transfer`
always `false` (plan_001 decision 6), so every `TRF …` debit currently leaks
into Spending Detail. This slice implements **ADR-15 steps 1+2** — description
rules + account-pair matching — to set `is_transfer` correctly. The report's
Spending Detail query already filters `is_transfer = 0` (`report.py:39`), so the
visible effect lands the moment detection runs; no report changes this slice.

Scope chosen over Summary/FX/LLM/Gmail because it is dependency-free (no network,
no LLM, no new deps), fixes a known live defect, and unblocks correct Earned/Spent
and Portfolio Δ NetContrib in later slices.

## Slice scope (in) vs deferred (out)

### In slice 2

- `config/flows.yaml` with `transfer_patterns` (committed; placeholder values).
- `config.py`: load `flows.yaml` → `Config.transfer_patterns: list[str]`.
- New `src/cruzar/transfers.py`: `detect(conn, transfer_patterns)` — the
  normalize stage (ADR-4). Sets `transactions.is_transfer` deterministically.
- Wire `transfers.detect` into `pipeline.process`, **before** `categorize`.
- AC21 test (detection + Spending-Detail exclusion portion — see "AC21 split").
- Unit test pinning the salary-credit trap and idempotency.

### Deferred (later slices — explicitly not now)

- **Earned/Spent exclusion assertion of AC21** — blocked on the Summary section
  (Earned/Spent don't exist yet). See "AC21 split" — this is a real dependency,
  not a dodge.
- ADR-15 **step 3** (Needs-Transfer-Review appendix) — spec-deferred to v1.1.
- `investment_flow_patterns` (the other list spec'd for `flows.yaml`) — belongs
  to the Portfolio Δ / ADR-14 slice. I will add the `transfer_patterns` key only
  and leave a commented placeholder for `investment_flow_patterns` so the file
  shape matches the spec without consuming it.
- Cross-currency transfer pairs (see decision D3).

#### Future analysis — distinguishing a transfer from a payment

Per D1, v1 treats every transfer-like line as a transfer, even a payment to a
person. A later slice should separate true inter-account **transfers** (money
moving between your own accounts, net-zero to net worth) from **payments**
(money leaving to a third party, real spending). Candidate signals to explore:

- **Counterparty ownership:** is the destination one of *your own* tracked
  accounts? Strongest signal, but needs an account-identity/IBAN registry.
  (User hint: `TRF P/ Moey` literally names the user's own Moey account in the
  description — a named-own-account token is a cheap first cut at ownership,
  short of a full IBAN registry.)
- **Round-trip pairing:** an opposite leg of equal magnitude lands on another
  tracked account within a few days (already ADR-15 step 2) ⇒ transfer; no
  matching leg ⇒ likely a payment.
- **Rail / channel:** MB WAY / person-name payees lean *payment*; SEPA/standing
  orders to a saved own-account lean *transfer*.
- **Recurrence & memo:** repeated same-payee, same-amount to a non-owned account
  is more payment-like (rent, allowance) than transfer-like.

Marked here so the simplification stays visible and revisitable, not silently
accepted.

## Design decisions

1. **No table for transfer patterns — consume `flows.yaml` directly.** The spec
   data model defines a `merchant_patterns` table but **no** flow/transfer-pattern
   table. So `flows.yaml` is read at normalize-time (a rules engine over config);
   the *derived* result (`transactions.is_transfer`) is the persisted source of
   truth (ADR-3). This avoids adding a non-spec table (CLAUDE: flag before
   touching schema — flagging it here; the decision is to NOT add one).

2. **`is_transfer` is fully recomputed each run, deterministically.** `detect`
   computes the target set and writes `is_transfer = 1` for members, `0` for all
   others. There is no `manual` override for `is_transfer` in the spec (unlike
   `merchant_source`), so full recompute is correct and keeps AC1 (idempotency)
   holding — same inputs → same flags. Mirrors `categorize.py`'s re-evaluation
   model. No LLM involved, so ADR-12 is untouched.

3. **Pipeline order:** `ingest → transfers.detect → categorize → report`
   (ADR-4 normalize precedes categorize). `is_transfer` and `merchant_*` are
   independent; a transfer can still carry a merchant match or `none`.

## Algorithm

`detect(conn, transfer_patterns)`:

1. Load all transactions joined to their account: `(id, account_id, currency,
   date, amount, description_raw)`. Amounts → `Decimal` (native, signed).
2. **Step 1 — description rules.** Compile `transfer_patterns` (case-insensitive,
   like merchant patterns). Any transaction whose `description_raw` matches →
   add to the transfer set.
3. **Step 2 — account-pair matching.** Deterministic greedy pass over
   transactions sorted by `(date, id)`. For each not-yet-paired transaction, find
   a not-yet-paired counterpart where: different `account_id`, **same currency**
   (D3), opposite sign, **equal |amount|** (compare `abs(Decimal)`), and
   `|date_a − date_b| ≤ 3` calendar days. Choose deterministically: smallest day
   gap, tie-break lowest `id`. On a match, add **both** legs to the transfer set
   and remove both from the pool (symmetric, one-to-one).
4. **Write:** single `UPDATE transactions SET is_transfer = ?` per row — `1` if
   in the transfer set, else `0`. Commit.

Logging (per the levels added in slice 1.5): one INFO summary line, e.g.
`marked N transfer(s) (M by rule, K by pairing)` — counts only, never
descriptions/amounts.

## DECISIONS (all resolved)

- **D1 — What counts as a transfer? → RESOLVED.** Keep it simple — **any
  transfer-like description is a transfer**, including a payment to a person
  (e.g. `TRF MB WAY P/ …`, `Trf imediata …`). The transfer-vs-payment nuance is
  deferred (see "Future analysis").
  - **Salary carve-out (confirmed):** `TRANSFERENCIA - VENCIMENTO` is **income**,
    not a transfer. Implemented by **pattern specificity** — seed only specific
    transfer prefixes, never a bare `TRANSFER` catch-all — so the salary line is
    never matched and Earned (later slice, AC19) isn't broken. `detect()` stays a
    pure "match-any → transfer"; the exclusion lives in how patterns are authored.
    `flows.yaml` carries a comment warning against broad patterns for this reason.
  - Patterns are generic keywords, committed in `flows.yaml`; never a real
    counterparty name (CLAUDE PII invariant).

- **D2 — AC21 split. → RESOLVED (test now, extend later).** AC21 wants a pair
  (a) flagged on both legs AND (b) excluded from Earned/Spent; (b) needs the
  not-yet-built Summary section. This slice's `test_ac21_*` asserts (a) both legs
  `is_transfer = 1` and the legs are absent from **Spending Detail** (the live
  consumer). The Earned/Spent clause is added to the same test when Summary lands,
  carried as an explicit pending comment so it's never silently dropped.

- **D3 — Cross-currency pairs. → RESOLVED (EUR-only).** Step 2 requires same
  currency (a EUR −100 and USD +100 do not pair). Recorded as a v1 known
  limitation in `docs/SPEC.md` (consistent with single-base EUR).

## Findings from the added Moey statement (data/inbox/May.pdf)

A second institution's statement (Moey / Crédito Agrícola) was dropped in. It
does **not** change this slice's logic, but it sharpens the seed patterns and
surfaces prerequisites for a *later* slice:

- **Two transfer vocabularies to seed.** Moey uses `Trf imediata <person>` and
  `TRANSF SEPA -<name>`; ActivoBank uses `TRF P/` and `TRF MB WAY`. Seed
  `transfer_patterns` covers both. None of these match `…VENCIMENTO` (salary), so
  the carve-out holds by specificity.
- **Real step-2 pairs now exist.** ActivoBank `TRF P/ Moey` (outbound) and Moey
  `TRANSF SEPA -<self>` (inbound, account holder's own name) are the two legs of
  genuine own-account transfers. Validates ADR-15 step 2 against real data once
  both accounts are ingested (tests still use synthetic fixtures per convention).
- **Out of scope here — flagged, not fixed:**
  - Moey needs its **own parser** (`parsers/moey.py`) + synthetic fixture
    (ADR-11, AC7/AC8): single signed `MOVIMENTOS (+/-)` column, two date columns,
    and **multi-line wrapped descriptions**. That is the natural **next slice**;
    `May.pdf` cannot be ingested until it exists.
  - `May.pdf` is at the inbox **root** (no `<account_match>/` folder) → would be
    `unresolved_account`. The existing ActivoBank file is now under
    `data/inbox/moey/`, which looks **misfiled**. Both need correct placement and
    a `moey` entry in `sources.yaml` before ingestion. Needs user confirmation.

## Proposed `config/flows.yaml` seed (for sign-off)

Generic keywords only — no counterparty names. Specific prefixes, no bare
`TRANSFER`, so the salary line (`TRANSFERENCIA - VENCIMENTO`) is never matched.

```yaml
# Flow rules (SPEC §Inputs). transfer_patterns drive is_transfer step 1 (ADR-15).
# Matched case-insensitively against transactions.description_raw.
# KEEP PATTERNS SPECIFIC: never a bare "TRANSFER"/"TRANSFEREN" — it would swallow
# "TRANSFERENCIA - VENCIMENTO" (salary/income) and drop it from Earned (AC19).
transfer_patterns:
  - "TRF P/"            # ActivoBank: outbound transfer
  - "TRF MB WAY"        # ActivoBank: MB WAY payment to a person (transfer for now, per D1)
  - "Trf imediata"      # Moey: instant transfer to a person
  - "TRANSF SEPA"       # Moey: SEPA transfer (own-account inbound leg, etc.)

# investment_flow_patterns: []   # ADR-14 / Portfolio Δ slice — not consumed yet
```

## Test plan (slice gate)

- **`tests/acceptance/test_ac21_transfer_pair_excluded.py`** — build a fresh temp
  DB with two tracked accounts (checking + savings), each with a statement, and
  synthetic transactions: a pair (checking −100.00 on 2026-05-10, savings +100.00
  on 2026-05-11), a non-transfer purchase (−50.00), and an income credit
  `TRANSFERENCIA - VENCIMENTO`-style (+1000.00). Run `transfers.detect`. Assert:
  both pair legs `is_transfer = 1`; purchase and income `is_transfer = 0`; the
  pair legs do not appear in Spending Detail rows; income is not flagged.
  Synthetic, obviously-fake values only (no parser/PDF needed — detection is
  DB-level logic, distinct from AC8 parser fixtures).
- **`tests/test_transfers.py`** (unit) — (i) step-1 pattern marks an outbound
  `TRF P/ …` while leaving `TRANSFERENCIA - VENCIMENTO` unmarked (the D1 trap);
  (ii) `detect` run twice yields identical `is_transfer` state (idempotency,
  guards AC1).
- Full suite (AC1/3/8/12 + canonical-amount) stays green. Note: once you set real
  `transfer_patterns`, the *real* report's `TRF` rows will drop from Spending
  Detail — intended; no existing test asserts report content, so none breaks.

## Files touched

```text
config/flows.yaml                              # NEW (committed, placeholder transfer_patterns)
src/cruzar/config.py                           # load flows.yaml -> Config.transfer_patterns
src/cruzar/transfers.py                        # NEW — detect(conn, transfer_patterns) [normalize stage]
src/cruzar/pipeline.py                         # call transfers.detect before categorize; load patterns
tests/acceptance/test_ac21_transfer_pair_excluded.py   # NEW
tests/test_transfers.py                        # NEW (unit: D1 trap + idempotency)
```

No `schema.sql` change (`is_transfer` column already exists). No `report.py`
change (already filters `is_transfer = 0`).

## Verification / done

- `uv run pytest tests/acceptance` → AC21 (detection portion per D2) green
  alongside AC1/3/8/12.
- `uv run ruff check . && uv run pyright && uv run pytest` all clean.
- Manual smoke: `uv run cruzar process` logs a transfer-count summary; the real
  Spending Detail no longer lists descriptions matching `transfer_patterns`.
- **"Done"** = the detection portion of AC21 + the unit tests pass, ruff/pyright/
  full-suite clean. The Earned/Spent clause of AC21 is explicitly carried to the
  Summary slice (D2), not claimed complete here.

# Plan 020 — Categorization prompt: validate the held change (don't lose it)

**Status: a change is already written but UNCOMMITTED** in `src/cruzar/llm.py`
(`_SYSTEM`). It was deliberately held back to validate with the eval harness before
committing — this plan is the reminder + the validation procedure so it isn't
forgotten or shipped on vibes.

## What the held change does (3 targeted edits to `_SYSTEM`)

1. **Anti-catch-all.** "A fees/charges category is ONLY for bank/card fees, interest,
   FX charges; a taxes category is ONLY for government taxes — never a shop/restaurant/
   market/transport. If unsure, use the most general option (e.g. 'Other')." → attacks
   the two junk drawers (Fees & Charges, Taxes) the merchants-by-category review exposed.
2. **Confidence = category certainty, not just merchant recognizability.** The bug was
   "McDonald's" recognized → high confidence → confident *mis-file*. Now a guessed
   category → low confidence → it lands in Needs-Categorization for a rule, not a wrong
   label.
3. **Anti-hallucination.** "Use ONLY words that appear in the description; never invent,
   translate, or append." → targets the `StatefulWidget` / glued-Arabic extraction
   glitches.

If the working-tree change is ever lost, these three are enough to recreate it.

## Why it can't just be committed and forgotten

- **Unvalidated.** The offline suite uses fake LLMs, so it proves wiring, not labeling
  quality. "Looks better" is anecdote (the McDonald's miss taught us not to trust that).
- **ADR-12 cache.** Committing + re-running `process` changes nothing for existing data
  — proposals are cached and never recomputed. The new prompt only affects *new* lines
  or a forced re-categorization. So validation goes through the eval harness (which
  calls the model fresh), and re-applying to real data is a separate, explicit step.

## Decisions (signed off)

- **D1 — A/B old vs new prompt via `git stash`.** The eval harness runs whatever prompt
  is in the working tree. Run it once on the **new** prompt (current tree), `git stash`
  (reverts `llm.py` to the old prompt), run again, `git stash pop` — two accuracy numbers
  from the *same* labeled set, no code plumbing. Preferred over adding a prompt-override
  parameter just for this.
- **D2 — the metric.** Two numbers on the labeled set: (a) **overall accuracy**
  (predicted == expected), and (b) **junk-drawer false-positives** — count of
  non-fee/non-tax merchants the model filed under Fees & Charges or Taxes. Ship the new
  prompt iff (b) drops materially AND (a) is not worse. *Implemented:* the harness now
  prints the junk-drawer FP count + list alongside accuracy.
- **D3 — re-applying to existing data (separate, explicit, opt-in).** After validation +
  commit, existing categorizations are still the OLD ones (ADR-12 cache). To re-label
  with the new prompt, clear the LLM cache and reset the `llm` tier, then re-run
  (~500 live calls — opt-in, on a calm machine):

  ```sql
  DELETE FROM llm_categorizations;
  UPDATE transactions SET merchant_id=NULL, merchant_source='none' WHERE merchant_source='llm';
  -- then: uv run cruzar process   (rules + manual labels are untouched)
  ```

  A deliberate follow-up you run, not part of the commit. (A `cruzar process --reextract`
  CLI, ADR-12, would make this a flag instead of raw SQL — a separate, optional slice.)

## Prerequisite you own: the labeled set

The decision rides on the eval set, so it must be **adversarial**, not easy
(`STARBUCKS → Coffee` ranks everything tied). Put a few dozen rows in the gitignored
`data/eval/categorization.csv` (`description,expected_category`) weighted to the hard
cases the review surfaced: foreign/abbreviated merchants, truncated descriptors,
ambiguous strings, and — crucially — the ones that landed in Fees & Charges / Taxes
wrongly (McDonald's, Carrefour, Polish markets, …) so the junk-drawer metric (D2-b) has
signal.

## Steps

1. You author `data/eval/categorization.csv` (adversarial, incl. junk-drawer cases).
2. Run the eval on the new prompt; `git stash` → run on old prompt → `git stash pop` (D1).
3. Compare accuracy + junk-drawer FPs (D2). If better → commit `llm.py`; if not → revise
   the prompt and repeat, or drop it.
4. Optional, opt-in: re-apply to existing data (D3).

## Definition of done

- The held `llm.py` prompt change is either **committed** (validated: junk-drawer FPs
  down, accuracy not worse) or revised/dropped on the eval data — not left dangling.
- `uv run ruff check . && uv run pyright && uv run pytest` clean (the offline suite is
  unaffected — it uses fakes).
- No real-run gate beyond the eval itself; re-applying to real data (D3) is your call.

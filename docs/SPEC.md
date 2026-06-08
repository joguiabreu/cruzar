# Cruzar — Spec

Status: v1 (final for build)
Last updated: 2026-05-31
Owner: Guilherme Abreu

> Rev 7 changelog (vs rev 6) — final pass, no open TODOs:
>
> - is_transfer cascade CONFIRMED at steps 1+2 (ADR-15).
> - Portfolio Δ CHANGED to option (c): total return net of contributions
>   (ADR-14). This required (a) tracking total investment value = securities
>   - uninvested cash (uses the new statements.closing_balance), and
>     (b) external-contribution detection. Knock-on: Earned/Spent are now
>     defined over CASH accounts only, since investment trades/dividends are
>     not spending/income (see Account classes).
> - Net Worth source RESOLVED (option a): added statements.closing_balance.
> - Local LLM CONFIRMED Qwen 3 8B (Ollama); model is config-driven and
>   swappable, with a `--reextract` path. Validation on real fixtures is a
>   recommended pre-reliance check, not an open decision.
> - FX source CONFIRMED: exchangerate.host /timeseries, ECB fallback.
> - "Open questions / TODOs" section removed. Accepted approximations are now
>   recorded under "Known limitations" so they stay visible without being open.

---

## Problem

Cruzar ("to cross") aggregates financial data from multiple banking and investment accounts into a single local view. Today this data lives scattered across institution emails and statement PDFs — checking net worth requires logging into N apps, and tracking spending requires manual spreadsheet work. Cruzar consolidates it locally (privacy-preserving) into a unified report showing net worth, spending patterns, and portfolio performance over time.

## Account classes

Two classes, used throughout:

- **Cash accounts:** account_type ∈ {checking, savings}. Source of Earned/Spent.
- **Investment accounts:** account_type ∈ {brokerage, retirement}. Source of
  Portfolio Δ. Their transactions (trades, contributions, in-account
  dividends) are NOT counted in Earned/Spent.
  Both classes contribute to Net Worth.

## Non-goals

<Bulleted list. Explicit things this will NOT do. Be generous here —
non-goals are the most under-used section of a spec and the one that
saves you the most time later.>

- Not fetching the information directly from the banking or broker account
- Not providing an online service. For now, this software is only meant to run locally
- Not handling cash/Venmo/PayPal/crypto in v1
- Not building a UI (markdown reports only)
- Not real-time / push-based updates
- Not multi-user
- Not making investment recommendations or advice
- Not reconciling conflicting versions of the same transaction (first write wins — see "Transaction identity")
- Not OCR / image-based PDFs (text-extractable only)
- Not tax-lot accounting or realized-gain computation (cost basis is broker-reported aggregate only)
- Not per-transaction-date FX conversion (period-end only — ADR-5)
- Not true multi-currency. v1 is single-base (EUR); a foreign-currency account is supported via period-end conversion for the base rollup only
- Not a time-weighted or money-weighted % return. Portfolio Δ is an absolute base-currency figure (ADR-14)

## Inputs

<What goes in. Sources, formats, frequency.>

- Data sources: text-based PDFs (image-based PDFs, XML, HTML deferred to v2+)
- Ingestion paths:
  - Email: Gmail fetcher polls inbox, applies sender + subject allowlist
    from sources.yaml, downloads attachments to /data/inbox/
  - Manual: user drops PDFs into /data/inbox/ directly
    Both converge — files in /data/inbox/ are treated identically EXCEPT
    for account resolution (below), which differs by path because statements
    carry no in-document account identifier.
- Frequency / trigger:
  - Manual: pipeline runs on demand (`cruzar process`)
  - Email fetch: triggered by user (`cruzar fetch`) or scheduled via launchd/cron
  - No daemon, no background service in v1
- Source allowlist: /config/sources.yaml. One entry per **account**,
  specifying institution, account_match (binding signal; see "Account
  resolution"), name, source_type (email|manual), sender_match (email only),
  subject_match (email only), account_type, currency. New account = new yaml
  entry; no code change unless the format requires a new parser.
- Categorization config: /config/merchants.yaml — human-editable, seeded into
  the `merchants` / `merchant_patterns` tables each run. SQLite remains source
  of truth (ADR-3).
- Category vocabulary: /config/categories.yaml seeds the `categories` table.
  Starter set: Income, Transfer, Groceries, Dining, Transport, Fuel,
  Utilities, Rent/Mortgage, Insurance, Health, Shopping, Entertainment,
  Subscriptions, Travel, Fees & Charges, Taxes, Education, Other.
- Flow config: /config/flows.yaml — two pattern lists:
  - transfer_patterns: description rules for is_transfer step 1 (ADR-15).
  - investment_flow_patterns: description rules identifying external
    contributions/withdrawals on investment accounts (e.g. employer
    contribution, external ACH deposit), used by ADR-14.
- App config: /config/cruzar.yaml — base_currency (hardcoded EUR), llm_model
  (Ollama model string, default `qwen3:8b`), fx provider settings.
- Local LLM: Qwen 3 8B via Ollama. Config-driven and swappable; persisted
  extractions can be cleared and rerun with `cruzar process --reextract`
  (ADR-12). Recommended: validate extraction quality on real redacted
  fixtures before relying on it.

### Account resolution

How a parsed statement maps to exactly one `accounts.id`. Required for AC12.

**Statements carry no stable account identifier.** Resolution binds by
ingestion path, not by reading the statement body:

- **Email path:** bound to the account whose `sources.yaml` entry matched its
  sender + subject. `sender_match` + `subject_match` must identify exactly
  one account.
- **Manual path:** bound by a required drop convention — file under
  `/data/inbox/<account_match>/` (or a filename token). `account_match` here
  is the folder/token.
- Accounts are **declared, not auto-created.** A statement resolving to no
  entry is logged loudly and marked `unresolved_account`; never ingested
  against a guess. Keeps AC12 enforceable.
- **Known break:** a single email/PDF containing two accounts' statements
  cannot be split (multi-account splitting is v2).

## Outputs

<What comes out. Files, schemas, where they land.>

Report file: /reports/cruzar-YYYY-MM.md (one per calendar month)

A report for month M is computed **as of M's period boundaries**, not "today."
Regenerating an old month reproduces that month's view (FX uses the persisted
period-end rate per ADR-5).

**Metric definitions (Summary row for month M):**

- **Earned:** SUM(amount) over transactions in M on **cash accounts** where
  amount > 0 AND is_transfer = false. Includes salary, interest, refunds, and
  dividends credited to a cash account.
- **Spent:** SUM(amount) over transactions in M on **cash accounts** where
  amount < 0 AND is_transfer = false. (Negative.)
- **Portfolio Δ:** total return net of contributions, over investment
  accounts — see ADR-14. Shows "—" when no prior snapshot exists.
- **Net Worth:** base-currency total, at M's month-end, over all non-closed
  accounts, of (cash closing_balance) + (Σ holdings value). See ADR-16.

**Display currency per section:**

- Section 1 (Summary): **base currency (EUR).**
- Section 2 (Spending Detail): **native currency**, with a Currency column
  (raw transactions.amount; makes AC2 reconciliation exact).
- Section 3 (Earning Detail): **native currency**, with a Currency column.
- Section 4 (Investment Detail): per-account subsections in **native
  currency**; Grand Total in **base currency**.

Section 1: Summary
Columns: Month | Earned | Spent | Portfolio Δ | Net Worth
One row per month, descending. Up to last 12 months of available data; fewer
if less history. No padding rows.

Section 2: Spending Detail (this month)
Columns: Date | Amount | Currency | Merchant | Category
Transactions on cash accounts where amount < 0 AND is_transfer = false, this
month only. Sorted by date descending.

Section 3: Earning Detail (this month)
Columns: Date | Amount | Currency | Source
Transactions on cash accounts where amount > 0 AND is_transfer = false, this
month only (the itemised counterpart of the Summary's Earned). Source = matched
merchant name, else raw description. Native currency. Sorted by date descending.

Section 4: Investment Detail (snapshot as of this month-end)
Per-account subsections:
Columns: Symbol | Quantity | Currency | Cost Basis | Current Value | Δ Amount | Δ %
Latest `holdings_snapshot` with `snapshot_date <= month-end`. Holdings render in
native currency (a Currency column — one account may hold multiple currencies, e.g.
USD + EUR at IBKR). Δ Amount = Current Value − Cost Basis; Δ % = Δ ÷ Cost Basis;
both `n/a` when cost_basis is null. Per-account total row and the final "Grand
Total" subsection are in base currency (converted at the month-end rate).

Section 5 (conditional): Needs Categorization
Shown iff un-categorized merchants exist.
Columns: Raw Description | LLM-Proposed Merchant | LLM-Proposed Category

## Data model

<Tables / entities, fields, PKs, relationships. Mark immutable fields.>

### accounts

- id (PK, immutable)
- institution
- name
- account_match (binding signal: matched-entry on email path, folder/filename
  token on manual path — not an in-document string)
- source_type (email | manual)
- account_type (checking | savings | brokerage | retirement)
- currency (ISO 4217)
- created_at (immutable)
- closed_at (nullable — excludes from current Net Worth, retained historically)

### statements

- id (PK)
- account_id (FK)
- period_start, period_end
- closing_balance (native currency, account's cash balance at period_end;
  for investment accounts this is uninvested cash). Source for Net Worth and
  for total investment value in ADR-14.
- created_at
- Provenance is held one-directionally on `processed_files.statement_id` (a
  file is processed into at most one statement). There is no
  `statements.processed_file_id` back-reference — storing both would create a
  circular FK requiring nullable columns and insert-order backfill. "Which file
  produced this statement?" is answered by querying processed_files.

### transactions

- id (PK)
- statement_id (FK)
- date
- amount (signed, native currency)
- description_raw (immutable)
- intra_statement_seq (line ordinal within its statement; feeds content_hash)
- is_transfer (bool — inter-account transfer; set by ADR-15)
- merchant_id (nullable FK, **mutable** — re-matched per ADR-13)
- merchant_source (enum: manual | rule | llm | none — per ADR-13)
- content_hash (UNIQUE — see "Transaction identity")

### Transaction identity

`content_hash = sha256(account_id, posting_date, amount, description_raw, intra_statement_seq)`

- Statement lines carry no stable per-line reference, so the ordinal
  `intra_statement_seq` is the disambiguator. Parsers MUST emit lines in
  deterministic top-to-bottom order (ADR-11) so it's stable across re-parses.
- Residual risk: two identical transactions on DIFFERENT statements, same day,
  same amount/description, collide. See Known limitations.
- First-write-wins (ADR-8): a corrected/restated transaction on a later
  statement hashes differently → lands as a second row; flagged per AC14,
  never silently merged.

### holdings_snapshot

- account_id (FK)
- statement_id (FK)
- symbol
- snapshot_date (= statement.period_end)
- quantity
- cost_basis (broker-reported AGGREGATE at period_end, native currency; not
  lot-level, not computed by Cruzar; **nullable** — NULL when the broker doesn't
  report it, e.g. a Degiro portfolio overview)
- value (market value at snapshot_date, native currency)
- currency (the holding's OWN native currency, e.g. USD for a US stock in an EUR
  account; converted to base at the period-end rate at report time, ADR-5/16)
- PK: (account_id, symbol, snapshot_date)
- IMMUTABLE

### merchants

- id (PK)
- name
- category (FK → categories.name)

### merchant_patterns

- id (PK)
- merchant_id (FK)
- pattern (regex)
- priority (int — lower wins; ties broken by id)

### categories

- name (PK) — controlled vocabulary, seeded from /config/categories.yaml

### processed_files

- file_hash (PK, sha256 of file contents)
- original_filename
- processed_at
- statement_id (FK, nullable if parsing failed)
- status (ok | parse_failed | extraction_failed | unresolved_account)

### fx_rates

- date, base_currency, quote_currency, rate
- PK: (date, base, quote)

## Architectural constraints (ADRs)

- ADR-1: All financial arithmetic in Python/SQL, never in an LLM prompt.
- ADR-2: LLM produces only JSON conforming to declared schemas (via `instructor`/`outlines`).
- ADR-3: SQLite is the source of truth; reports are derived, safe to regenerate. yaml configs are editable inputs seeded into SQLite, not a competing runtime source of truth.
- ADR-4: Each pipeline step independently runnable and idempotent (fetch → parse → normalize → categorize → report).
- ADR-5: **Single-base (EUR).** Data stored native; conversion at report time using the `fx_rates` row as of the relevant **period-end date**. No per-transaction-date conversion.
  - fx_rates is a valuation table, not a transaction-FX record. Transaction-time FX is neither captured nor needed (cost_basis native, per-account gain computed natively, aggregate statements lack per-purchase rates).
  - Stock vs flow caveat: period-end conversion is exact for stocks (Net Worth, holdings value), an approximation for flows (Earned, Spent, Portfolio Δ); error is unsigned. Accepted.
  - Source: exchangerate.host `/timeseries`, ECB fallback. Persisted → reproducible.
- ADR-6: Holdings are event-sourced (immutable snapshots), not state-overwritten.
- ADR-7: Three-layer dedup: file hash, statement period+account, transaction content hash.
- ADR-8: First write wins; conflicting restatements that present as new rows are flagged, never merged (AC14).
- ADR-9: Secrets (OAuth tokens) in macOS Keychain via `keyring`, never plaintext on disk.
- ADR-10: Two ingestion paths converge at `/data/inbox/` (resolution differs by path — see Account resolution).
- ADR-11: One parser module per institution format in /parsers/, common interface `parse(pdf_path) → ParsedStatement`, emitting lines in deterministic top-to-bottom order. Investment parsers MUST also emit (a) closing_balance and (b) cash-flow transactions (deposits/withdrawals/transfers) so ADR-14 can detect contributions. Quirks isolated per parser; core pipeline institution-agnostic.
- ADR-12: LLM outputs persisted to SQLite and never recomputed on reprocessing. A second run of an already-processed file invokes the LLM zero times. `cruzar process --reextract` clears persisted extractions for named files to force re-run (e.g. after a model swap). Makes ADR-4/AC1 hold despite model nondeterminism.
- ADR-13: Categorization provenance three-tier by **authority**: `manual` > `rule` > `llm`.
  - `manual`: frozen; set only by `cruzar recategorize <id> --set <merchant>`; never overwritten.
  - `rule`: re-evaluated each run; a matching rule MAY override a prior `llm` assignment (human correction fixes history); may change/clear if its pattern was edited/removed.
  - `llm`: applied only when no rule matches; overwritten by any rule that later matches; persisted (ADR-12), so supersession by a rule needs no LLM call.
  - `none`: re-evaluated against rules; if still unmatched, eligible for an LLM proposal (subject to ADR-12).
  - `recategorize <id> --clear` resets to `none`.
- ADR-14: **Portfolio Δ = total return net of contributions (option c).** For month M, over investment accounts:
  `Portfolio Δ(M) = (IV_end − IV_prev) − NetContrib(M)`
  - `IV_t` (total investment value at month-end t) = Σ over investment accounts of [ Σ holdings_snapshot.value @ t + statements.closing_balance @ t (uninvested cash) ], each converted to base at t's period-end rate. Using securities + cash means internal buys/sells net to zero in IV and don't pollute the figure.
  - `NetContrib(M)` = signed sum of EXTERNAL cash flows into/out of investment accounts in M (inbound +, outbound −). A transaction on an investment account is an external flow iff `is_transfer = true` (paired with another tracked account, e.g. checking→brokerage) OR it matches an `investment_flow_pattern` (external deposit, employer contribution). Internal trades are not external flows and are excluded.
  - Dividends, reinvested or held as cash, raise IV and are not external flows → counted as return.
  - No prior snapshot → "—".
  - Degradation: if an investment parser cannot emit cash-flow transactions, contributions for that account are undetectable; the month's Portfolio Δ is computed as gross `IV_end − IV_prev` and **flagged** "(gross — contributions undetected)". Documented, never silent.
- ADR-15: **is_transfer detection, steps 1+2 (v1):**
  1. Description rules from `transfer_patterns` in /config/flows.yaml.
  2. Account-pair matching: an opposite-signed transaction of the same absolute amount on another tracked account within ±3 calendar days → mark BOTH `is_transfer = true` (symmetric).
     Step 3 (review appendix) deferred to v1.1. Residual: uncaught transfers leak into Earned/Spent — accepted.
- ADR-16: **Net Worth** at month-end M = base-currency sum, over accounts not closed as of M, of `statements.closing_balance` (latest statement ≤ M per account) + Σ `holdings_snapshot.value` (latest snapshot ≤ M). Closed accounts are excluded from the most-recent row but remain in historical rows preceding their closed_at.

## Acceptance criteria

<Each AC mechanically checkable.>

- AC1: Two consecutive pipeline runs on the same input PDFs produce identical DB state. Verified by sha256 of the DB dump after each. (Holds via ADR-12.)
- AC2: Report aggregates reconcile in the storage currency:
  - Native (exact): per-account report sums equal `SUM(transactions.amount)` over the same account, period, account-class, and is_transfer filter.
  - Base (method-consistent): converted Summary figures equal the reconciliation script's own ADR-5 conversion. Asserts same method, not converted == native.
- AC3: No content_hash appears twice in the DB.
- AC4: The LLM is invoked only for (a) extraction when pdfplumber returns <50% of expected columns, and (b) categorization of merchants unmatched by any pattern, and only when no persisted prior result exists (ADR-12). Verified by log inspection.
- AC5: No secret material on disk outside the Keychain. Smoke test: `grep -rI "ya29\|refresh_token" .` finds nothing in project files AND a scan of the DB file finds no token-shaped values. (Necessary-not-sufficient.)
- AC6: Each investment statement creates exactly one holdings_snapshot row per holding, dated period_end, linked via statement_id; existing rows never UPDATEd/DELETEd. Verified by grouping snapshots by statement_id.
- AC7: Adding an account requires only one sources.yaml entry, (if format differs) one parser module, one test fixture. No core pipeline changes.
- AC8: Every parser module has ≥1 fixture (redacted PDF + expected JSON). Runs on every commit.
- AC9: The report contains Summary, Spending Detail, Earning Detail, Investment Detail in order, plus an optional Needs-Categorization section iff un-categorized merchants exist. Schemas/currencies as in Outputs.
- AC10: Converted figures use the `fx_rates` row whose `date` = the report's month-end (ADR-5); fetched+persisted if absent. Fixture: one foreign-currency account, regenerate the same month on two calendar days → identical converted output.
- AC11: Debits negative, credits positive; no amount+type split. Verified by MIN/MAX(amount).
- AC12: Every transaction has non-null account_id via the FK chain; no orphans. Statements failing resolution are unresolved_account with zero transactions. Verified by LEFT JOIN.
- AC13: `cruzar report` is read-only w.r.t. the DB. Verified by DB sha256 before/after.
- AC14: A restated transaction (same identity inputs, differing amount) on a later statement is surfaced in a conflicts section, never merged or double-counted. Verified by a fixture pair.
- AC15: A second run over PDFs already in processed_files with status=ok invokes the LLM zero times. Verified by a raise-on-call stub LLM; run completes.
- AC16: The FIRST run over a fixture requiring the LLM (trips the <50%-columns fallback, or an unmatched transaction) invokes the LLM (count > 0). Counting spy. AC15+AC16 together prove ADR-12.
- AC17: `manual` rows are never modified by a run. Fixture: set a row manual, add a rule and an llm path that would match it, run, assert unchanged.
- AC18: A rule matching a current `llm` row overrides it next run (becomes `rule`) with no LLM call. Fixture + AC15 raise-stub.
- AC19: Earned and Spent include only cash-account transactions; investment-account transactions are excluded. Fixture with a brokerage buy and an in-account dividend asserts neither appears in Earned or Spent.
- AC20: Portfolio Δ = (IV_end − IV_prev) − NetContrib (ADR-14). Fixtures: (i) a contribution from checking→brokerage is subtracted (Δ unaffected by the transfer itself); (ii) an internal buy funded by existing cash leaves Δ unchanged; (iii) a price-only rise raises Δ by exactly that amount; (iv) no prior snapshot renders "—".
- AC21: A transfer pair (opposite-signed, equal magnitude, two tracked accounts, ±3 days) is excluded from both Earned and Spent and both legs carry is_transfer = true. Fixture.
- AC22: Net Worth = Σ closing_balance + Σ holdings value over non-closed accounts at month-end (ADR-16). A closed account is excluded from the most-recent row but present in historical rows preceding closure. Fixture spanning the closure date.

## Edge cases & failure modes

- Gmail rate limit → exponential backoff, resume from last processed ID.
- Parser fails → log loudly, mark parse_failed, no partial data.
- Statement resolves to no account → unresolved_account, log, ingest nothing.
- Single email/PDF with two accounts → cannot split in v1; flag needs-attention.
- LLM malformed JSON → retry once with stricter prompt, then surface.
- LLM extraction fallback malformed twice → extraction_failed, no partial data, in run summary.
- Statement period overlaps existing data → transaction dedup handles it.
- Same-day identical transactions, same statement → distinguished by intra_statement_seq; different statements → documented collision (Known limitations).
- Restated transaction on a later statement → flagged (AC14), not merged.
- Transfer uncaught by rules+pair-matching → leaks into Earned/Spent (accepted).
- Investment parser lacks cash-flow lines → Portfolio Δ flagged gross for that account-month (ADR-14).
- FX API down → use most recent cached rate, flag in report.
- Network down → all DB writes atomic; complete or rollback.
- New merchant unmatched + LLM low-confidence → "Needs Categorization", no auto-assign.
- LLM proposes off-vocabulary category → treat as un-categorized; no silent new category.
- Merchant pattern removed between runs → affected `rule` rows cleared to `none`, reappear in "Needs Categorization"; `manual`/`llm` unaffected.
- Rule added matching an `llm` row → rule wins (AC18); `manual` untouched.
- Account first appears mid-history → "Tracking since YYYY-MM-DD" footer per account.

## Out of scope for v1 (deferred to v2+)

- UI (web/desktop), direct bank/broker API integration, mobile app
- Cash/Venmo/PayPal/crypto, multi-user, real-time data
- Tax-loss harvesting, rebalancing, tax-lot / realized-gain accounting
- OCR for scanned PDFs
- Automated reconciliation of restated transactions (v1 flags only)
- Per-transaction-date FX / true flow conversion (period-end approximation in v1)
- True multi-currency (multiple bases / per-transaction FX); v1 is single-base EUR
- Splitting a single email/PDF containing multiple accounts' statements
- "Needs Transfer Review" appendix (is_transfer cascade step 3)
- Time-weighted / money-weighted % return; Portfolio Δ is absolute (ADR-14)

## Known limitations (accepted for v1)

These are decided, not open — recorded so they stay visible.

- Cross-statement identical same-day transactions (same amount + description) can collide under content_hash and the second is dropped. Mitigated by deterministic line ordering within a statement; not eliminated across statements.
- Transfers not caught by description rules or ±3-day pair-matching leak into Earned/Spent.
- Transfer pair-matching (ADR-15 step 2) requires both legs in the same currency; a cross-currency transfer pair is not matched (consistent with single-base EUR).
- Flow columns (Earned/Spent/Portfolio Δ) converted at a single period-end rate are an approximation of the true day-by-day converted value; error is unsigned. The base rollup also folds FX drift into cost basis.
- In-account dividends/interest on investment accounts are reported as portfolio return (via IV), not as Earned.
- Portfolio Δ degrades to gross (flagged) for any account-month whose statement does not itemize external contributions.
- Net Worth depends on parsers extracting closing_balance; an account-month without it cannot contribute a balance and is flagged.

## Resolved decisions (traceability)

- cost_basis: aggregate, broker-reported, native currency.
- account_match: no in-statement identifier; resolve by matched sources.yaml entry (email) / folder convention (manual).
- Base currency: hardcoded EUR in /config/cruzar.yaml.
- Multi-currency: single-base EUR; period-end conversion for base rollup only.
- categories: seeded with generic starter vocabulary.
- Earned: cash accounts only; includes salary/interest/refunds/cash-dividends; excludes transfers (ADR-15).
- Portfolio Δ: option (c), total return net of contributions (ADR-14).
- content_hash: option (b), intra_statement_seq.
- Local LLM: Qwen 3 8B via Ollama; config-driven, `--reextract` to rerun.
- Account closure: closed_at; excluded from current Net Worth, kept historical.
- Net Worth: statements.closing_balance + holdings value (ADR-16).
- FX source: exchangerate.host /timeseries, ECB fallback.

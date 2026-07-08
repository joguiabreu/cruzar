# Cruzar

**Cruzar** ("to cross") is a local, privacy-preserving personal-finance
aggregator. It consolidates banking and investment statement PDFs into a single
local view — net worth, spending patterns, and portfolio performance over time —
without sending your financial data anywhere. Everything runs on your machine;
the only data store is a local SQLite file.

For *what* Cruzar computes (metrics, account classes, FX rules, acceptance
criteria), see [`docs/SPEC.md`](docs/SPEC.md). This README covers *how to run it*.

> All names, amounts, and institutions in this README are **made-up
> placeholders**. Real financial data lives only in the gitignored `data/` and
> `reports/` directories and must never appear in anything git can track.

> **Status:** v1 in progress. The manual-ingest pipeline (`cruzar process`) is
> implemented end-to-end: drop PDFs → parse → persist → categorize → write
> reports. The `cruzar fetch` (Gmail) and standalone `cruzar report` commands
> described in the spec are not wired into the CLI yet — see
> [Roadmap](#roadmap).

## Requirements

- **Python 3.12**
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- **[Ollama](https://ollama.com/)** running locally with the configured model
  (`llama3.2:3b` by default) — needed for LLM categorization of merchants no rule
  matched. Optional: set `llm.enabled: false` to run fully offline (rule-only).
  Statements with known parsers and rule-matched merchants don't call the LLM.

## Install

```bash
uv sync
```

This installs the project and exposes the `cruzar` console script. Run it via
`uv run cruzar …` (no manual venv activation required).

## Quick start

```bash
# 1. Declare your accounts (one entry per account)
cp config/sources.yaml.example config/sources.yaml
$EDITOR config/sources.yaml

# 2. Drop a statement PDF into the matching account folder
#    (folder name must equal the account's `account_match`)
mkdir -p data/inbox/examplebank
cp ~/Downloads/statement.pdf data/inbox/examplebank/

# 3. Run the pipeline
uv run cruzar process

# 4. Read your report
open reports/cruzar-2026-05.md
```

## Commands

### `cruzar process`

Runs the full manual-ingest pipeline. It:

1. Seeds the SQLite DB from the YAML configs (accounts, categories, merchants).
2. Scans `data/inbox/` recursively for `*.pdf`.
3. Resolves each PDF to one account by its **folder name** (see
   [Account setup](#account-setup)).
4. Deduplicates by file hash, statement period, and transaction content hash —
   so re-running over unchanged files is a no-op and makes **zero LLM calls**.
5. Parses and persists each statement. If a statement's layout defeats the
   structured parser (fewer than half its rows yield a clean amount column), Cruzar
   asks the **local LLM to read it** from the raw text instead — needs Ollama
   running; without it (or if the model returns unusable output) the file is flagged
   `extraction_failed` and retried on the next run.
6. **Flags transfers** (`is_transfer`) so inter-account moves don't count as
   spending — see [Transfers](#transfers).
7. **Flags restatements** so a corrected line re-listed on a later statement isn't
   double-counted — the first one is kept, the later flagged (see the Conflicts
   section of a report).
8. Categorizes merchants, and writes one Markdown report per month to `reports/`.

```bash
uv run cruzar process
```

It prints a short progress summary to the terminal and writes the reports to
disk. A typical first run looks like:

```text
ingested statement.pdf (12 transactions)
processed 1 file(s): 1 ingested, 0 skipped, 0 failed
wrote 1 report(s) to reports
```

The command is **idempotent**: two consecutive runs on the same inputs produce
identical DB state (SPEC AC1), and the second run reports the file as `skipped`.
A file that fails to parse is logged as an error, marked `parse_failed`, and
writes nothing partial; a PDF in a folder with no matching account is logged as a
warning and marked `unresolved_account` (logged, never guessed).

### `cruzar report`

Re-renders the monthly reports from the existing database, without ingesting
anything:

```bash
uv run cruzar report
```

Because SQLite is the source of truth and reports are derived (ADR-3), you can
regenerate them any time — after a hand correction, or if you deleted the
`reports/` folder. It is **read-only**: it never writes to the DB (SPEC AC13),
makes no network calls, and uses only FX rates already cached by a prior
`process` (a month-end rate that isn't cached renders as `n/a`). Fetching rates is
`process`'s job.

### `cruzar ask`

Ask a free-form question about your data:

```bash
uv run cruzar ask "how much did I spend on Dining in the last 6 months?"
uv run cruzar ask "how much did I spend from the 10th to the 30th of June?"
uv run cruzar ask "what did I spend in the last 10 days?"
uv run cruzar ask "what was my main source of spending last year?"
uv run cruzar ask "how have my investments been going?"
```

The **local LLM only translates your question into a query** (which metric, which
category, which time range) — it never does the arithmetic. The numbers are computed
in Python/`Decimal` from the same source the reports use, so answers are exact and
reconcile with them (the model can't hallucinate a figure). It needs Ollama running
(same `llm:` config as categorization), is **read-only**, and uses cached FX. It
answers about spending (total / by category / by merchant), income (total / by source),
net worth (now or as a trend), and investment performance over a time range. Spending and
income work at **day** granularity too — an explicit day window (e.g. a vacation), "the
last 10 days", "this month", "last month" — while net worth and performance stay
month-based. Anything outside that gets an honest "I can't answer that" rather than a
guess. See
[design notes](docs/design/query_planner.md) for how it works.

### `cruzar anonymize`

Produce a **privacy-safe sample** of a statement so a parser can be developed for a new
institution without exposing real data:

```bash
uv run cruzar anonymize data/inbox/newbank/statement.pdf
# -> data/parsergen/statement/sample.layout.json + gate_report.txt
```

It captures the exact word/geometry layer a parser reads (via `pdfplumber`). Python
**deterministically force-replaces** every value-shaped token (amounts, dates, times, NIF/IBAN,
card masks, long ids, postal codes, emails) so value scrubbing never depends on the model, and the local LLM
**classifies** the rest — labeling remaining tokens as structural (kept) or non-value PII like
names/addresses (replaced). **Python then generates a shape-preserving fake** for each: a comma
stays a comma, a dot stays a dot, dates stay valid, lengths and columns are unchanged.
Two gates guard the result — a deterministic **safety gate** (no real value may survive; it
aborts and writes nothing if one does) and a **fidelity gate** (structure preserved). It needs
Ollama running, is an **operator tool** (never part of `process`), and the output stays in
gitignored `data/` — nothing leaves your machine. See
[plan 030](docs/plans/plan_030_document_anonymizer.md).

The model only has to **name the personal-data tokens to replace** — Python force-replaces every
amount/date/account-number deterministically first — so even a small model (the default
`qwen2.5:3b`) preserves structure well and runs in well under a minute. If you want sharper name
detection on tougher documents, override the model with `--model` or a persistent default:

```yaml
llm:
  anonymize_model: qwen2.5:14b        # override just for anonymize (unset -> uses llm.model)
  anonymize_timeout_seconds: 300
```

```bash
uv run cruzar anonymize data/inbox/newbank/statement.pdf --model qwen2.5:14b
```

**Model caveat:** use one that *terminates* under Ollama's structured-JSON output. Some models
(e.g. `gemma4`) run away to their token limit and stall the whole Ollama queue; same-family
scale-ups of a model you know works (e.g. `qwen2.5:7b`/`14b`) are the safe choice.

**Scrub your own name/address deterministically.** Amounts, dates, and account numbers are
value-shaped, so Python force-replaces them without the model. A **name is just a word** — it's
not value-shaped, so it relies on the model, which can miss it. Put your name, address, and account
numbers in the gitignored `.pii-denylist` (one term per line; phrases are split into words, so a
full name protects each name token). The anonymizer then force-replaces those deterministically,
and the safety gate blocks any run where one survives. Still eyeball the sample for third-party
names the model didn't catch.

### What a report looks like

Reports land at `reports/cruzar-YYYY-MM.md`, one per calendar month. The
generated Markdown (placeholder data shown):

```text
# Cruzar — 2026-05

## Summary

| Month | Earned | Spent | Net | Portfolio Δ | Net Worth |
| --- | --- | --- | --- | --- | --- |
| 2026-05 | 2000.00 | -52.50 | 1947.50 | 130.40 | 18230.40 |
| 2026-04 | 2000.00 | -610.00 | 1390.00 | — | 17800.10 |

## Spending Detail

| Date | Amount | Currency | Merchant | Category |
| --- | --- | --- | --- | --- |
| 2026-05-27 | -10.00 | EUR | Streaming Co | Subscriptions |
| 2026-05-20 | -42.50 | EUR | Corner Grocer | Groceries |

## Spending by Category

| Category | Spent (EUR) |
| --- | --- |
| Groceries | -42.50 |
| Subscriptions | -10.00 |

## Earning Detail

| Date | Amount | Currency | Source |
| --- | --- | --- | --- |
| 2026-05-22 | 2000.00 | EUR | Example Salary |

## Investment Detail

### Example Brokerage

| Symbol | Quantity | Currency | Cost Basis | Current Value | Δ Amount | Δ % |
| --- | --- | --- | --- | --- | --- | --- |
| EXMPL | 2 | USD | 300.00 | 360.00 | 60.00 | 20.0% |
| **Total (EUR)** |  |  |  | 331.20 |  |  |

### Grand Total (EUR)

| Current Value |
| --- |
| 331.20 |

## Needs Categorization

| Raw Description | LLM-Proposed Merchant | LLM-Proposed Category |
| --- | --- | --- |
| POS 4521 UNKNOWN VENDOR | Maybe Cafe | Dining |

## Conflicts

| Date | Account | Description | Amount (kept) | Amount (restated) |
| --- | --- | --- | --- | --- |
| 2026-05-15 | Example Checking | EXAMPLE SUBSCRIPTION | -10.00 | -12.00 |
```

**Summary** (Section 1) is in EUR: one row per month (last 12, newest first),
computed as of each month-end — except the current, in-progress month, which is
valued as-of **today** (its month-end is in the future, where no exchange rate
exists yet). **Earned/Spent** are cash-account flows and **Net**
is their sum — the cash you kept that month (negative when you spent more than you
earned). **Net Worth** sums cash balances + holdings value across accounts, converting foreign
holdings at the month-end rate (see [FX rates](#fx-rates)). **Portfolio Δ** is
total return on your investment accounts net of external contributions, month over
month — `(value now − value a month ago) − money you paid in or took out`; it shows
`—` until there's a prior month to compare against, and `(gross — contributions
undetected)` when a broker's statement can't itemise deposits (see
[`emits_cash_flows`](#account-setup)). **Investment Detail** lists each holding per
account (native currency, with unrealised Δ vs cost where the broker reports it) and
a EUR Grand Total. The **Spending Detail** and
**Earning Detail** sections are native-currency and itemise that month's
cash outflows and inflows respectively (Earning Detail's rows sum to the Summary's
Earned); transfers between your own accounts are excluded — see
[Transfers](#transfers). **Spending by Category** rolls that month's spending up by
the matched merchant's category, in EUR (spending the categorizer couldn't place is
bucketed as *Uncategorized*); its rows sum to the Summary's Spent. **Needs Categorization** appears only when this month has
cash transactions no merchant pattern matched and the LLM didn't confidently place;
it shows the raw description and the LLM's (unapplied) guess for you to act on — see
[Categorization](#categorization). **Conflicts** appears only when a later statement
re-lists a transaction with a corrected amount: the line lands as a second row, so
Cruzar keeps the first one (it stays in your totals) and flags the restatement here
rather than silently merging or double-counting it — you decide which is right.

## Account setup

Statements carry no stable in-document account identifier, so Cruzar binds each
PDF to an account by **where you put it**. The folder name under `data/inbox/`
must match an `account_match` value in `config/sources.yaml`.

`config/sources.yaml` — one entry per account (placeholder values):

```yaml
accounts:
  - institution: examplebank     # selects src/cruzar/parsers/<institution>.py
    name: Everyday Checking
    account_match: examplebank    # files go in data/inbox/<account_match>/
    source_type: manual
    account_type: checking        # checking | savings | brokerage | retirement
    currency: EUR
    # emits_cash_flows: false     # investment accounts only; see the note below
```

Then place statements accordingly:

```text
data/inbox/
└── examplebank/
    └── statement.pdf
```

Adding a new account is a **YAML edit only** (SPEC AC7) — no code change —
*unless* the institution's PDF format needs a new parser module (see below).

> The `institution` value must match a parser module in
> `src/cruzar/parsers/`. The repo ships with six parsers today (`activobank`,
> `moey`, `revolut`, `interactivebrokers`, `degiro`, and `aforronet` — the last
> three are investment accounts; `aforronet` reads AforroNet *Certificado de
> Aforro* statements, the Portuguese state savings certificates, as a held
> position); if your bank isn't covered, add one (next section).

> **ActivoBank multi-month exports:** a single PDF that stacks several monthly
> sections (each with its own `EXTRATO DE … A …`, `SALDO INICIAL/FINAL`, and salary)
> is parsed into **one statement per month** — each keeps its own period and closing
> balance, so month-by-month Net Worth is correct and re-uploading one of those months
> later is recognised as a duplicate (not added twice). A single-month statement is
> just the one-section case.

> **Investment accounts** (`account_type: brokerage`/`retirement`) capture an
> immutable `holdings_snapshot` from the statement's positions (each holding in its
> own native currency) plus the uninvested cash balance. These feed Net Worth and
> the **Portfolio Δ** summary column.
>
> Add `emits_cash_flows: false` (default `true`) when the broker's statement is a
> periodic *summary* with no per-deposit lines — Interactive Brokers' monthly
> Activity Statement is the example shipped. Without those lines, external
> contributions can't be detected, so that account's Portfolio Δ is reported **gross**
> and flagged `(gross — contributions undetected)` rather than silently mistaking a
> deposit for a gain (ADR-14).

## Configuration

All config lives in `config/` and is seeded into SQLite on each run. SQLite is
the source of truth at runtime; the YAML files are editable inputs (ADR-3).

| File                   | Purpose                                                  |
| ---------------------- | -------------------------------------------------------- |
| `sources.yaml`         | Account allowlist (gitignored — your real accounts).     |
| `sources.yaml.example` | Template to copy from.                                   |
| `cruzar.yaml`          | App config: `base_currency` (EUR), `llm` (Ollama categorization), `fx`. |
| `categories.yaml`      | Controlled category vocabulary.                          |
| `merchants.yaml`       | Merchant names + match patterns for categorization.      |
| `flows.yaml`           | `transfer_patterns` (transfer detection) + `investment_flow_patterns` (external contributions, ADR-14). |
| `fx_rates.yaml`        | *Optional* hand-supplied FX rates (see FX rates below).  |

`config/cruzar.yaml` — the app-wide knobs:

```yaml
base_currency: EUR
llm:
  enabled: true                 # false → rule-only, no LLM calls (fully offline)
  model: llama3.2:3b            # any Ollama model string (see note below)
  host: http://localhost:11434  # where Ollama listens
  min_confidence: 0.7           # below this, a proposal is shown but not auto-assigned
  timeout_seconds: 60           # per request; a too-slow model is skipped, run continues
fx:
  offline: false                # true → never fetch; use only cached/manual rates
  timeout_seconds: 10           # FX HTTP request timeout
  # access_key:                 # optional exchangerate.host key; without it, ECB is used
```

### Every setting you can change

| Setting | What it does | Change it when… |
| --- | --- | --- |
| `base_currency` | Report/base currency (EUR for v1). | Don't — v1 is EUR-only (ADR-5). |
| `llm.enabled` | Turns the LLM categorization tier on/off. | You want a fully offline, rule-only run, or Ollama isn't installed. |
| `llm.model` | Which **Ollama** model labels merchants. Must be pulled first (`ollama pull <model>`). | You want faster/better labeling. **Use a non-thinking model** (e.g. `qwen2.5:7b`/`:3b`, `llama3.2:3b`); reasoning models (`qwen3.x`) emit a `<think>` block that is slow and truncates the JSON, and Ollama's structured-output path gives no reliable way to disable it. |
| `llm.host` | Ollama's address. | Ollama runs on another port/host. |
| `llm.min_confidence` | Threshold to auto-apply a proposal; below it the guess goes to *Needs Categorization* unapplied. | Too much lands in Needs-Categorization → lower toward `0.6`; too many wrong auto-assigns → raise. |
| `llm.timeout_seconds` | Per-request timeout. After 3 consecutive timeouts the run gives up the LLM pass with a hint. | Your model is slow but you want to wait longer (or you switched to a fast model and want it tighter). |
| `fx.offline` | `true` → never hit the network for FX; use cached/manual rates only. | You have no internet or supply rates by hand. |
| `fx.timeout_seconds` | FX HTTP request timeout. | Flaky network. |
| `fx.access_key` | Optional exchangerate.host key (else ECB is used). | You have a paid FX provider key. |

> **Model note (learned the hard way):** Cruzar asks the LLM only to *label* a short
> description, so a small, fast, **non-reasoning** model is ideal. `qwen3:8b` and other
> "thinking" models generate 1000+ reasoning tokens per call and blow past the timeout
> on consumer hardware; `llama3.2:3b` answers in ~1s. Install with `ollama pull llama3.2:3b`.
> After changing the model, clear cached proposals so it re-runs (they're never recomputed —
> ADR-12): `sqlite3 data/cruzar.db "DELETE FROM llm_categorizations; UPDATE transactions SET merchant_id=NULL, merchant_source='none' WHERE merchant_source='llm';"`

The other editable inputs — accounts (`sources.yaml`), categories (`categories.yaml`),
merchant rules (`merchants.yaml`), and flow patterns (`flows.yaml`) — are covered in
[Account setup](#account-setup), [Categorization](#categorization), and [Transfers](#transfers).

## Categorization

Each transaction gets a merchant + category by **authority** (ADR-13): `manual >
rule > llm`.

1. **Rule** — a transaction whose description matches a `merchant_patterns` entry in
   `config/merchants.yaml` is assigned that merchant (re-evaluated every run).
2. **LLM** — for descriptions no rule matched, a local LLM (Ollama) proposes a
   merchant + category + confidence. A **confident, in-vocabulary** proposal is
   applied (`merchant_source = 'llm'`); a **low-confidence or off-vocabulary** one is
   kept as a suggestion and listed in the report's **Needs Categorization** section,
   never auto-assigned. A matching rule added later overrides an LLM assignment.
3. **Manual** — frozen; set it yourself (CLI is a later slice). Never overwritten.

Proposals are **persisted and never recomputed** (ADR-12): a re-run over unchanged
data makes **zero** LLM calls. The LLM is **optional** — set `llm.enabled: false` for
a fully offline, rule-only run. And it **degrades gracefully**: if Ollama isn't
running, the report still generates (the numbers are all there); affected lines stay
uncategorized in Needs Categorization and are **retried automatically** on the next
`cruzar process` once the model is back. Cruzar never asks the LLM to do arithmetic —
it only proposes labels (ADR-1/2).

> **Setup:** install [Ollama](https://ollama.com) and pull the model
> (`ollama pull llama3.2:3b`). Cruzar talks to it locally at
> `http://localhost:11434`; nothing leaves your machine.

## FX rates

Foreign-currency holdings (e.g. a USD stock in an EUR account) are converted to the
base currency at the **valuation-date rate** (ADR-5): a month's month-end, but never
later than today (`min(month_end, today)`). For the in-progress month that means
today's rate — a future month-end has none yet. A rate is **fetched once and
persisted**, then reused — so regenerating a *past* month is reproducible (the
in-progress month tracks today, so it can change day to day).

This is the **only external network call** Cruzar makes. It sends just a currency
pair and a date — **never any financial data** — and after the first fetch the rate
lives in your local SQLite. Source: exchangerate.host (if you set `fx.access_key`),
else the keyless ECB reference rates. If the provider is unreachable, the most
recent cached rate is used and flagged. To stay fully offline, set `fx.offline: true`
and/or supply rates by hand in `config/fx_rates.yaml` (copy from `.example`).

## Transfers

Money moving between your own accounts (or paid to someone) isn't spending, so
Cruzar flags those transactions `is_transfer` and excludes them from Spending
Detail. Detection (ADR-15) is two steps:

1. **Description rules** — any transaction whose description matches a
   `transfer_pattern` in `config/flows.yaml`.
2. **Account-pair matching** — an opposite-signed transaction of equal amount on
   another tracked account, same currency, within ±3 days, marks both legs.

`config/flows.yaml` (placeholder values):

```yaml
transfer_patterns:
  - "TRF P/"                 # outbound transfer
  - "Trf imediata"           # instant transfer to a person
  - "TRANSF SEPA"            # SEPA transfer
  - "Transferência para"     # Revolut: outbound transfer to a person
  - "P2P Personal Payments"  # Revolut: peer payment
  - "Carregamento com"       # Revolut: top-up (card / Google Pay) — own funding
  - "Conversão cambial"      # Revolut: internal currency exchange
```

> Patterns match **outbound or own-funding** flows only. **Inbound** descriptions
> are never patterned — Revolut `Transferência de …` and Moey `IPS/…` carry real
> third-party income, so a rule would wrongly drop it from income. Their
> own-account legs are caught by step-2 pairing instead.

> Keep patterns **specific** — never a bare `TRANSFER`. A broad rule would also
> match an income line like `TRANSFERENCIA - VENCIMENTO` (salary) and wrongly
> drop it from income. Patterns are committed, so they must never contain a real
> counterparty name.

Detection is recomputed every run from the current patterns, so editing
`flows.yaml` re-evaluates all transactions on the next `cruzar process`.

`flows.yaml` also holds `investment_flow_patterns` — description rules that mark a
transaction on an **investment account** as an external contribution/withdrawal
(money paid in or taken out from outside), so Portfolio Δ can net it out. Keep them
deposit/withdrawal-specific: a return line like `Flatex Interest Income` must stay
counted as a gain, not a contribution.

```yaml
investment_flow_patterns:
  - "flatex Deposit"          # Degiro: external cash deposit into the brokerage
```

## Adding a parser for a new institution

Each institution format has one parser module in `src/cruzar/parsers/`,
implementing `parse(pdf_path) -> ParsedStatement` and emitting lines in
deterministic top-to-bottom order (ADR-11). To add one:

1. Create `src/cruzar/parsers/<institution>.py` (shared row-clustering helpers
   live in `src/cruzar/parsers/_common.py`).
2. Register it in `src/cruzar/parsers/__init__.py` (one line in `PARSERS`).
3. Add a fixture under `tests/fixtures/<institution>/` — a **synthetic** PDF
   (generated by a committed generator from a hand-authored transaction table)
   plus the expected `ParsedStatement` JSON (SPEC AC8).
4. Add the account to `config/sources.yaml`.

That's the whole change (SPEC AC7) — the core pipeline stays
institution-agnostic. The shipped `moey` parser is the canonical example.

To develop a parser against a real statement without exposing its data, first run
[`cruzar anonymize`](#cruzar-anonymize) on it and work from the anonymized sample. The
committed fixture (step 3) is still hand-authored with obviously-fake values — the anonymized
sample is a gitignored dev aid, never the fixture.

**Never** copy a real statement into `tests/fixtures/` — those are tracked by
git. Real data lives only in the gitignored `data/` directory, and fixture
values must be obviously fake.

## Where things live

```text
config/          # YAML inputs (sources, categories, merchants, app config)
data/            # gitignored — your statements (data/inbox/) and the SQLite DB
reports/         # gitignored — generated cruzar-YYYY-MM.md reports
src/cruzar/      # pipeline, parsers, persistence, reporting
src/cruzar/parsergen/  # `cruzar anonymize` tooling (operator-only, not in the pipeline)
tests/           # unit tests + acceptance harness (tests/acceptance/)
docs/SPEC.md     # full specification (the source of truth for behavior)
```

`data/` and `reports/` are gitignored: your financial data and generated reports
never enter version control.

## Development

```bash
uv run pytest                    # full test suite
uv run pytest tests/acceptance   # acceptance harness — one test per AC (AC1–AC23)
uv run ruff check .              # lint
uv run pyright                   # strict type check
```

The acceptance harness in `tests/acceptance/` is the gate — each test maps 1:1 to
an acceptance criterion in `docs/SPEC.md`. Before any change is "done":

```bash
uv run ruff check . && uv run pyright && uv run pytest
```

### Measuring categorization accuracy

The test suite is offline (it uses fake LLMs), so it proves the wiring but not how
well a given model labels your real transactions. To make model choice data-driven,
put a few dozen labeled rows in `data/eval/categorization.csv` (gitignored;
`description,expected_category`) and run, with Ollama up:

```bash
uv run python scripts/eval_categorization.py
```

It runs the live model over the LLM tier only and reports accuracy + the misses — a
20-minute way to compare models before changing `llm.model`.

### Privacy guard

A deterministic pre-commit hook (`.githooks/check_pii.py`) blocks staging any
real financial value (figures, account numbers, denylisted names). A fresh clone
must opt in:

```bash
git config core.hooksPath .githooks
```

## Roadmap

These are specified in `docs/SPEC.md` but not yet exposed by the CLI:

- `cruzar fetch` — Gmail fetcher that polls the inbox, applies the
  sender/subject allowlist from `sources.yaml`, and downloads attachments to
  `data/inbox/`. (OAuth tokens stored in the macOS Keychain via `keyring`.)
- `cruzar report` — standalone, read-only report regeneration.
- `cruzar recategorize <id> --set/--clear` — manual categorization overrides.
- `cruzar process --reextract` — clear persisted LLM extractions and re-run
  (e.g. after swapping the model).

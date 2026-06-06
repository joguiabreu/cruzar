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
  (`qwen3:8b` by default) — needed for LLM extraction/categorization fallbacks.
  Pure-text statements with known parsers and known merchants don't call the LLM.

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
5. Parses and persists each statement.
6. **Flags transfers** (`is_transfer`) so inter-account moves don't count as
   spending — see [Transfers](#transfers).
7. Categorizes merchants, and writes one Markdown report per month to `reports/`.

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

### What a report looks like

Reports land at `reports/cruzar-YYYY-MM.md`, one per calendar month. The
generated Markdown (placeholder data shown):

```text
# Cruzar — 2026-05

## Spending Detail

| Date | Amount | Currency | Merchant | Category |
| --- | --- | --- | --- | --- |
| 2026-05-27 | -10.00 | EUR | Streaming Co | Subscriptions |
| 2026-05-20 | -42.50 | EUR | Corner Grocer | Groceries |
```

(Transfers between your own accounts are excluded — see [Transfers](#transfers).)

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
> `src/cruzar/parsers/`. The repo ships with three parsers today (`activobank`,
> `moey`, `revolut`); if your bank isn't covered, add one (next section).

## Configuration

All config lives in `config/` and is seeded into SQLite on each run. SQLite is
the source of truth at runtime; the YAML files are editable inputs (ADR-3).

| File                   | Purpose                                                  |
| ---------------------- | -------------------------------------------------------- |
| `sources.yaml`         | Account allowlist (gitignored — your real accounts).     |
| `sources.yaml.example` | Template to copy from.                                   |
| `cruzar.yaml`          | App config: `base_currency` (EUR), `llm_model` (Ollama). |
| `categories.yaml`      | Controlled category vocabulary.                          |
| `merchants.yaml`       | Merchant names + match patterns for categorization.      |
| `flows.yaml`           | `transfer_patterns` for transfer detection (see below).  |

`config/cruzar.yaml`:

```yaml
base_currency: EUR
llm_model: qwen3:8b
```

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

**Never** copy a real statement into `tests/fixtures/` — those are tracked by
git. Real data lives only in the gitignored `data/` directory, and fixture
values must be obviously fake.

## Where things live

```text
config/          # YAML inputs (sources, categories, merchants, app config)
data/            # gitignored — your statements (data/inbox/) and the SQLite DB
reports/         # gitignored — generated cruzar-YYYY-MM.md reports
src/cruzar/      # pipeline, parsers, persistence, reporting
tests/           # unit tests + acceptance harness (tests/acceptance/)
docs/SPEC.md     # full specification (the source of truth for behavior)
```

`data/` and `reports/` are gitignored: your financial data and generated reports
never enter version control.

## Development

```bash
uv run pytest                    # full test suite
uv run pytest tests/acceptance   # acceptance harness — one test per AC (AC1–AC22)
uv run ruff check .              # lint
uv run pyright                   # strict type check
```

The acceptance harness in `tests/acceptance/` is the gate — each test maps 1:1 to
an acceptance criterion in `docs/SPEC.md`. Before any change is "done":

```bash
uv run ruff check . && uv run pyright && uv run pytest
```

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

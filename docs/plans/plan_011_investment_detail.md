# Cruzar — Slice 11 Plan (Investment Detail, Section 4 — SPEC §Outputs, AC9)

Plan of record (decisions settled). Implements the already-specified Section 4 — a
per-position view of the holdings Net Worth aggregates. Reuses `holdings_snapshot`

- FX; no new ADR, no schema change.

## Section 4 (as built)

A per-account subsection for each investment account, as of the report's month-end,
then a base-currency Grand Total.

```
## Investment Detail

### Conta Interactive Brokers
| Symbol | Quantity | Currency | Cost Basis | Current Value | Δ Amount | Δ % |
| AMZN | 2 | USD | 300.00 | 360.00 | 60.00 | 20.0% |
| WEBN | 5 | EUR | 400.00 | 450.00 | 50.00 | 12.5% |
| Total (EUR) |  |  |  | 760.00 |  |  |

### Conta Degiro
| IE00… | 30 | EUR | n/a | 1,500.00 | n/a | n/a |
| Total (EUR) |  |  |  | 1,500.00 |  |  |

### Grand Total (EUR)
| Current Value |
| 2,260.00 |
```

- Source: latest `holdings_snapshot` with `snapshot_date ≤ month-end` per investment
  account.
- **Δ Amount** = Current Value − Cost Basis (unrealised vs cost, native); **Δ %** =
  Δ ÷ Cost Basis. `n/a` when cost_basis is NULL (Degiro reports none).
- Per holding shown in its own currency; per-account **Total** and **Grand Total**
  in **EUR** (each holding converted at the month-end rate, reusing slice-8 FX).

## Decisions (settled)

- **D1 — Include Δ Amount / Δ %. AGREED.** Per-position unrealised gain vs cost;
  `n/a` where cost is unknown. Distinct from the declined Summary portfolio-return %
  (that was contribution-adjusted / time-weighted, an ADR-14 non-goal; this is
  simply value−cost).
- **D2 — Currency & totals. AGREED, with a build correction.** Holdings render in
  native currency; totals in EUR via the month-end rate (degrade a cell to `n/a` on
  FX failure, like the Summary). **Build note:** real IBKR holdings are _mixed
  currency_ (EUR + USD in one account), which the SPEC's "native per subsection"
  didn't anticipate — so a **Currency column** is added per holding and per-account
  totals are EUR (a native total would be meaningless when currencies mix). SPEC
  §Outputs Section 4 updated to match.
- **D3 — Always present + AC9 fix. AGREED.** Render the section always, with a
  "No investment holdings." line when there are none (satisfies AC9 ordering for
  cash-only users). AC9 updated to the current order: Summary → Spending Detail →
  Earning Detail → Investment Detail, plus the optional Needs-Categorization section.

## Files touched

```text
src/cruzar/metrics.py                          # investment_holdings(conn, month_end) -> per-account holdings
src/cruzar/report.py                           # _investment_section: subsections + EUR totals (n/a on FxError)
docs/SPEC.md                                   # Section 4 columns (+Currency, EUR totals); AC9 order
README.md                                      # show Investment Detail in the example report
tests/acceptance/test_ac09_report_sections.py  # section presence + order; Δ/n/a; EUR grand total
```

## Test plan (slice gate)

- Fixture: an IBKR-like account with a USD holding + cost basis, and a Degiro-like
  account with an EUR holding + NULL cost basis; a seeded fx_rate. Assert positions
  listed under their account; Δ Amount/Δ% computed for the one with cost, `n/a` for
  the null one; Grand Total in EUR sums the converted values.
- Section order (AC9): a report contains `## Summary`, `## Spending Detail`,
  `## Earning Detail`, `## Investment Detail` in that order; a cash-only report
  shows the Investment Detail header with the empty-state line.
- Existing suite green; `ruff`/`pyright`/`pytest` clean; **then run
  `uv run cruzar process`** (live-path rule) to confirm it renders on real data.

## Verification / done

- AC9 test green; gate clean; SPEC + README updated; real run shows Investment
  Detail with the IBKR/Degiro positions and an EUR Grand Total.
- **"Done"** = per-position holdings render, Δ where cost is known, Grand Total in
  EUR; section present in order.

```

```

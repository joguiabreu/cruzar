# Cruzar — Slice 8 Plan (FX rates — ADR-5/AC10)

Plan of record (decisions settled). Foundation for the Summary/Net Worth slice —
makes USD (IBKR) holdings valuable in EUR. No report consumes it yet; this slice
adds the capability + offline tests, dormant until Net Worth wires it in.

## What the SPEC already fixed (implemented, not re-decided)

- **ADR-5:** single-base EUR; store native; convert at report time using the
  `fx_rates` row as of the **period-end date**. `fx_rates` is a valuation table.
- **AC10:** converted figures use the `fx_rates` row whose `date` = the month-end;
  **fetched + persisted if absent**; regenerating a month gives identical output
  (rate now persisted → no re-fetch).
- **Source:** exchangerate.host `/timeseries` (when a key is set), **ECB fallback**
  (keyless), persisted → reproducible.
- **Degradation:** provider unreachable → most recent cached rate, flagged stale.

## Decisions (settled)

- **D1 — Network fetch (privacy) + optional manual seed. AGREED.** Implements the
  SPEC's fetch-if-absent + persist, AND an optional `config/fx_rates.yaml` seeded
  into `fx_rates` for fully-offline use. The fetch sends only a currency pair + date
  (never financial data) and caches forever after the first call. `fx.offline: true`
  disables fetching entirely.
- **D2 — HTTP via stdlib `urllib`. AGREED.** No new dependency; the fetch lives
  behind an injectable seam, so swapping to `httpx` later (proxies, retries, or the
  Gmail `fetch` slice) is a localized ~10-line change.
- **D3 — Rate convention. AGREED.** One row per `(date, EUR, quote)`, `rate` =
  units of quote per 1 EUR; convert quote→EUR = `amount / rate`; EUR→EUR identity.
- **D4 — Lookup + degradation ladder. AGREED.** exact persisted row → fetch+persist
  → most-recent cached ≤ date (stale flag) → raise (fail loud).
- **D5 — Tests never hit the network. AGREED.** `fetch` is injected; a spy asserts
  no re-fetch when cached (AC10 core); convention + degradation unit-tested.

## What was built

```text
src/cruzar/fx.py                 # get_rate / convert / providers (exchangerate.host→ECB) / persist
src/cruzar/config.py             # ManualRate; Config.fx_rates + fx_offline/access_key/timeout
config/cruzar.yaml               # fx: offline / timeout_seconds / access_key
config/fx_rates.yaml.example     # optional manual-seed template
src/cruzar/persist.py            # seed_config seeds manual fx rates (INSERT-only)
tests/test_fx.py                 # offline unit tests (mocked fetch)
README.md                        # FX section (the one external call; cached; degradation)
```

- `get_rate(conn, on, quote, *, fetch=_default_fetch) -> (Decimal, stale)`;
  `convert(conn, amount, currency, on, *, fetch=...) -> Decimal`. `fetch=None` = offline.
- Rates parsed straight to `Decimal` (`json.loads(parse_float=Decimal)`); persisted to
  the existing `fx_rates` table (no schema change).
- ECB fetch queries a small back-window and takes the latest observation ≤ date
  (covers weekends/holidays). The live providers are the network seam — exercised in
  production, not in the unit suite (D5).

## Out (next slices)

- The Summary / Net Worth / Portfolio Δ report that **consumes** `convert()`
  (AC22/AC20) — next slice; AC10's report-level "regenerate twice → identical"
  assertion lands there with a foreign-currency-account fixture.
- No parser/model changes; FX is not sourced from statements (SPEC source is the
  rate API), though the IBKR statement's rate table remains a possible future
  offline source.

## Test plan (slice gate) — all green

- Convention: seeded rate → `convert(100 USD, 2026-05-31)` == `100 / rate`; EUR identity.
- Fetch-if-absent + persist; second call served from cache, fetcher **not** re-called
  (AC10 core, via spy).
- Degradation: fetch fails → most recent cached + stale flag; no cache + fail → raises.
- Manual seed: `seed_config` rows are visible to `get_rate` offline.
- `ruff` / `pyright` / `pytest` clean (35 passed); pipeline smoke + schema-parity green.

## Verification / done

- FX unit tests green; full gate clean; README updated.
- **"Done"** = `fx.convert()` works against a persisted rate and fetches-then-caches
  when absent (offline-reproducible), with a degradation path; ready for the Net
  Worth slice. AC10's report-level assertion explicitly deferred to that slice.
```

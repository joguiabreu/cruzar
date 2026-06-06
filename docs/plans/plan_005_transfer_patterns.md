# Cruzar — Slice 5 Plan (transfer detection for Moey & Revolut — ADR-15, AC19/AC21)

Plan of record (decisions settled). **Not implemented yet — awaiting "address
notes, implement."** This is a `config/flows.yaml` change + test coverage —
**no parser, pipeline, or schema code changes.**

## Context

Transfer detection (ADR-15) runs on persisted transactions in two steps:
(1) `transfer_patterns` in `config/flows.yaml`, matched case-insensitively against
`description_raw`; (2) account-pair matching (opposite sign, equal magnitude,
another tracked account, same currency, ±3 days). `flows.yaml` was deliberately
out of scope for the parser slices (3 and 4). Verified against the real data:

- **Moey — already covered.** Its outbound verbs (`Trf imediata`, `TRF P/`) and
  the self SEPA inbound leg (`TRANSF SEPA`) already match step-1 patterns. No
  Moey change needed.
- **Revolut — matches nothing.** Its wording (`Transferência …`, `Carregamento
  com cartão`, `Levantamento de numerário`) hits zero current patterns, so only
  step-2 pairing can flag a Revolut leg — and only when the opposite leg is on
  another tracked account within ±3 days. Card top-ups, payments to people, and
  currency conversions are not flagged at all.

## Governing principle: never pattern an inflow that could be income

Earned = inflows on cash accounts with `is_transfer = false` (SPEC; includes
salary, interest, refunds). Per AC19 / the `VENCIMENTO` carve-out: flagging an
inbound description as a transfer when it was actually money paid to you
**silently deletes real income**. The real data proves inbound is mixed — Moey
`IPS/…` appears both as `…-JOAO GUILHERME` (you, own-account) and
`…-<other people>` (real income). So **outbound** descriptions are safe to
pattern; **inbound** descriptions are not — they stay unflagged and are caught
only by step-2 pairing when genuinely own-account. Uncaught own-account inflows
leaking into Earned is an accepted ADR-15 residual; dropping real income is not.

## Revolut vocabulary (real data, classified)

| Verb (description head) | Sign | Meaning | Pattern? |
|----|----|----|----|
| `Transferência para <NAME>` | − | outbound transfer to a person | YES (D2) |
| `P2P Personal Payments` | − | outbound peer payment | YES (D2) |
| `Carregamento com cartão` / `… com Google Pay …` | + | top-up funding Revolut from your own card | YES (D3) — inflow but never income |
| `Conversão cambial para <CCY>` | −/+ | internal currency exchange between your own wallets | YES (D1) |
| `Transferência de <NAME>` / `… de utilizador Revolut` | + | inbound transfer — may be income | NO — income risk; pairing only |
| `Levantamento de numerário em …` | − | ATM cash withdrawal | NO — cash spending |
| `Comissão …`, merchant names (Continente, Spotify, …) | − | fees / purchases | NO — spending |
| `Revolut Bank UAB` / `Revolut Payments UAB` | +/− | fees / cashback / interest (ambiguous, low volume) | NO — leave; interest belongs in Earned |

## `flows.yaml` change

Add Revolut outbound + own-funding patterns; add none for inbound. Specificity:
`Transferência para` includes `para` so it cannot match inbound `Transferência
de`; and neither matches ActivoBank's accent-free salary `TRANSFERENCIA -
VENCIMENTO`.

```yaml
transfer_patterns:
  - "TRF P/"                 # ActivoBank outbound   (existing)
  - "TRF MB WAY"             # ActivoBank → person   (existing)
  - "Trf imediata"           # Moey → person         (existing)
  - "TRANSF SEPA"            # Moey self inbound leg  (existing)
  - "Transferência para"     # Revolut → person       (NEW, outbound only)
  - "P2P Personal Payments"  # Revolut peer payment   (NEW, outbound)
  - "Carregamento com"       # Revolut top-up (card / Google Pay), own funding (NEW)
  - "Conversão cambial"      # Revolut internal FX wallet move        (NEW, D1)
# DELIBERATELY NOT ADDED (income risk, AC19): "Transferência de", "IPS"
```

## Decisions (settled)

- **D1 — `Conversão cambial` = transfer. RESOLVED.** Internal exchange between your
  own wallets; never leaves net worth, and patterning it avoids double-counting
  when a pocket is funded then spent via its own merchant lines. Accepted caveat:
  if downstream foreign spending isn't separately itemised it's hidden — judged
  non-critical (only occurs while travelling). Pairing can't catch it (other leg
  is an untracked-currency wallet), so a pattern is the only lever.
- **D2 — Outbound person-payments = transfer. RESOLVED.** `Transferência para` and
  `P2P Personal Payments` flagged, consistent with the existing "payment to a
  person = transfer for now" treatment of `TRF MB WAY` / `Trf imediata`.
- **D3 — Top-ups = transfer. RESOLVED.** `Carregamento com` (covers `cartão` and
  `Google Pay`) flagged — inflow but never third-party income, so income-safe.
- **Reaffirmed: no inbound patterns.** `Transferência de …` and Moey `IPS/…` stay
  unpatterned (mixed real income). Own-account inbound legs are caught by step-2
  pairing; the rest stay in Earned (accepted residual). Protects AC19.

## Scope

### In
- `config/flows.yaml` — add the four NEW patterns above.
- `tests/acceptance/test_ac19_income_not_flagged.py` — NEW. Asserts the new
  classification on synthetic, obviously-fake descriptions: outbound/top-up/FX
  flagged; inbound `Transferência de` / `IPS` and `Levantamento` NOT flagged by
  rule. Locks in income protection against future `flows.yaml` edits.
- `README.md` — Transfers section: document the Revolut verbs and the
  inbound-stays-as-income asymmetry.

### Out
- Any parser / pipeline / schema change (none needed — detection is config-driven).
- Revisiting MB WAY / Trf imediata semantics (D2 keeps consistency).
- ADR-15 step-3 review appendix (deferred to v1.1 per SPEC).
- Earned/Spent Summary assertions (AC21 scope note — lands with the Summary slice).

## Files touched

```text
config/flows.yaml                                  # add Revolut patterns (D1–D3)
tests/acceptance/test_ac19_income_not_flagged.py   # NEW — inbound stays income; outbound/top-up/FX flagged
README.md                                          # Transfers: Revolut verbs + income asymmetry
```

## Test plan (slice gate)

- New test builds a fresh DB with synthetic Revolut/Moey-style descriptions on two
  accounts, runs `transfers.detect(conn, <flows.yaml patterns>)`, and asserts:
  - `Transferência para …`, `P2P Personal Payments`, `Carregamento com cartão`,
    `Conversão cambial …` → `is_transfer = 1`;
  - `Transferência de …`, `IPS/…`, `Levantamento de numerário …`, a merchant
    `COMPRA …` → `is_transfer = 0` (income/spending preserved).
- The test loads the real `config/flows.yaml` patterns (not a hardcoded list) so a
  future edit that breaks income protection fails here.
- Existing AC21 pairing test stays green (its own pattern list is unaffected).
- `uv run ruff check . && uv run pyright && uv run pytest` all clean.

## Verification / done

- New test green; full gate clean; README Transfers section updated.
- **"Done"** = new test passes, gate clean, README updated, and a re-run of the
  real-data smoke shows Revolut outbound/top-ups/FX now flagged while inbound
  stays as income (run on request — touches real data).

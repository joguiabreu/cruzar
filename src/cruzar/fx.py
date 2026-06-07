"""FX rates: fetch-if-absent, persist, convert at the period-end rate (ADR-5).

Single-base EUR. ``fx_rates`` is a valuation table keyed by (date, base, quote);
the stored ``rate`` is **units of quote per 1 EUR** (ECB / exchangerate.host
convention), so converting an amount in ``quote`` to EUR is ``amount / rate``.

Lookup ladder for a month-end (AC10 / SPEC degradation):
  1. exact persisted row  → use it (reproducible; no network);
  2. else fetch that date → persist → use it;
  3. fetch failed but an earlier rate is cached → most recent ≤ date, flagged stale;
  4. nothing cached and fetch failed → raise (fail loud).

The fetch is an injectable seam: ``fetch`` defaults to the live provider; pass
``fetch=None`` for fully-offline operation (cache / manual-seed only), or a custom
callable in tests so the suite never touches the network. Rates parse straight to
``Decimal`` (never float).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

BASE = "EUR"
_DEFAULT_TIMEOUT = 10.0
Fetcher = Callable[[date, str], Decimal]


class FxError(Exception):
    """Raised when a needed FX rate is unavailable (no cache and fetch failed)."""


# --- live provider (network seam — exercised in prod, not in the unit suite) ---

def _http_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - https provider
        return json.loads(resp.read().decode("utf-8"), parse_float=Decimal)


def _fetch_exchangerate_host(
    on: date, quote: str, *, access_key: str, timeout: float
) -> Decimal:
    url = (
        "https://api.exchangerate.host/timeseries"
        f"?start_date={on.isoformat()}&end_date={on.isoformat()}"
        f"&base={BASE}&symbols={quote}&access_key={access_key}"
    )
    doc = _http_json(url, timeout)
    try:
        value = doc["rates"][on.isoformat()][quote]
    except (KeyError, TypeError) as exc:
        raise FxError(f"exchangerate.host: no {quote} rate for {on}") from exc
    if not isinstance(value, Decimal):
        raise FxError(f"exchangerate.host: non-numeric {quote} rate for {on}")
    return value


def _fetch_ecb(on: date, quote: str, *, timeout: float) -> Decimal:
    # ECB publishes EUR reference rates on business days; query a small back-window
    # and take the latest observation on/before `on` (covers weekends/holidays).
    start = (on - timedelta(days=10)).isoformat()
    url = (
        f"https://data-api.ecb.europa.eu/service/data/EXR/D.{quote}.{BASE}.SP00.A"
        f"?startPeriod={start}&endPeriod={on.isoformat()}&format=jsondata"
    )
    doc = _http_json(url, timeout)
    try:
        series = next(iter(doc["dataSets"][0]["series"].values()))
        observations = series["observations"]
        periods = doc["structure"]["dimensions"]["observation"][0]["values"]
        dated = sorted(
            (str(periods[int(i)]["id"]), obs[0]) for i, obs in observations.items()
        )
    except (KeyError, IndexError, StopIteration, TypeError) as exc:
        raise FxError(f"ECB: unparseable response for {quote} {on}") from exc
    for period_id, value in reversed(dated):
        if period_id <= on.isoformat() and isinstance(value, Decimal):
            return value
    raise FxError(f"ECB: no {quote} observation on/before {on}")


def _default_fetch(
    on: date, quote: str, *, access_key: str | None = None, timeout: float = _DEFAULT_TIMEOUT
) -> Decimal:
    """exchangerate.host (when a key is configured) → ECB fallback (keyless)."""
    if access_key:
        try:
            return _fetch_exchangerate_host(on, quote, access_key=access_key, timeout=timeout)
        except (FxError, OSError) as exc:
            logger.warning("FX: exchangerate.host failed (%s); trying ECB", exc)
    try:
        return _fetch_ecb(on, quote, timeout=timeout)
    except (FxError, OSError) as exc:
        raise FxError(f"all FX providers failed for {quote} {on}: {exc}") from exc


# --- public API --------------------------------------------------------------

def get_rate(
    conn: sqlite3.Connection,
    on: date,
    quote: str,
    *,
    fetch: Fetcher | None = _default_fetch,
) -> tuple[Decimal, bool]:
    """Return (rate, stale) to convert ``quote`` → EUR as of ``on``.

    ``rate`` is quote-per-EUR. ``stale`` is True when a fetch failed and an older
    cached rate was used instead. Pass ``fetch=None`` to stay offline.
    """
    quote = quote.upper()
    if quote == BASE:
        return Decimal(1), False

    exact = conn.execute(
        "SELECT rate FROM fx_rates WHERE date = ? AND base_currency = ? "
        "AND quote_currency = ?",
        (on.isoformat(), BASE, quote),
    ).fetchone()
    if exact is not None:
        return Decimal(exact[0]), False

    if fetch is not None:
        try:
            rate = fetch(on, quote)
        except (FxError, OSError):
            rate = None
        if rate is not None:
            _persist(conn, on, quote, rate)
            return rate, False

    cached = _nearest_cached(conn, on, quote)
    if cached is not None:
        logger.warning(
            "FX: no rate for %s on %s; using most recent cached rate (stale)", quote, on
        )
        return cached, True
    raise FxError(f"no FX rate for {quote} as of {on} and no cached fallback")


def convert(
    conn: sqlite3.Connection,
    amount: Decimal,
    currency: str,
    on: date,
    *,
    fetch: Fetcher | None = _default_fetch,
) -> Decimal:
    """Convert ``amount`` in ``currency`` to EUR as of ``on`` (amount / rate)."""
    rate, _stale = get_rate(conn, on, currency, fetch=fetch)
    return amount / rate


def _persist(conn: sqlite3.Connection, on: date, quote: str, rate: Decimal) -> None:
    conn.execute(
        "INSERT INTO fx_rates(date, base_currency, quote_currency, rate) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(date, base_currency, quote_currency) "
        "DO NOTHING",
        (on.isoformat(), BASE, quote, str(rate)),
    )


def _nearest_cached(conn: sqlite3.Connection, on: date, quote: str) -> Decimal | None:
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE base_currency = ? AND quote_currency = ? "
        "AND date <= ? ORDER BY date DESC LIMIT 1",
        (BASE, quote, on.isoformat()),
    ).fetchone()
    return Decimal(row[0]) if row is not None else None

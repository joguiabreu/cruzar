"""FX module: conversion convention, fetch-if-absent + persist (no re-fetch),
manual-seed, and the offline/degradation ladder. The network is never touched —
``fetch`` is always injected (a stub, a raiser, or None).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cruzar import fx
from cruzar.config import Config, ManualRate
from cruzar.db import connect, init_schema
from cruzar.fx import FxError, convert, get_rate
from cruzar.persist import seed_config


class _NonJsonResponse:
    """A urlopen() stand-in returning a non-JSON body (e.g. ECB's XML default)."""

    def __enter__(self) -> _NonJsonResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return b"<html>not json</html>"


def _fake_urlopen(*_args: object, **_kwargs: object) -> _NonJsonResponse:
    return _NonJsonResponse()

_D = date(2026, 5, 31)


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "fx.db")
    init_schema(conn)
    return conn


def _seed_rate(conn: sqlite3.Connection, on: date, quote: str, rate: str) -> None:
    conn.execute(
        "INSERT INTO fx_rates(date, base_currency, quote_currency, rate) "
        "VALUES (?, 'EUR', ?, ?)",
        (on.isoformat(), quote, rate),
    )
    conn.commit()


class _Spy:
    def __init__(self, rate: Decimal) -> None:
        self.rate = rate
        self.calls = 0

    def __call__(self, on: date, quote: str) -> Decimal:
        self.calls += 1
        return self.rate


def test_convert_convention_amount_over_rate(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _seed_rate(conn, _D, "USD", "1.25")  # 1 EUR = 1.25 USD
    assert convert(conn, Decimal("100"), "USD", _D, fetch=None) == Decimal("80")
    assert convert(conn, Decimal("100"), "EUR", _D, fetch=None) == Decimal("100")  # identity


def test_fetch_if_absent_persists_then_no_refetch(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    spy = _Spy(Decimal("2"))
    rate, stale = get_rate(conn, _D, "USD", fetch=spy)
    assert (rate, stale) == (Decimal("2"), False)
    assert spy.calls == 1
    # second call is served from the persisted row — no re-fetch (AC10 core)
    rate2, stale2 = get_rate(conn, _D, "USD", fetch=spy)
    assert (rate2, stale2) == (Decimal("2"), False)
    assert spy.calls == 1


def test_degradation_uses_most_recent_cached_when_fetch_fails(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _seed_rate(conn, date(2026, 5, 20), "USD", "1.10")

    def boom(on: date, quote: str) -> Decimal:
        raise FxError("api down")

    rate, stale = get_rate(conn, _D, "USD", fetch=boom)
    assert rate == Decimal("1.10")
    assert stale is True  # flagged: an older rate was used


def test_no_cache_and_fetch_fails_raises(tmp_path: Path) -> None:
    conn = _db(tmp_path)

    def boom(on: date, quote: str) -> Decimal:
        raise FxError("api down")

    with pytest.raises(FxError):
        get_rate(conn, _D, "USD", fetch=boom)


def test_offline_uses_cache_only(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _seed_rate(conn, _D, "USD", "1.30")
    rate, stale = get_rate(conn, _D, "USD", fetch=None)  # offline
    assert (rate, stale) == (Decimal("1.30"), False)


def test_live_fetch_non_json_raises_fxerror(monkeypatch: pytest.MonkeyPatch) -> None:
    # A provider returning XML/HTML (the real ECB crash) must surface as FxError,
    # not an uncaught JSONDecodeError that kills the pipeline.
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    with pytest.raises(FxError):
        fx.live_fetcher()(_D, "USD")


def test_live_fetch_garbage_degrades_to_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live fetcher hitting a garbage response degrades to the cached rate via
    # the ladder — no crash (this is exactly what broke in the real run).
    conn = _db(tmp_path)
    _seed_rate(conn, date(2026, 5, 20), "USD", "1.10")
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    rate, stale = get_rate(conn, _D, "USD", fetch=fx.live_fetcher())
    assert rate == Decimal("1.10")
    assert stale is True


def test_manual_seed_feeds_fx_rates(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    config = Config(
        base_currency="EUR", accounts=[], categories=[], merchants=[],
        transfer_patterns=[], fx_rates=[ManualRate("2026-05-31", "USD", Decimal("1.4"))],
    )
    seed_config(conn, config)
    assert get_rate(conn, _D, "USD", fetch=None) == (Decimal("1.4"), False)

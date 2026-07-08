"""LLM categorization tier (ADR-2/12/13). One offline file; each test maps to a
spec clause via its name. The LLM is always a fake injected as ``propose`` — no test
reaches Ollama.

- AC4(b): the LLM is invoked only for pattern-unmatched transactions, and only when
  no persisted prior proposal exists.
- AC15: a second run over already-processed data invokes the LLM zero times.
- AC16: the first run over an unmatched transaction invokes the LLM (count > 0).
- AC17: ``manual`` rows are never modified, even when a rule AND the LLM would match.
- AC18: a rule matching a current ``llm`` row overrides it next run (→ ``rule``), no call.

Plus: low-confidence / off-vocabulary → ``needs_review`` (not applied); an LLM outage
degrades (rows stay ``none``, nothing cached) and recovers on the next run; and the
report's Needs-Categorization section renders the proposals.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cruzar import categorize, report
from cruzar.categorize import LlmError, LlmTimeout, LlmUnavailable, Proposal
from cruzar.db import connect, init_schema
from cruzar.models import ParsedStatement
from cruzar.persist import persist_statement

_CATEGORIES = ["Groceries", "Dining", "Other"]


# --- fakes injected as the LlmCategorizer ------------------------------------

class _Spy:
    """Counts calls; returns a fixed proposal."""

    def __init__(self, proposal: Proposal) -> None:
        self.calls = 0
        self._proposal = proposal

    def propose(self, description: str, categories: list[str]) -> Proposal:
        self.calls += 1
        return self._proposal


class _Raise:
    """Fails the test loudly if the LLM is called (NOT an LlmError, so it is not
    swallowed by the degradation path)."""

    def propose(self, description: str, categories: list[str]) -> Proposal:
        raise AssertionError("the LLM must not be called on this run")


class _Boom:
    """Simulates a single bad answer (per-item degradation, not a full outage)."""

    def propose(self, description: str, categories: list[str]) -> Proposal:
        raise LlmError("bad json")


class _Down:
    """Simulates Ollama being unreachable; counts how often it was hit."""

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, description: str, categories: list[str]) -> Proposal:
        self.calls += 1
        raise LlmUnavailable("connection refused")


class _AlwaysTimeout:
    """Every call times out (model too slow); counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, description: str, categories: list[str]) -> Proposal:
        self.calls += 1
        raise LlmTimeout("timed out")


class _TimeoutExceptNth:
    """Times out on every call except the ``ok_on``-th, which succeeds."""

    def __init__(self, ok_on: int, proposal: Proposal) -> None:
        self.calls = 0
        self._ok_on = ok_on
        self._proposal = proposal

    def propose(self, description: str, categories: list[str]) -> Proposal:
        self.calls += 1
        if self.calls == self._ok_on:
            return self._proposal
        raise LlmTimeout("timed out")


class _FlakyThenOk:
    """Unreachable for the first ``fail_times`` calls (a crash/restart), then recovers."""

    def __init__(self, fail_times: int, proposal: Proposal) -> None:
        self.calls = 0
        self._fail_times = fail_times
        self._proposal = proposal

    def propose(self, description: str, categories: list[str]) -> Proposal:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise LlmUnavailable("connection refused")
        return self._proposal


# --- helpers -----------------------------------------------------------------

def _setup(conn: sqlite3.Connection) -> int:
    """A checking account + an empty May statement; seed the category vocabulary.
    Returns the statement id to hang transactions on."""
    init_schema(conn)
    for name in _CATEGORIES:
        conn.execute("INSERT INTO categories(name) VALUES (?)", (name,))
    cur = conn.execute(
        "INSERT INTO accounts(institution, name, account_match, source_type, "
        "account_type, currency, created_at) "
        "VALUES ('bank', 'Checking', 'm', 'manual', 'checking', 'EUR', '2026-01-01T00:00:00+00:00')"
    )
    account_id = cur.lastrowid
    assert account_id is not None
    return persist_statement(
        conn, account_id,
        ParsedStatement(currency="EUR", period_start=date(2026, 5, 1),
                        period_end=date(2026, 5, 31), closing_balance=Decimal("0.00"),
                        transactions=[]),
    )


def _txn(conn: sqlite3.Connection, statement_id: int, description: str, *, seq: int = 1,
         amount: str = "-10.00", source: str = "none", merchant_id: int | None = None,
         is_transfer: int = 0) -> int:
    cur = conn.execute(
        "INSERT INTO transactions(statement_id, date, amount, description_raw, "
        "intra_statement_seq, is_transfer, merchant_id, merchant_source, content_hash) "
        "VALUES (?, '2026-05-10', ?, ?, ?, ?, ?, ?, ?)",
        (statement_id, amount, description, seq, is_transfer, merchant_id, source,
         f"hash-{seq}-{description}"),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _merchant_with_pattern(conn: sqlite3.Connection, name: str, category: str, pattern: str) -> int:
    cur = conn.execute("INSERT INTO merchants(name, category) VALUES (?, ?)", (name, category))
    merchant_id = cur.lastrowid
    assert merchant_id is not None
    conn.execute(
        "INSERT INTO merchant_patterns(merchant_id, pattern, priority) VALUES (?, ?, 100)",
        (merchant_id, pattern),
    )
    return merchant_id


def _no_sleep(_seconds: float) -> None:
    """Replaces time.sleep so retry-backoff tests don't actually wait."""
    return None


def _source(conn: sqlite3.Connection, txn_id: int) -> tuple[str, int | None]:
    row = conn.execute(
        "SELECT merchant_source, merchant_id FROM transactions WHERE id = ?", (txn_id,)
    ).fetchone()
    return row["merchant_source"], row["merchant_id"]


# --- tests -------------------------------------------------------------------

def test_ac16_first_run_invokes_llm_on_unmatched(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        txn = _txn(conn, stmt, "UNKNOWN MERCHANT XYZ")
        conn.commit()
        spy = _Spy(Proposal("Corner Grocer", "Groceries", 0.95))

        categorize.categorize(conn, propose=spy, model="qwen3:8b", min_confidence=0.7)

        assert spy.calls == 1
        source, merchant_id = _source(conn, txn)
        assert source == "llm" and merchant_id is not None
        cached = conn.execute(
            "SELECT proposed_merchant, status FROM llm_categorizations "
            "WHERE description_raw = 'UNKNOWN MERCHANT XYZ'"
        ).fetchone()
        assert cached["status"] == "applied" and cached["proposed_merchant"] == "Corner Grocer"
    finally:
        conn.close()


def test_ac15_reprocess_makes_zero_llm_calls(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        txn = _txn(conn, stmt, "UNKNOWN MERCHANT XYZ")
        conn.commit()
        categorize.categorize(conn, propose=_Spy(Proposal("Corner Grocer", "Groceries", 0.95)))

        # Second run: a raise-on-call stub proves the LLM is not touched.
        categorize.categorize(conn, propose=_Raise())  # must not raise

        assert _source(conn, txn) == ("llm", _source(conn, txn)[1])
    finally:
        conn.close()


def test_ac04_llm_only_for_unmatched_and_no_prior(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        _merchant_with_pattern(conn, "Coffee Bar", "Dining", "COFFEE")
        ruled = _txn(conn, stmt, "COFFEE morning", seq=1)         # a rule matches → no LLM
        unmatched = _txn(conn, stmt, "MYSTERY VENDOR", seq=2)     # no rule → one LLM call
        # A pre-existing proposal for a third description: must NOT trigger a call.
        conn.execute(
            "INSERT INTO llm_categorizations(description_raw, proposed_merchant, "
            "proposed_category, confidence, status, model, created_at) "
            "VALUES ('CACHED VENDOR', 'Known Co', 'Other', 0.9, 'applied', 'm', 'now')"
        )
        cached_txn = _txn(conn, stmt, "CACHED VENDOR", seq=3)
        conn.commit()
        spy = _Spy(Proposal("Mystery Co", "Other", 0.9))

        categorize.categorize(conn, propose=spy, min_confidence=0.7)

        assert spy.calls == 1  # only the unmatched, uncached transaction
        assert _source(conn, ruled)[0] == "rule"
        assert _source(conn, unmatched)[0] == "llm"
        assert _source(conn, cached_txn)[0] == "llm"  # applied from cache, no call
    finally:
        conn.close()


def test_ac17_manual_row_never_modified(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        # A manual assignment whose description a rule AND the LLM would otherwise match.
        manual_merchant = _merchant_with_pattern(conn, "Hand Set", "Other", "PAYDAY")
        manual_txn = _txn(conn, stmt, "PAYDAY bonus", source="manual", merchant_id=manual_merchant)
        conn.commit()
        spy = _Spy(Proposal("Something Else", "Dining", 0.99))

        categorize.categorize(conn, propose=spy, min_confidence=0.7)

        assert spy.calls == 0  # manual rows are not in the 'none' set
        assert _source(conn, manual_txn) == ("manual", manual_merchant)
    finally:
        conn.close()


def test_ac18_rule_overrides_llm_with_no_call(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        txn = _txn(conn, stmt, "GROCER PLUS")
        conn.commit()
        categorize.categorize(conn, propose=_Spy(Proposal("Grocer", "Groceries", 0.95)))
        assert _source(conn, txn)[0] == "llm"

        # A rule is added that matches it; next run must demote llm → rule, no call.
        rule_merchant = _merchant_with_pattern(conn, "Grocer Plus", "Groceries", "GROCER PLUS")
        conn.commit()
        categorize.categorize(conn, propose=_Raise())  # must not raise

        assert _source(conn, txn) == ("rule", rule_merchant)
    finally:
        conn.close()


def test_low_confidence_is_needs_review_not_applied(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        txn = _txn(conn, stmt, "AMBIGUOUS THING")
        conn.commit()

        categorize.categorize(conn, propose=_Spy(Proposal("Maybe Co", "Groceries", 0.3)),
                              min_confidence=0.7)

        assert _source(conn, txn) == ("none", None)  # not auto-assigned
        row = conn.execute(
            "SELECT status, proposed_merchant FROM llm_categorizations "
            "WHERE description_raw = 'AMBIGUOUS THING'"
        ).fetchone()
        assert row["status"] == "needs_review" and row["proposed_merchant"] == "Maybe Co"
    finally:
        conn.close()


def test_off_vocabulary_category_is_needs_review(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        txn = _txn(conn, stmt, "CRYPTO EXCHANGE")
        conn.commit()

        # High confidence, but 'Crypto' is not in the controlled vocabulary.
        categorize.categorize(conn, propose=_Spy(Proposal("Coin Place", "Crypto", 0.99)),
                              min_confidence=0.7)

        assert _source(conn, txn) == ("none", None)
        assert conn.execute(
            "SELECT status FROM llm_categorizations WHERE description_raw = 'CRYPTO EXCHANGE'"
        ).fetchone()["status"] == "needs_review"
    finally:
        conn.close()


def test_outage_degrades_then_recovers_next_run(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        txn = _txn(conn, stmt, "FLAKY VENDOR")
        conn.commit()

        # Ollama down: the run completes, the row stays none, NOTHING is cached.
        categorize.categorize(conn, propose=_Boom())
        assert _source(conn, txn) == ("none", None)
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM llm_categorizations WHERE description_raw = 'FLAKY VENDOR'"
        ).fetchone()["n"] == 0

        # Model back: because nothing was cached, the row is retried and resolved.
        spy = _Spy(Proposal("Recovered Co", "Other", 0.9))
        categorize.categorize(conn, propose=spy)
        assert spy.calls == 1
        assert _source(conn, txn)[0] == "llm"
    finally:
        conn.close()


def test_unavailable_aborts_pass_after_exhausting_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sustained outage is retried (bounded) on the first item, then the pass aborts
    and leaves every line none — it does NOT retry-storm each of the 500 descriptions."""
    monkeypatch.setattr(categorize, "_CONNECT_RETRIES", 2)
    monkeypatch.setattr(categorize.time, "sleep", _no_sleep)  # no real backoff wait
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        a = _txn(conn, stmt, "VENDOR ONE", seq=1)
        b = _txn(conn, stmt, "VENDOR TWO", seq=2)
        conn.commit()
        down = _Down()

        categorize.categorize(conn, propose=down)

        # First item: 1 try + 2 retries = 3; then the pass aborts (second item untried).
        assert down.calls == 3
        assert _source(conn, a) == ("none", None)
        assert _source(conn, b) == ("none", None)
        assert conn.execute("SELECT COUNT(*) AS n FROM llm_categorizations").fetchone()["n"] == 0
    finally:
        conn.close()


def test_transient_outage_is_retried_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash/restart mid-call is bridged by the retry: the item is still categorized
    and the pass continues, rather than abandoning the batch."""
    monkeypatch.setattr(categorize, "_CONNECT_RETRIES", 3)
    monkeypatch.setattr(categorize.time, "sleep", _no_sleep)
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        txn = _txn(conn, stmt, "FLAKY VENDOR")
        conn.commit()
        flaky = _FlakyThenOk(fail_times=2, proposal=Proposal("Recovered Co", "Other", 0.9))

        categorize.categorize(conn, propose=flaky)

        assert flaky.calls == 3  # 2 failures bridged, succeeded on the 3rd
        assert _source(conn, txn)[0] == "llm"
    finally:
        conn.close()


def test_consecutive_timeouts_abort_the_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A too-slow model that keeps timing out aborts after _TIMEOUT_ABORT consecutive
    timeouts instead of grinding one full timeout per line through all 500."""
    monkeypatch.setattr(categorize, "_TIMEOUT_ABORT", 3)
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        for i in range(5):
            _txn(conn, stmt, f"VENDOR {i}", seq=i + 1)
        conn.commit()
        slow = _AlwaysTimeout()

        categorize.categorize(conn, propose=slow)

        assert slow.calls == 3  # stopped after 3 consecutive timeouts (not all 5)
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE merchant_source = 'none'"
        ).fetchone()["n"]
        assert left == 5  # nothing applied; all retried next run
    finally:
        conn.close()


def test_a_success_resets_the_timeout_streak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout streak broken by a success doesn't trip the abort: 2 timeouts, a
    success, then 2 more timeouts never reaches 3-in-a-row."""
    monkeypatch.setattr(categorize, "_TIMEOUT_ABORT", 3)
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        for i in range(5):
            _txn(conn, stmt, f"VENDOR {i}", seq=i + 1)
        conn.commit()
        fake = _TimeoutExceptNth(ok_on=3, proposal=Proposal("Co", "Other", 0.9))

        categorize.categorize(conn, propose=fake)

        assert fake.calls == 5  # never 3 consecutive, so all five were attempted
    finally:
        conn.close()


def test_section5_renders_uncategorized_with_proposal(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        _txn(conn, stmt, "REVIEW ME PLEASE")
        conn.commit()
        categorize.categorize(conn, propose=_Spy(Proposal("Guess Co", "Groceries", 0.2)),
                              min_confidence=0.7)

        report.write_reports(conn, tmp_path / "out", fetch=None)
        content = (tmp_path / "out" / "cruzar-2026-05.md").read_text(encoding="utf-8")
        assert "## Needs Categorization" in content
        assert "| REVIEW ME PLEASE | Guess Co | Groceries |" in content
    finally:
        conn.close()


def test_section5_absent_when_all_categorized(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    try:
        stmt = _setup(conn)
        _txn(conn, stmt, "GROCER PLUS")
        conn.commit()
        categorize.categorize(conn, propose=_Spy(Proposal("Grocer", "Groceries", 0.95)),
                              min_confidence=0.7)

        report.write_reports(conn, tmp_path / "out", fetch=None)
        content = (tmp_path / "out" / "cruzar-2026-05.md").read_text(encoding="utf-8")
        assert "## Needs Categorization" not in content
    finally:
        conn.close()

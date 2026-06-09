"""Categorization (ADR-13) — three-tier by authority: ``manual > rule > llm``.

Two passes per run, both idempotent:

1. **Rule pass.** For each non-``manual`` transaction, match ``merchant_patterns``
   (lower priority wins, ties by id). A match sets ``merchant_source = 'rule'``
   (overriding a prior ``llm`` — a human correction fixes history). No match clears
   a stale ``rule`` to ``none`` (its pattern was removed) but LEAVES an ``llm`` row
   intact (its proposal persists, ADR-12/13).
2. **LLM pass.** For each remaining ``none`` transaction the LLM proposes a merchant
   + category + confidence (ADR-2, schema-constrained JSON; no math, ADR-1). The
   proposal is persisted in ``llm_categorizations`` keyed by the raw description, so
   an identical line reuses it and a re-run makes ZERO calls (ADR-12). A confident,
   in-vocabulary proposal is applied (a merchant row is upserted and linked,
   ``merchant_source = 'llm'``); a low-confidence or off-vocabulary one is kept as
   ``needs_review`` and surfaced in the report, never auto-assigned. An LLM outage
   degrades (logs, leaves the row ``none``, caches nothing) so it retries next run.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import NamedTuple, Protocol

logger = logging.getLogger(__name__)

# A model server can crash and restart mid-run (memory pressure is common locally),
# so a refused connection is retried with growing backoff to bridge the restart
# rather than abandoning the rest of the batch. Sustained failure still gives up.
_CONNECT_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds; doubles each retry (2, 4, 8)
# Stop the pass after this many consecutive timeouts — the model is too slow for the
# configured timeout, so grinding through the rest one full timeout at a time is waste.
_TIMEOUT_ABORT = 3


class Proposal(NamedTuple):
    merchant: str
    category: str
    confidence: float  # in [0, 1]


class LlmError(Exception):
    """A per-item failure talking to the LLM (e.g. the model returned unusable JSON).
    Caught by the LLM pass, which degrades (leaves that row ``none``) rather than
    crashing the run."""


class LlmUnavailable(LlmError):
    """The LLM service is unreachable (not a single bad answer). The pass aborts and
    leaves the remaining rows uncategorized for the next run, instead of retrying
    every line against a service that is down."""


class LlmTimeout(LlmError):
    """A request exceeded the timeout — the model was too slow for this item (not a
    dead server). Handled per-item, but a *run* of them aborts the pass (the model is
    too slow for the configured timeout; grinding 60s/line helps no one)."""


class LlmCategorizer(Protocol):
    def propose(self, description: str, categories: list[str]) -> Proposal:
        """Propose {merchant, category, confidence} for a raw description, choosing
        the category from ``categories``. Raises ``LlmError`` on a transport failure."""
        ...


def categorize(
    conn: sqlite3.Connection,
    *,
    propose: LlmCategorizer | None = None,
    model: str = "",
    min_confidence: float = 0.7,
) -> None:
    _rule_pass(conn)
    _llm_pass(conn, propose=propose, model=model, min_confidence=min_confidence)
    conn.commit()


def _rule_pass(conn: sqlite3.Connection) -> None:
    patterns = conn.execute(
        "SELECT merchant_id, pattern FROM merchant_patterns ORDER BY priority ASC, id ASC"
    ).fetchall()
    compiled = [(row["merchant_id"], re.compile(row["pattern"], re.IGNORECASE)) for row in patterns]

    rows = conn.execute(
        "SELECT id, description_raw, merchant_source FROM transactions "
        "WHERE merchant_source != 'manual'"
    ).fetchall()
    for row in rows:
        matched_merchant: int | None = None
        for merchant_id, rx in compiled:
            if rx.search(row["description_raw"]):
                matched_merchant = merchant_id
                break
        if matched_merchant is not None:
            conn.execute(
                "UPDATE transactions SET merchant_id = ?, merchant_source = 'rule' WHERE id = ?",
                (matched_merchant, row["id"]),
            )
        elif row["merchant_source"] == "rule":
            # Its pattern was removed/edited and no longer matches → back to none.
            # An 'llm' row is left untouched: the proposal persists until a rule wins.
            conn.execute(
                "UPDATE transactions SET merchant_id = NULL, merchant_source = 'none' WHERE id = ?",
                (row["id"],),
            )


def _llm_pass(
    conn: sqlite3.Connection,
    *,
    propose: LlmCategorizer | None,
    model: str,
    min_confidence: float,
) -> None:
    categories = [row["name"] for row in conn.execute("SELECT name FROM categories")]
    rows = conn.execute(
        "SELECT id, description_raw FROM transactions WHERE merchant_source = 'none'"
    ).fetchall()
    by_description: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        by_description[row["description_raw"]].append(row["id"])
    if not by_description:
        return

    # Counts only — never log a description or amount (privacy invariant).
    total = len(by_description)
    if propose is not None:
        logger.info(
            "categorizing %d description(s) (%d transaction(s)) via LLM (%s)",
            total, len(rows), model or "llm",
        )
    applied = needs_review = left = 0
    aborted = False
    consecutive_timeouts = 0

    for index, (description, txn_ids) in enumerate(by_description.items(), start=1):
        cached = conn.execute(
            "SELECT proposed_merchant, proposed_category, status "
            "FROM llm_categorizations WHERE description_raw = ?",
            (description,),
        ).fetchone()
        if cached is None:
            if propose is None or aborted:
                left += len(txn_ids)  # LLM disabled, or already gave up this run
                continue
            logger.info("LLM proposal %d/%d…", index, total)  # liveness: each is one call
            try:
                proposal = _propose_with_retry(propose, description, categories)
            except LlmUnavailable as exc:
                # Down even after retries — stop; leave the rest for the next run.
                logger.warning(
                    "LLM unavailable after %d retries (%s); skipping the rest this run",
                    _CONNECT_RETRIES, exc,
                )
                aborted = True
                left += len(txn_ids)
                continue
            except LlmTimeout as exc:
                consecutive_timeouts += 1
                left += len(txn_ids)
                if consecutive_timeouts >= _TIMEOUT_ABORT:
                    logger.warning(
                        "LLM timed out on %d consecutive descriptions — model too slow; "
                        "skipping the rest this run (use a smaller model or raise "
                        "llm.timeout_seconds)", consecutive_timeouts,
                    )
                    aborted = True
                else:
                    logger.warning("LLM proposal timed out (%s); left for next run", exc)
                continue
            except LlmError as exc:
                consecutive_timeouts = 0  # a bad answer, not a slowness streak
                logger.warning("one LLM proposal failed (%s); left for next run", exc)
                left += len(txn_ids)
                continue  # cache nothing → retried when healthy
            cached = _persist_proposal(conn, description, proposal, categories, model, min_confidence)
        consecutive_timeouts = 0  # a resolved item (fresh or cached) breaks any streak
        if cached["status"] == "applied":
            _apply(conn, txn_ids, cached["proposed_merchant"], cached["proposed_category"])
            applied += len(txn_ids)
        else:
            needs_review += len(txn_ids)

    logger.info(
        "LLM categorization: %d applied, %d need review, %d left uncategorized",
        applied, needs_review, left,
    )


def _propose_with_retry(
    propose: LlmCategorizer, description: str, categories: list[str]
) -> Proposal:
    """Call the LLM, retrying a *connection* failure with growing backoff so a model
    server that crashed and is restarting doesn't abandon the batch. A non-connection
    ``LlmError`` (e.g. a bad answer) is not retried — it's a per-item problem."""
    delay = _BACKOFF_BASE
    last_exc: LlmUnavailable | None = None
    for attempt in range(1, _CONNECT_RETRIES + 2):  # initial try + _CONNECT_RETRIES
        try:
            return propose.propose(description, categories)
        except LlmUnavailable as exc:
            last_exc = exc
            if attempt <= _CONNECT_RETRIES:
                logger.warning(
                    "LLM unreachable; retry %d/%d in %.0fs…", attempt, _CONNECT_RETRIES, delay
                )
                time.sleep(delay)
                delay *= 2
    assert last_exc is not None
    raise last_exc


def _persist_proposal(
    conn: sqlite3.Connection,
    description: str,
    proposal: Proposal,
    categories: list[str],
    model: str,
    min_confidence: float,
) -> sqlite3.Row:
    applied = proposal.confidence >= min_confidence and proposal.category in categories
    status = "applied" if applied else "needs_review"
    conn.execute(
        "INSERT INTO llm_categorizations(description_raw, proposed_merchant, "
        "proposed_category, confidence, status, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(description_raw) DO UPDATE SET "
        "proposed_merchant = excluded.proposed_merchant, "
        "proposed_category = excluded.proposed_category, confidence = excluded.confidence, "
        "status = excluded.status, model = excluded.model, created_at = excluded.created_at",
        (description, proposal.merchant, proposal.category, proposal.confidence,
         status, model, datetime.now(UTC).isoformat()),
    )
    row = conn.execute(
        "SELECT proposed_merchant, proposed_category, status "
        "FROM llm_categorizations WHERE description_raw = ?",
        (description,),
    ).fetchone()
    assert row is not None
    return row


def _apply(conn: sqlite3.Connection, txn_ids: list[int], merchant: str, category: str) -> None:
    # Upsert the merchant (reuse a human-curated row if the name already exists; never
    # overwrite its category), then link the transactions as an 'llm' assignment.
    conn.execute(
        "INSERT INTO merchants(name, category) VALUES (?, ?) ON CONFLICT(name) DO NOTHING",
        (merchant, category),
    )
    row = conn.execute("SELECT id FROM merchants WHERE name = ?", (merchant,)).fetchone()
    merchant_id = row["id"]
    for txn_id in txn_ids:
        conn.execute(
            "UPDATE transactions SET merchant_id = ?, merchant_source = 'llm' WHERE id = ?",
            (merchant_id, txn_id),
        )

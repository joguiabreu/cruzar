"""Pins plan decision 7: canonical amount serialization makes scale drift
(Decimal("-100.0") vs Decimal("-100.00")) hash identically, so cross-statement
dedup cannot silently miss a duplicate. Not an AC; guards the invariant early.
"""

from __future__ import annotations

from decimal import Decimal

from cruzar.persist import canonical_amount, content_hash


def test_canonical_amount_collapses_scale() -> None:
    assert canonical_amount(Decimal("-100.0"), "EUR") == "-100.00"
    assert canonical_amount(Decimal("-100.00"), "EUR") == "-100.00"
    assert canonical_amount(Decimal("-100.0"), "EUR") == canonical_amount(
        Decimal("-100.00"), "EUR"
    )


def test_content_hash_identical_across_scale_drift() -> None:
    a = canonical_amount(Decimal("-100.0"), "EUR")
    b = canonical_amount(Decimal("-100.00"), "EUR")
    hash_a = content_hash(1, "2026-05-07", a, "TRF P/ Moey", 2)
    hash_b = content_hash(1, "2026-05-07", b, "TRF P/ Moey", 2)
    assert hash_a == hash_b

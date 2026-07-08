"""Anonymizer unit tests (plan 030) — fully offline, with a deterministic fake classifier.

The classifier tier is mocked (offline suite stays offline). These assert the deterministic
guarantees: structure preserved (word count, geometry, separators, date formats) AND no real
source value survives, plus that a mis-classification trips the safety gate.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from pathlib import Path

from cruzar.parsergen import bundle as bundle_mod
from cruzar.parsergen.anonymize import (
    NUM_DATE_RE,
    Classification,
    SemanticType,
    anonymize,
)
from cruzar.parsergen.gates import detect_value_type, run_safety_gate

FIXTURE = Path(__file__).parent / "fixtures" / "parsergen_sample" / "statement.pdf"
_PAYEES = {"ACME", "GLOBEX", "FANTASIA", "XPTO"}


def _semantic(token: str) -> SemanticType:
    if NUM_DATE_RE.match(token):
        return "date"
    if re.search(r"[A-Za-z]", token) and re.search(r"\d", token):
        return "id"
    if re.search(r"\d", token):
        return "amount"
    return "text"


class FakeClassifier:
    """Marks every value-shaped token (and a few known payees) as replace — what a good model
    would do. Deterministic, no network."""

    def classify(
        self, *, text: str, tokens: Sequence[str], feedback: str | None = None
    ) -> list[Classification]:
        out: list[Classification] = []
        for t in tokens:
            # Replace anything carrying a digit (a superset of every value-shaped token the
            # safety gate flags) plus a few known letter payees; keep the rest.
            if re.search(r"\d", t) or t in _PAYEES:
                out.append(Classification(token=t, kind="replace", type=_semantic(t)))
            else:
                out.append(Classification(token=t, kind="keep"))
        return out


class KeepEverythingClassifier:
    """A do-nothing classifier that keeps every token — the deterministic force-replace pass must
    still scrub all value-shaped tokens without the model's help."""

    def classify(
        self, *, text: str, tokens: Sequence[str], feedback: str | None = None
    ) -> list[Classification]:
        return [Classification(token=t, kind="keep") for t in tokens]


def test_bundle_roundtrips_through_dict() -> None:
    src = bundle_mod.extract(FIXTURE)
    assert bundle_mod.from_dict(bundle_mod.to_dict(src)) == src


def test_anonymize_preserves_shape_and_scrubs_values() -> None:
    src = bundle_mod.extract(FIXTURE)
    result = anonymize(src, FakeClassifier(), rng=random.Random(0))

    assert result.fidelity.ok
    assert result.substitutions  # something was replaced

    # No replaced source value survives anywhere in the output.
    anon_texts = {w.text for w in result.bundle.iter_words()}
    for source in result.substitutions:
        assert source not in anon_texts

    # Word count and geometry are preserved verbatim.
    src_words = list(src.iter_words())
    anon_words = list(result.bundle.iter_words())
    assert len(src_words) == len(anon_words)
    for o, a in zip(src_words, anon_words, strict=True):
        assert (o.x0, o.x1, o.top, o.bottom) == (a.x0, a.x1, a.top, a.bottom)
        assert len(o.text) == len(a.text)

    # Row structure is unchanged (same clustered rows count).
    assert len(bundle_mod.rows(src)) == len(bundle_mod.rows(result.bundle))


def test_amounts_keep_locale_shape() -> None:
    src = bundle_mod.extract(FIXTURE)
    result = anonymize(src, FakeClassifier(), rng=random.Random(1))

    # A PT comma-decimal amount is replaced with a same-shaped, different fake.
    fake = result.substitutions["-1.000,00"]
    assert fake != "-1.000,00"
    assert len(fake) == len("-1.000,00")
    assert fake[0] == "-" and fake[2] == "." and fake[6] == ","  # separators in place


def test_dates_stay_valid_and_reformatted() -> None:
    src = bundle_mod.extract(FIXTURE)
    result = anonymize(src, FakeClassifier(), rng=random.Random(2))
    fake = result.substitutions["01/02/2020"]
    assert fake != "01/02/2020"
    assert NUM_DATE_RE.match(fake)
    day, month, year = (int(p) for p in fake.split("/"))
    assert 1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2030


def test_safety_gate_flags_a_kept_value() -> None:
    # Directly probe the gate: with no substitutions, every value-shaped token in the statement
    # is "kept" and must be reported (this is the belt-and-suspenders behind force-replace).
    src = bundle_mod.extract(FIXTURE)
    report = run_safety_gate(src, src, subs={})
    assert not report.ok
    assert report.violations


def test_force_replace_scrubs_values_even_if_model_keeps_everything() -> None:
    # The local model may be weak and miss figures; the deterministic pass still scrubs them.
    src = bundle_mod.extract(FIXTURE)
    result = anonymize(src, KeepEverythingClassifier(), rng=random.Random(0))
    assert result.fidelity.ok
    anon_texts = {w.text for w in result.bundle.iter_words()}
    for source in result.substitutions:
        assert source not in anon_texts
    # Every amount/date in the fixture was replaced deterministically.
    assert "-1.000,00" in result.substitutions
    assert "01/02/2020" in result.substitutions


def test_postal_code_is_a_deterministic_value() -> None:
    # A PT postal code has a shape, so it's scrubbed deterministically — not left to the model.
    assert detect_value_type("1234-567") == "id"
    assert detect_value_type("COMPRA") is None  # a plain label is not a value


def test_denylist_word_is_force_replaced_even_if_model_keeps_it() -> None:
    # A personal name isn't value-shaped, so the model must catch it — but a .pii-denylist entry
    # force-replaces it deterministically, at the word level (a lone name token).
    src = bundle_mod.extract(FIXTURE)
    # "ACME" appears in the fixture as a plain letter token; a do-nothing model keeps it.
    result = anonymize(
        src, KeepEverythingClassifier(), rng=random.Random(0), denylist=["Acme Fakename"]
    )
    assert "ACME" in result.substitutions
    anon_texts = {w.text for w in result.bundle.iter_words()}
    assert "ACME" not in anon_texts

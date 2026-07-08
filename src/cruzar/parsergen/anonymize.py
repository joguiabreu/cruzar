"""Anonymize a layout bundle — the model classifies, Python generates the fakes.

The local model decides only *which* tokens are values and *what kind* (``amount`` / ``date``
/ ``text`` / ``id``); it never invents replacement strings. Python generates a shape-preserving
fake for each — digit→digit, letter→letter, separators and currency untouched, dates kept
valid — so "a comma is a comma" is a Python guarantee, not model compliance. Substitutions are
stable (same source token → same fake everywhere) and applied without moving any geometry.

Orchestration: classify → generate → apply → gates. A **safety** failure is terminal; a
**fidelity** failure is retried with feedback up to ``max_attempts`` (plan 030 D3).
"""

from __future__ import annotations

import random
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from cruzar.parsergen.bundle import LayoutBundle, distinct_tokens, rows
from cruzar.parsergen.gates import (
    FidelityGateError,
    FidelityReport,
    SafetyGateError,
    SafetyReport,
    SemanticType,
    denylist_words,
    detect_value_type,
    norm_token,
    run_fidelity_gate,
    run_safety_gate,
)

TokenKind = Literal["keep", "replace"]

# A fully-numeric date token: three digit groups joined by a single / . or -. Shared with the
# fidelity gate so a generated date is validated against the exact shape it replaced.
NUM_DATE_RE = re.compile(r"^\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}$")

# PT month names (lowercase). A worded date arrives as several tokens; a month-name token is
# remapped to another month name so the sample's dates stay parseable.
_PT_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)

_DIGITS = "0123456789"


@dataclass(frozen=True)
class Classification:
    """One token's label. ``type`` is required (and only meaningful) when ``kind == 'replace'``."""

    token: str
    kind: TokenKind
    type: SemanticType | None = None


class Classifier(Protocol):
    def classify(
        self, *, text: str, tokens: Sequence[str], feedback: str | None = None
    ) -> Sequence[Classification]:
        """Label each token in ``tokens`` (structural ``keep`` vs value ``replace`` + type),
        given the whole statement ``text`` for context. Returns one ``Classification`` per input
        token. ``feedback`` carries a prior fidelity failure so the model can correct."""
        ...


@dataclass(frozen=True)
class AnonymizeResult:
    bundle: LayoutBundle
    substitutions: Mapping[str, str]  # source token -> fake (the safety denylist)
    attempts: int
    fidelity: FidelityReport


def _match_case(template: str, repl: str) -> str:
    if template.isupper():
        return repl.upper()
    if template[:1].isupper():
        return repl.capitalize()
    return repl.lower()


def _mask_digits(token: str, rng: random.Random) -> str:
    return "".join(rng.choice(_DIGITS) if c.isdigit() else c for c in token)


def _mask_letters(token: str, rng: random.Random) -> str:
    out: list[str] = []
    for c in token:
        if c in string.ascii_lowercase:
            out.append(rng.choice(string.ascii_lowercase))
        elif c in string.ascii_uppercase:
            out.append(rng.choice(string.ascii_uppercase))
        else:
            out.append(c)
    return "".join(out)


def _mask_alnum(token: str, rng: random.Random) -> str:
    out: list[str] = []
    for c in token:
        if c.isdigit():
            out.append(rng.choice(_DIGITS))
        elif c in string.ascii_lowercase:
            out.append(rng.choice(string.ascii_lowercase))
        elif c in string.ascii_uppercase:
            out.append(rng.choice(string.ascii_uppercase))
        else:
            out.append(c)
    return "".join(out)


def _fake_date(token: str, rng: random.Random) -> str:
    """A fake but *valid* date in the same format as ``token``. Numeric groups keep their
    width and separators; a 4-digit group is treated as a year, a 1–2 digit group as a
    day/month in a range valid for either. Month-name and bare-number date fragments are
    handled too."""
    m = NUM_DATE_RE.match(token)
    if m is None:
        low = token.lower()
        if low in _PT_MONTHS:
            return _match_case(token, rng.choice(_PT_MONTHS))
        if token.isdigit():
            if len(token) == 4:
                return f"{rng.randint(2000, 2030):04d}"
            hi = 9 if len(token) == 1 else 28
            return f"{rng.randint(1, hi):0{len(token)}d}"
        return _mask_digits(token, rng)

    parts = re.split(r"([/.\-])", token)  # [g0, sep, g1, sep, g2]
    for i in (0, 2, 4):
        width = len(parts[i])
        if width == 4:
            parts[i] = f"{rng.randint(2000, 2030):04d}"
        else:
            hi = 9 if width == 1 else 12  # 1..12 is valid as either day or month
            parts[i] = f"{rng.randint(1, hi):0{width}d}"
    return "".join(parts)


def _generate(token: str, semantic: SemanticType, rng: random.Random) -> str:
    if semantic == "amount":
        return _mask_digits(token, rng)
    if semantic == "date":
        return _fake_date(token, rng)
    if semantic == "id":
        return _mask_alnum(token, rng)
    return _mask_letters(token, rng)


def build_substitutions(
    classifications: Sequence[Classification], rng: random.Random
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(subs, date_fakes)``: the source→fake map for every replaced token, plus the
    subset whose source matched a numeric date (used by the fidelity gate). A token that cannot
    be meaningfully altered (e.g. punctuation-only) is left as ``keep`` rather than mapped to
    itself, so the safety gate never sees a bogus 'survivor'."""
    # Every real value token, so a fake never lands on *another* real value (a collision that
    # would reappear a genuine figure in the output — common with many similar short amounts).
    sources = {c.token for c in classifications if c.kind == "replace"}
    subs: dict[str, str] = {}
    date_fakes: dict[str, str] = {}
    used: set[str] = set()
    for c in classifications:
        if c.kind != "replace" or c.token in subs:
            continue
        semantic: SemanticType = c.type or "text"
        fake = c.token
        for _ in range(32):  # avoid identity, any real value, and a reused fake
            candidate = _generate(c.token, semantic, rng)
            if candidate != c.token and candidate not in sources and candidate not in used:
                fake = candidate
                break
        if fake == c.token or fake in sources:
            continue  # unfakeable within its shape — leave untouched (a value here would be
            # caught by the safety gate rather than silently leaked)
        subs[c.token] = fake
        used.add(fake)
        if semantic == "date" and NUM_DATE_RE.match(c.token):
            date_fakes[c.token] = fake
    return subs, date_fakes


def force_replace_values(
    classifications: Sequence[Classification], deny_words: frozenset[str] = frozenset()
) -> list[Classification]:
    """Override the model with deterministic force-replacements — value scrubbing can't depend on
    a weak model getting every one right:

    - any token that *looks like a value* (amount, date, time, NIF/IBAN, card, long id) → its type;
    - any token matching a ``.pii-denylist`` word (the account holder's own name/address/ids the
      user listed) → ``id`` if it carries a digit, else ``text``.

    The model still contributes ``replace`` for the non-value PII neither rule can see (third-party
    names it recognises)."""
    out: list[Classification] = []
    for c in classifications:
        detected = detect_value_type(c.token)
        if detected is not None:
            out.append(Classification(token=c.token, kind="replace", type=detected))
        elif deny_words and norm_token(c.token) in deny_words:
            kind_type: SemanticType = "id" if any(ch.isdigit() for ch in c.token) else "text"
            out.append(Classification(token=c.token, kind="replace", type=kind_type))
        else:
            out.append(c)
    return out


def apply_substitutions(bundle: LayoutBundle, subs: Mapping[str, str]) -> LayoutBundle:
    """Rewrite word text via ``subs``; geometry is preserved verbatim (fakes are equal-length)."""
    pages = tuple(
        replace(page, words=tuple(replace(w, text=subs.get(w.text, w.text)) for w in page.words))
        for page in bundle.pages
    )
    return LayoutBundle(pages=pages)


def anonymize(
    bundle: LayoutBundle,
    classifier: Classifier,
    *,
    rng: random.Random,
    denylist: Sequence[str] = (),
    max_attempts: int = 3,
) -> AnonymizeResult:
    """Classify → generate → apply → gate. Raises ``SafetyGateError`` (terminal) on any leak,
    or ``FidelityGateError`` if the shape can't be preserved within ``max_attempts``."""
    text = "\n".join(rows(bundle))
    tokens = distinct_tokens(bundle)
    deny_words = denylist_words(denylist)
    feedback: str | None = None
    last_fidelity = FidelityReport(issues=("no attempt ran",))

    for attempt in range(1, max_attempts + 1):
        classifications = classifier.classify(text=text, tokens=tokens, feedback=feedback)
        classifications = force_replace_values(classifications, deny_words)
        subs, date_fakes = build_substitutions(classifications, rng)
        anon = apply_substitutions(bundle, subs)

        safety: SafetyReport = run_safety_gate(bundle, anon, subs, denylist)
        if not safety.ok:
            raise SafetyGateError(safety)  # terminal — never retried into a "good enough" pass

        fidelity = run_fidelity_gate(
            bundle, anon, date_pattern=NUM_DATE_RE, date_fakes=tuple(date_fakes.values())
        )
        if fidelity.ok:
            return AnonymizeResult(
                bundle=anon, substitutions=subs, attempts=attempt, fidelity=fidelity
            )
        last_fidelity = fidelity
        feedback = fidelity.summary()

    raise FidelityGateError(last_fidelity)

"""The two deterministic gates that validate an anonymized bundle.

- **Safety gate** — the hard, no-LLM privacy guarantee. It never trusts a model's judgment:
  it verifies every replaced value is actually gone AND that no *kept* token still looks like a
  real value (the way a mis-classification would leak). Reports positions/lengths only, never
  the value itself. A safety failure is terminal (plan 030 D3).
- **Fidelity gate** — guards that anonymization preserved the statement's *shape* (row count,
  separator histogram, date formats). A fidelity failure is retryable with feedback.

Both depend only on ``bundle`` — never on ``anonymize`` — so there is no import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from cruzar.parsergen.bundle import LayoutBundle

SemanticType = Literal["amount", "date", "text", "id"]

# Deterministic value detectors, most-specific first. A token matching any of these carries real
# data (an amount, date/time, account/card/reference, NIF/IBAN) and MUST be replaced — the
# anonymizer force-replaces on these regardless of the model, and the safety gate flags any that
# were kept. Deliberately does NOT trip on bare 4-digit years or page numbers, which legitimately
# appear in kept headers/footers.
_NUM_DATE_RE = re.compile(r"^\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}$")  # 06/07/2026, 26-07-06
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")  # 14:56, 14:56:07
_POSTAL_RE = re.compile(r"^\d{4}-\d{3}$")  # PT postal code, e.g. 1234-567 (locates a person)
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")  # an email address (identifies a person)
_AMOUNT_RE = re.compile(r"\d[.,]\d")  # 9,99  1.234,56  88,50€
_IDCODE_RE = re.compile(r"[A-Z]{2}\d{2}\d|\*+\d{2,}|\d{5,}")  # NIF/IBAN, masked card, long id run

_SEPARATORS = ",./-:"


def detect_value_type(token: str) -> SemanticType | None:
    """The semantic type of a token that deterministically looks like a real value, or ``None``
    for structural/text tokens. Single source of truth for both the force-replace pass and the
    safety gate, so everything the gate would flag is exactly what gets replaced."""
    if _NUM_DATE_RE.match(token):
        return "date"
    if _TIME_RE.match(token):
        return "amount"  # digit-mask keeps the ':'; a fake time need not be a valid clock time
    if _POSTAL_RE.match(token):
        return "id"  # digit-mask keeps the '-'; scrubs a location that pinpoints someone
    if _EMAIL_RE.search(token):
        return "id"  # alnum-mask keeps '@' and '.'; scrubs a personal email
    if _AMOUNT_RE.search(token):
        return "amount"
    if _IDCODE_RE.search(token):
        return "id"
    return None


def _looks_like_value(text: str) -> bool:
    return detect_value_type(text) is not None


# Common connectors dropped when deriving distinctive words from a denylist phrase, so
# force-replacing them doesn't scramble unrelated addresses/labels.
_DENY_STOP = frozenset({"de", "do", "da", "dos", "das", "e", "the", "of", "av", "rua"})


def norm_token(token: str) -> str:
    """Lowercase and strip surrounding punctuation — for matching a token against denylist words."""
    return re.sub(r"[^0-9a-zàáâãçéêíóôõúñü]", "", token.lower())


def denylist_words(terms: Sequence[str]) -> frozenset[str]:
    """The distinctive individual words in the ``.pii-denylist`` terms — phrases split, common
    connectors and very short words dropped. A full-name term like 'Firstname Middlename Surname'
    thus also protects the lone tokens 'Firstname', 'Surname', which is how a name actually appears
    on a statement: scattered across lines, not as one contiguous phrase."""
    words: set[str] = set()
    for term in terms:
        for raw in re.split(r"\s+", term.strip().lower()):
            word = re.sub(r"[^0-9a-zàáâãçéêíóôõúñü]", "", raw)
            if len(word) >= 3 and word not in _DENY_STOP:
                words.add(word)
    return frozenset(words)


@dataclass(frozen=True)
class SafetyReport:
    """Redacted findings — positions/lengths only, never the offending value (like the PII
    pre-commit guard, this must not echo a secret to make itself heard)."""

    violations: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class FidelityReport:
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        return "; ".join(self.issues)


def run_safety_gate(
    original: LayoutBundle,
    anon: LayoutBundle,
    subs: Mapping[str, str],
    denylist: Sequence[str] = (),
) -> SafetyReport:
    """Verify the anonymized bundle leaks no real value. Never trusts the model."""
    violations: list[str] = []

    # 1. Every replaced source token is actually gone from the output (guards a generation
    #    collision where a fake happened to equal its source).
    anon_texts = {w.text for w in anon.iter_words()}
    for src in subs:
        if src in anon_texts:
            violations.append(f"replaced source token survived (len {len(src)})")

    # 2. No KEPT token still looks like a real value — this is where a value mis-tagged as
    #    ``keep`` would leak. Kept tokens are unchanged, so we read them off the original.
    for word in original.iter_words():
        if word.text in subs:
            continue  # replaced — its fake is checked structurally by the fidelity gate
        if _looks_like_value(word.text):
            violations.append(f"kept token looks like a value (len {len(word.text)})")

    # 3. Belt-and-suspenders: no known-distinctive real value (the gitignored .pii-denylist)
    #    survives anywhere — as a whole phrase (space-insensitive, mirroring the pre-commit guard)
    #    OR as one of its distinctive words (a lone name token, how a name really appears).
    if denylist:
        blob = " ".join(w.text for w in anon.iter_words())
        nblob = blob.replace(" ", "").lower()
        phrase_hit = any(
            term in blob or (nterm and nterm in nblob)
            for term in denylist
            for nterm in (term.replace(" ", "").lower(),)
        )
        deny_words = denylist_words(denylist)
        word_hit = any(norm_token(w.text) in deny_words for w in anon.iter_words())
        if phrase_hit or word_hit:
            violations.append("a .pii-denylist value survived in the output")

    return SafetyReport(violations=tuple(violations))


def _sep_histogram(bundle: LayoutBundle) -> dict[str, int]:
    counts = dict.fromkeys(_SEPARATORS, 0)
    for word in bundle.iter_words():
        for ch in word.text:
            if ch in counts:
                counts[ch] += 1
    return counts


def run_fidelity_gate(
    original: LayoutBundle,
    anon: LayoutBundle,
    date_pattern: re.Pattern[str] | None = None,
    date_fakes: Sequence[str] = (),
) -> FidelityReport:
    """Verify anonymization preserved the statement's shape.

    ``date_fakes`` are the generated replacements for date-typed tokens that matched
    ``date_pattern`` in the source; each must still match it, so a parser's date regex works on
    the sample exactly as it would on the original.
    """
    issues: list[str] = []

    orig_words = list(original.iter_words())
    anon_words = list(anon.iter_words())
    if len(orig_words) != len(anon_words):
        issues.append(f"word count changed ({len(orig_words)} -> {len(anon_words)})")
    else:
        for o, a in zip(orig_words, anon_words, strict=True):
            if (o.x0, o.x1, o.top, o.bottom) != (a.x0, a.x1, a.top, a.bottom):
                issues.append("word geometry moved")
                break
            if len(o.text) != len(a.text):
                issues.append("token length changed")
                break

    if _sep_histogram(original) != _sep_histogram(anon):
        issues.append("separator histogram changed (a comma/dot/slash was altered)")

    if date_pattern is not None:
        for fake in date_fakes:
            if not date_pattern.fullmatch(fake):
                issues.append("a date replacement no longer matches the source date format")
                break

    return FidelityReport(issues=tuple(issues))


class SafetyGateError(Exception):
    """Raised when the safety gate finds a leak. Terminal — never retried (plan 030 D3)."""

    def __init__(self, report: SafetyReport) -> None:
        super().__init__("anonymization safety gate failed: " + "; ".join(report.violations))
        self.report = report


class FidelityGateError(Exception):
    """Raised when fidelity retries are exhausted."""

    def __init__(self, report: FidelityReport) -> None:
        super().__init__("anonymization fidelity gate failed: " + report.summary())
        self.report = report

"""Measure the LLM categorization tier's accuracy against YOUR labeled examples.

Model choice (e.g. a 4B vs a 9B) should be data-driven, not vibes — this runs your
own labeled descriptions through the live local model and reports how often it picks
the category you expected. It hits Ollama, so it is deliberately NOT part of the
pytest suite (which stays offline with fakes). Run it by hand:

    uv run python scripts/eval_categorization.py [path/to/labels.csv]

The labels file is your REAL data, so it lives under the gitignored ``data/``
(default: ``data/eval/categorization.csv``). It's a CSV with a header and
``description,expected_category`` rows (obviously-fake examples):

    description,expected_category
    COMPRA 0421 EXAMPLE GROCER LX,Groceries
    PAG EXAMPLE TRANSIT,Transport

Only the LLM tier is exercised (the rule/cache tiers are bypassed) so the number
reflects the model itself. The category vocabulary comes from config/categories.yaml.

Besides overall accuracy it reports **junk-drawer false positives**: how often a
merchant whose expected category is NOT a fee/tax bucket got filed under one anyway
(the "Fees & Charges"/"Taxes" catch-all failure the merchants review exposed). When
A/B-testing a prompt change, that number is the one to drive down without losing
accuracy.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from cruzar.categorize import LlmError, LlmUnavailable
from cruzar.config import load_config

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_LABELS = _ROOT / "data" / "eval" / "categorization.csv"


def _is_junk_drawer(category: str) -> bool:
    """The fee/tax catch-all buckets a non-fee/non-tax merchant should never land in.
    Matched by name so it tracks config/categories.yaml without a hardcoded list."""
    name = category.lower()
    return "fee" in name or "tax" in name


def main(argv: list[str]) -> int:
    labels_path = Path(argv[1]) if len(argv) > 1 else _DEFAULT_LABELS
    if not labels_path.exists():
        print(f"No labels file at {labels_path}. Create it — see this script's docstring.")
        return 1

    config = load_config(_ROOT / "config")
    if not config.llm.enabled:
        print("llm.enabled is false in config/cruzar.yaml — enable it and start Ollama.")
        return 1

    from cruzar.llm import ollama_categorizer

    categorizer = ollama_categorizer(config.llm.model, config.llm.host, config.llm.timeout)
    with labels_path.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("description") or "").strip()]
    if not rows:
        print(f"{labels_path} has no rows.")
        return 1

    hits = 0
    misses: list[tuple[str, str, str, float]] = []
    junk_fps: list[tuple[str, str, str, float]] = []
    for row in rows:
        description = (row.get("description") or "").strip()
        expected = (row.get("expected_category") or "").strip()
        try:
            proposal = categorizer.propose(description, config.categories)
            got, confidence = proposal.category, proposal.confidence
        except LlmUnavailable as exc:
            print(f"Ollama unreachable ({exc}). Is it running at {config.llm.host}?")
            return 1
        except LlmError as exc:
            got, confidence = f"<error: {exc}>", 0.0
        if got.lower() == expected.lower():
            hits += 1
        else:
            misses.append((description, expected, got, confidence))
        # A normal merchant (expected NOT a fee/tax bucket) filed under one anyway.
        if _is_junk_drawer(got) and not _is_junk_drawer(expected):
            junk_fps.append((description, expected, got, confidence))

    total = len(rows)
    print(f"model: {config.llm.model}   examples: {total}   accuracy: {hits}/{total} = {hits / total:.1%}")
    print(f"junk-drawer false positives (non-fee/tax filed under Fees/Taxes): {len(junk_fps)}/{total}")
    if junk_fps:
        print("\njunk-drawer FPs  (description | expected | got | confidence):")
        for description, expected, got, confidence in junk_fps:
            print(f"  {description!r} | {expected} | {got} | {confidence:.2f}")
    if misses:
        print("\nmisses  (description | expected | got | confidence):")
        for description, expected, got, confidence in misses:
            print(f"  {description!r} | {expected} | {got} | {confidence:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

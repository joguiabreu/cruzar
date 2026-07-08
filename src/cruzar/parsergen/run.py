"""Operator entry point for ``cruzar anonymize``: turn a real statement PDF into a gitignored,
privacy-safe layout bundle for parser development.

Purely local and fail-loud: if the safety gate finds a leak, ``anonymize`` raises and this
writes nothing. Output is a dev aid under gitignored ``data/`` — never a committed fixture
(plan 030 D2). Sending it anywhere is a separate, explicit step (plan 029).
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

from cruzar.parsergen import bundle as _bundle
from cruzar.parsergen.anonymize import Classifier, anonymize

logger = logging.getLogger(__name__)


def load_denylist(repo_root: Path) -> list[str]:
    """The gitignored ``.pii-denylist`` terms (comments/blank lines skipped), or empty if none.
    Feeds the safety gate's belt-and-suspenders scan."""
    path = repo_root / ".pii-denylist"
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


@dataclass(frozen=True)
class AnonymizeSummary:
    bundle_path: Path
    report_path: Path
    words: int
    replaced: int
    attempts: int


def anonymize_file(
    pdf_path: str | Path,
    out_dir: str | Path,
    classifier: Classifier,
    *,
    repo_root: Path,
    seed: int = 0,
) -> AnonymizeSummary:
    """Anonymize ``pdf_path`` and write ``sample.layout.json`` + ``gate_report.txt`` under
    ``out_dir``. Raises ``SafetyGateError``/``FidelityGateError`` (writing nothing) on failure."""
    source = _bundle.extract(pdf_path)
    denylist = load_denylist(repo_root)
    if not denylist:
        logger.warning(
            "no .pii-denylist entries — a personal NAME/address is not value-shaped, so it relies "
            "on the model alone and may survive. Add your name, address, and account numbers to "
            "the gitignored .pii-denylist for deterministic scrubbing, then re-run."
        )
    result = anonymize(source, classifier, rng=random.Random(seed), denylist=denylist)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bundle_path = out / "sample.layout.json"
    bundle_path.write_text(
        json.dumps(_bundle.to_dict(result.bundle), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    words = sum(len(p.words) for p in result.bundle.pages)
    report_path = out / "gate_report.txt"
    report_path.write_text(
        "anonymization gate report\n"
        f"words: {words}\n"
        f"tokens replaced: {len(result.substitutions)}\n"
        f"attempts: {result.attempts}\n"
        "safety gate: PASSED (no real value survives)\n"
        "fidelity gate: PASSED (row count / separators / date formats preserved)\n",
        encoding="utf-8",
    )
    return AnonymizeSummary(
        bundle_path=bundle_path,
        report_path=report_path,
        words=words,
        replaced=len(result.substitutions),
        attempts=result.attempts,
    )

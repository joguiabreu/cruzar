"""Shared test fixtures: a fresh temp DB + temp inbox + temp config per test.
Tests never touch a real DB (CLAUDE testing conventions).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
_STATEMENT_PDF = FIXTURES / "activobank" / "statement.pdf"

_SOURCES_YAML = """\
accounts:
  - institution: activobank
    name: Conta Simples
    account_match: activobank
    source_type: manual
    account_type: checking
    currency: EUR
"""

_CRUZAR_YAML = "base_currency: EUR\nllm_model: qwen3:8b\n"

_CATEGORIES_YAML = """\
categories:
  - Transfer
  - Subscriptions
  - Other
"""

_MERCHANTS_YAML = """\
merchants:
  - name: Spotify
    category: Subscriptions
    patterns:
      - pattern: "Spotify"
        priority: 100
"""


@pytest.fixture
def statement_pdf() -> Path:
    return _STATEMENT_PDF


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "sources.yaml").write_text(_SOURCES_YAML, encoding="utf-8")
    (cfg / "cruzar.yaml").write_text(_CRUZAR_YAML, encoding="utf-8")
    (cfg / "categories.yaml").write_text(_CATEGORIES_YAML, encoding="utf-8")
    (cfg / "merchants.yaml").write_text(_MERCHANTS_YAML, encoding="utf-8")
    return cfg


@pytest.fixture
def inbox_dir(tmp_path: Path) -> Path:
    """An inbox containing the ActivoBank fixture under its account folder."""
    inbox = tmp_path / "inbox"
    (inbox / "activobank").mkdir(parents=True)
    shutil.copy(_STATEMENT_PDF, inbox / "activobank" / "statement.pdf")
    return inbox


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cruzar.db"


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    return tmp_path / "reports"

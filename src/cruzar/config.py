"""Load the yaml config inputs (SPEC §Inputs). These are editable inputs seeded
into SQLite each run; SQLite remains the source of truth (ADR-3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AccountConfig:
    institution: str
    name: str
    account_match: str
    source_type: str
    account_type: str
    currency: str
    # ADR-14: false when the institution's parser cannot emit cash-flow
    # transactions (e.g. IB's monthly summary), so contributions are undetectable
    # and Portfolio Δ degrades to gross. Defaults true (most parsers are capable).
    emits_cash_flows: bool = True


@dataclass(frozen=True)
class PatternConfig:
    pattern: str
    priority: int


@dataclass(frozen=True)
class MerchantConfig:
    name: str
    category: str
    patterns: list[PatternConfig]


@dataclass(frozen=True)
class ManualRate:
    """An optional hand-supplied FX rate (base EUR), seeded into fx_rates."""

    date: str  # ISO YYYY-MM-DD
    quote: str  # ISO 4217
    rate: Decimal  # units of quote per 1 EUR


@dataclass(frozen=True)
class LlmConfig:
    """Local LLM (Ollama) settings for categorization (ADR-2/13). ``enabled=False``
    keeps the run fully offline (rule-only, zero calls)."""

    enabled: bool = True
    model: str = "qwen3:8b"
    host: str = "http://localhost:11434"
    min_confidence: float = 0.7  # below this a proposal is needs_review, not applied
    timeout: float = 60.0  # per-request seconds; a down service fails fast regardless


@dataclass(frozen=True)
class Config:
    base_currency: str
    accounts: list[AccountConfig]
    categories: list[str]
    merchants: list[MerchantConfig]
    transfer_patterns: list[str]  # is_transfer step 1 (ADR-15), from flows.yaml
    investment_flow_patterns: list[str]  # external contributions (ADR-14), from flows.yaml
    fx_rates: list[ManualRate]  # optional hand-supplied rates from fx_rates.yaml
    # FX provider settings let a fully-offline user disable fetching or supply an
    # exchangerate.host key (ADR-5).
    fx_offline: bool = False
    fx_access_key: str | None = None
    fx_timeout: float = 10.0
    llm: LlmConfig = field(default_factory=LlmConfig)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}  # optional config (e.g. flows.yaml) — absent means "no rules"
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


def load_config(config_dir: str | Path) -> Config:
    config_dir = Path(config_dir)
    app = _load_yaml(config_dir / "cruzar.yaml")
    sources = _load_yaml(config_dir / "sources.yaml")
    categories_doc = _load_yaml(config_dir / "categories.yaml")
    merchants_doc = _load_yaml(config_dir / "merchants.yaml")
    flows_doc = _load_yaml(config_dir / "flows.yaml")
    fx_doc = _load_yaml(config_dir / "fx_rates.yaml")
    fx_settings: dict[str, Any] = app.get("fx") or {}
    llm_settings: dict[str, Any] = app.get("llm") or {}
    llm = LlmConfig(
        enabled=bool(llm_settings.get("enabled", True)),
        # Back-compat: fall back to the legacy top-level `llm_model` if no `llm:` block.
        model=str(llm_settings.get("model", app.get("llm_model", "qwen3:8b"))),
        host=str(llm_settings.get("host", "http://localhost:11434")),
        min_confidence=float(llm_settings.get("min_confidence", 0.7)),
        timeout=float(llm_settings.get("timeout_seconds", 60.0)),
    )

    accounts = [AccountConfig(**entry) for entry in sources.get("accounts", [])]
    fx_rate_rows: list[Any] = fx_doc.get("rates") or []
    fx_rates = [
        ManualRate(
            date=str(r["date"]),
            quote=str(r["quote"]).upper(),
            rate=Decimal(str(r["rate"])),
        )
        for r in fx_rate_rows
    ]
    merchants = [
        MerchantConfig(
            name=m["name"],
            category=m["category"],
            patterns=[PatternConfig(**p) for p in m.get("patterns", [])],
        )
        for m in merchants_doc.get("merchants", [])
    ]
    return Config(
        base_currency=app.get("base_currency", "EUR"),
        accounts=accounts,
        categories=list(categories_doc.get("categories", [])),
        merchants=merchants,
        transfer_patterns=list(flows_doc.get("transfer_patterns", [])),
        investment_flow_patterns=list(flows_doc.get("investment_flow_patterns", [])),
        fx_rates=fx_rates,
        fx_offline=bool(fx_settings.get("offline", False)),
        fx_access_key=fx_settings.get("access_key"),
        fx_timeout=float(fx_settings.get("timeout_seconds", 10.0)),
        llm=llm,
    )

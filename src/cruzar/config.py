"""Load the yaml config inputs (SPEC §Inputs). These are editable inputs seeded
into SQLite each run; SQLite remains the source of truth (ADR-3).
"""

from __future__ import annotations

from dataclasses import dataclass
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
class Config:
    base_currency: str
    accounts: list[AccountConfig]
    categories: list[str]
    merchants: list[MerchantConfig]
    transfer_patterns: list[str]  # is_transfer step 1 (ADR-15), from flows.yaml
    fx_rates: list[ManualRate]  # optional hand-supplied rates from fx_rates.yaml
    # FX provider settings let a fully-offline user disable fetching or supply an
    # exchangerate.host key (ADR-5).
    fx_offline: bool = False
    fx_access_key: str | None = None
    fx_timeout: float = 10.0


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
        fx_rates=fx_rates,
        fx_offline=bool(fx_settings.get("offline", False)),
        fx_access_key=fx_settings.get("access_key"),
        fx_timeout=float(fx_settings.get("timeout_seconds", 10.0)),
    )

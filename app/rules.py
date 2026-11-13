"""Business rules, loaded once from config/business_rules.toml into typed objects.

Code holds no business constants (specs/01_data_contract.md, section 11). Prices,
FX rates, mapping tables, allowed values and the status order all come from the
file. A missing or malformed key stops the run with a message that names the key.
"""
from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

FX_MODES: tuple[str, ...] = ("multiply", "divide")   # R17, decision D1
_BILLED_MONTHS_SUFFIX = "_billed_months"


class RulesError(Exception):
    """The business rules file is missing, unreadable or incomplete."""


@dataclass(frozen=True)
class Rules:
    prices_usd: Mapping[str, int]             # R18: plan -> list price per seat per month
    billed_months: Mapping[str, int]          # R18: billing cycle -> months billed per invoice
    fx_mode: str                              # R17: "multiply" or "divide"
    fx_rate_to_usd: Mapping[str, Decimal]     # R17: currency -> rate
    currency_symbols: Mapping[str, str]       # R12: symbol -> currency
    regions: tuple[str, ...]                  # R8
    currencies: tuple[str, ...]               # R8
    plan_map: Mapping[str, str]               # R4, keys lower case
    billing_cycle_map: Mapping[str, str]      # R5
    status_map: Mapping[str, str]             # R6
    payment_method_map: Mapping[str, str]     # R7
    boolean_map: Mapping[str, bool]           # R8
    status_order: tuple[str, ...]             # R16c: first status present wins
    as_of_date: date | None                   # R20: None means the day the pipeline runs


def _get(data: Mapping[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise RulesError(f"Business rules: key [{dotted}] is missing.")
        node = node[part]
    return node


def _table(data: Mapping[str, Any], dotted: str) -> Mapping[str, Any]:
    value = _get(data, dotted)
    if not isinstance(value, Mapping) or not value:
        raise RulesError(f"Business rules: [{dotted}] must be a table with at least one entry.")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RulesError(f"Business rules: {where} must be a non-empty text value, got {value!r}.")
    return value.strip()


def _whole(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RulesError(f"Business rules: {where} must be a whole number above 0, got {value!r}.")
    return value


def _rate(value: Any, where: str) -> Decimal:
    # Rates are written in quotes so they never pass through a binary float.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise RulesError(f"Business rules: {where} must be written in quotes, for example \"1.08\", got {value!r}.")
    try:
        rate = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise RulesError(f"Business rules: {where} is not a number: {value!r}.") from exc
    if not rate.is_finite() or rate <= 0:
        raise RulesError(f"Business rules: {where} must be above 0, got {value!r}.")
    return rate


def _text_map(data: Mapping[str, Any], dotted: str) -> Mapping[str, str]:
    table = _table(data, dotted)
    return MappingProxyType({k.strip().lower(): _text(v, f"[{dotted}] {k!r}") for k, v in table.items()})


def _text_list(data: Mapping[str, Any], dotted: str) -> tuple[str, ...]:
    value = _get(data, dotted)
    if not isinstance(value, list) or not value:
        raise RulesError(f"Business rules: [{dotted}] must be a list with at least one entry.")
    return tuple(_text(item, f"[{dotted}] entry") for item in value)


def _billed_months(data: Mapping[str, Any]) -> Mapping[str, int]:
    table = _table(data, "billing")
    months: dict[str, int] = {}
    for key, value in table.items():
        if not key.endswith(_BILLED_MONTHS_SUFFIX):
            raise RulesError(f"Business rules: [billing] key {key!r} must end with {_BILLED_MONTHS_SUFFIX!r}.")
        months[key[: -len(_BILLED_MONTHS_SUFFIX)]] = _whole(value, f"[billing] {key}")
    return MappingProxyType(months)


def _boolean_map(data: Mapping[str, Any]) -> Mapping[str, bool]:
    table = _table(data, "mappings.boolean")
    for key, value in table.items():
        if not isinstance(value, bool):
            raise RulesError(f"Business rules: [mappings.boolean] {key!r} must be true or false, got {value!r}.")
    return MappingProxyType({k.strip().lower(): v for k, v in table.items()})


def _as_of(data: Mapping[str, Any]) -> date | None:
    run = data.get("run", {})
    value = run.get("as_of_date") if isinstance(run, Mapping) else None
    if value is None:
        return None
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise RulesError(f"Business rules: [run] as_of_date must look like 2026-11-12, got {value!r}.") from exc


def _fx_mode(data: Mapping[str, Any]) -> str:
    mode = _text(_get(data, "fx.mode"), "[fx] mode")
    if mode not in FX_MODES:
        raise RulesError(f"Business rules: [fx] mode must be one of {', '.join(FX_MODES)}, got {mode!r}.")
    return mode


def _need_all(wanted: set[str], have: set[str], what: str) -> None:
    missing = sorted(wanted - have)
    if missing:
        raise RulesError(f"Business rules: {what} is missing for {', '.join(missing)}.")


def _cross_check(rules: Rules) -> None:
    """Every value a mapping can produce must have what the later rules need."""
    _need_all(set(rules.plan_map.values()), set(rules.prices_usd), "a list price in [prices_usd_per_seat_month]")
    _need_all(set(rules.billing_cycle_map.values()), set(rules.billed_months), "a billed months entry in [billing]")
    _need_all(set(rules.currencies), set(rules.fx_rate_to_usd), "a rate in [fx.rate_to_usd]")
    _need_all(set(rules.currency_symbols.values()), set(rules.currencies), "an entry in [allowed] currencies")
    _need_all(set(rules.status_map.values()), set(rules.status_order), "a place in [duplicates] status_order")
    if len(set(rules.status_order)) != len(rules.status_order):
        raise RulesError("Business rules: [duplicates] status_order lists a status twice.")


def rules_from_mapping(data: Mapping[str, Any]) -> Rules:
    """Build `Rules` from parsed TOML. Raises RulesError naming the first bad key."""
    prices = _table(data, "prices_usd_per_seat_month")
    rates = _table(data, "fx.rate_to_usd")
    symbols = _table(data, "currency_symbols")
    rules = Rules(
        prices_usd=MappingProxyType({_text(k, "plan name"): _whole(v, f"price of {k}") for k, v in prices.items()}),
        billed_months=_billed_months(data),
        fx_mode=_fx_mode(data),
        fx_rate_to_usd=MappingProxyType(
            {k.strip().upper(): _rate(v, f"[fx.rate_to_usd] {k}") for k, v in rates.items()}),
        currency_symbols=MappingProxyType(
            {_text(k, "currency symbol"): _text(v, f"[currency_symbols] {k!r}").upper() for k, v in symbols.items()}),
        regions=tuple(r.upper() for r in _text_list(data, "allowed.regions")),
        currencies=tuple(c.upper() for c in _text_list(data, "allowed.currencies")),
        plan_map=_text_map(data, "mappings.plan"),
        billing_cycle_map=_text_map(data, "mappings.billing_cycle"),
        status_map=_text_map(data, "mappings.status"),
        payment_method_map=_text_map(data, "mappings.payment_method"),
        boolean_map=_boolean_map(data),
        status_order=_text_list(data, "duplicates.status_order"),
        as_of_date=_as_of(data),
    )
    for symbol in rules.currency_symbols:
        if len(symbol) != 1:
            raise RulesError(f"Business rules: currency symbol {symbol!r} must be one character.")
    _cross_check(rules)
    return rules


def load_rules(path: Path) -> Rules:
    """Read the business rules file. Raises RulesError with a plain reason."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise RulesError(f"Business rules file not found at {path}.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(f"Business rules file {path} is not valid TOML: {exc}") from exc
    return rules_from_mapping(data)

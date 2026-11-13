"""Clean one raw CSV row (R1 to R12, R14, R15 of specs/01_data_contract.md).

`clean_row` returns a `CleanRow`, or a `Reject` carrying the reason of the first
rule the row breaks, in rule order. Nothing is guessed: a value that no rule
covers rejects the row.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.rules import Rules

COLUMNS: tuple[str, ...] = (
    "invoice_id", "account_id", "account_name", "contact_email", "region", "industry", "plan",
    "billing_cycle", "seats", "currency", "amount", "discount_pct", "status", "payment_method",
    "signup_date", "invoice_date", "churned", "csat_score", "support_tickets",
)
OPTIONAL_COLUMNS: frozenset[str] = frozenset({"contact_email", "industry", "payment_method", "csat_score"})  # R2

_INVOICE_ID = re.compile(r"INV-\d{6}")                     # R3
_ACCOUNT_ID = re.compile(r"ACC-\d{4}")                     # R3
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")           # R9
_EMAIL_AT = "_at_"                                         # R9
_WHOLE = re.compile(r"[0-9]+")                             # R10: digits only
_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")                # R10
_DATE_YEAR_FIRST = re.compile(r"(\d{4})([-/])(\d{2})\2(\d{2})")   # R11
_DATE_SLASH = re.compile(r"(\d{2})/(\d{2})/(\d{4})")       # R11: month first
_DATE_DASH = re.compile(r"(\d{2})-(\d{2})-(\d{4})")        # R11: day first
_DATE_NAMED = re.compile(r"([A-Za-z]{3}) (\d{1,2}) (\d{4})")      # R11
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_INT32_MAX = 2**31 - 1               # storage limit of an INTEGER column
_MAX_AMOUNT_DIGITS = 12              # storage limit of DECIMAL(14,2)
_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)
_CSAT_RANGE = (1, 5)                 # R10
_OFF_FORMULA_FROM = Decimal("1.00")  # R15


@dataclass(frozen=True)
class CleanRow:
    """One raw row after cleaning. Column names follow specs/01, section 3."""
    source_row: int                  # CSV line number, the header is line 1 (R1)
    raw: tuple[str, ...]             # the 19 raw fields as read, untrimmed
    raw_status: str                  # trimmed raw status text, for dq_duplicates
    invoice_id: str
    account_id: str
    account_name: str
    contact_email: str | None
    region: str
    industry: str | None
    plan: str
    billing_cycle: str
    seats: int
    currency: str
    amount_local: Decimal
    discount_pct: Decimal
    status: str
    payment_method: str | None
    signup_date: date
    invoice_date: date
    churned: bool
    csat_score: int | None
    support_tickets: int
    dq_email_repaired: bool          # R9
    dq_missing_cents: bool           # R14
    dq_amount_off_formula: bool      # R15
    dq_invoice_date_ambiguous: bool  # R11
    dq_signup_date_ambiguous: bool   # R11


@dataclass(frozen=True)
class Reject:
    """A raw row that could not be cleaned (specs/01, section 3.5)."""
    source_row: int
    invoice_id: str | None
    reason: str
    detail: str
    raw_record: str                  # the raw row as JSON


class _Stop(Exception):
    """Raised by a rule to reject the row."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def raw_record_json(fields: list[str] | tuple[str, ...]) -> str:
    """The raw row as JSON: by column name when it has 19 fields, as a list otherwise."""
    if len(fields) == len(COLUMNS):
        return json.dumps(dict(zip(COLUMNS, fields)), ensure_ascii=False)
    return json.dumps({"fields": list(fields)}, ensure_ascii=False)


def _lookup(value: str, table, reason: str, label: str):
    key = value.lower()
    if key not in table:
        raise _Stop(reason, f"{label} {value!r} is not in the mapping table")
    return table[key]


def _check_required(row: dict[str, str]) -> None:
    """R2: every column except the four optional ones needs a value."""
    blank = [name for name in COLUMNS if name not in OPTIONAL_COLUMNS and not row[name]]
    if blank:
        raise _Stop("missing_required", f"blank required field: {', '.join(blank)}")


def _check_keys(row: dict[str, str]) -> None:
    """R3."""
    if not _INVOICE_ID.fullmatch(row["invoice_id"]):
        raise _Stop("bad_key", f"invoice_id {row['invoice_id']!r} is not INV- plus 6 digits")
    if not _ACCOUNT_ID.fullmatch(row["account_id"]):
        raise _Stop("bad_key", f"account_id {row['account_id']!r} is not ACC- plus 4 digits")


def _payment_method(value: str, rules: Rules) -> str | None:
    """R7: blank is NULL."""
    if not value:
        return None
    return _lookup(value, rules.payment_method_map, "unknown_payment_method", "payment method")


def _allowed(value: str, allowed: tuple[str, ...], reason: str, label: str) -> str:
    """R8: upper-case, then it must be on the list."""
    upper = value.upper()
    if upper not in allowed:
        raise _Stop(reason, f"{label} {value!r} is not one of {', '.join(allowed)}")
    return upper


def clean_email(value: str) -> tuple[str | None, bool]:
    """R9: lower case, repair `_at_`, NULL when blank or invalid. Never rejects."""
    lowered = value.lower()
    repaired = _EMAIL_AT in lowered
    email = lowered.replace(_EMAIL_AT, "@")
    if not email or not _EMAIL.fullmatch(email):
        return None, repaired
    return email, repaired


def _whole_number(value: str, label: str, low: int, high: int = _INT32_MAX) -> int:
    """R10: digits only, no sign, no decimal point."""
    if not _WHOLE.fullmatch(value):
        raise _Stop("bad_number", f"{label} {value!r} is not a whole number")
    number = int(value)
    if not low <= number <= high:
        raise _Stop("bad_number", f"{label} {value!r} is outside {low} to {high}")
    return number


def _discount(value: str) -> Decimal:
    """R10: digits with an optional decimal part, from 0 to 100."""
    if not _DECIMAL.fullmatch(value):
        raise _Stop("bad_number", f"discount_pct {value!r} is not a plain number")
    number = Decimal(value)
    if number > _HUNDRED:
        raise _Stop("bad_number", f"discount_pct {value!r} is outside 0 to 100")
    if number != number.quantize(_CENT):
        # The column is DECIMAL(5,2). Rounding a discount would be a guess.
        raise _Stop("bad_number", f"discount_pct {value!r} has more than two decimals")
    return number.quantize(_CENT)


def _csat(value: str) -> int | None:
    """R10: blank or a whole number from 1 to 5."""
    if not value:
        return None
    return _whole_number(value, "csat_score", _CSAT_RANGE[0], _CSAT_RANGE[1])


def _real_date(year: int, month: int, day: int, value: str, label: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise _Stop("bad_date", f"{label} {value!r} is not a real calendar date") from exc


def parse_date(value: str, label: str = "date") -> tuple[date, bool]:
    """R11: returns the date and whether its day and month could be swapped."""
    if m := _DATE_YEAR_FIRST.fullmatch(value):
        return _real_date(int(m[1]), int(m[3]), int(m[4]), value, label), False
    if m := _DATE_SLASH.fullmatch(value):      # NN/NN/YYYY is month first
        month, day, year = int(m[1]), int(m[2]), int(m[3])
        return _real_date(year, month, day, value, label), _ambiguous(month, day)
    if m := _DATE_DASH.fullmatch(value):       # NN-NN-YYYY is day first
        day, month, year = int(m[1]), int(m[2]), int(m[3])
        return _real_date(year, month, day, value, label), _ambiguous(day, month)
    if m := _DATE_NAMED.fullmatch(value):
        name = m[1].lower()
        if name not in _MONTHS:
            raise _Stop("bad_date", f"{label} {value!r} has an unknown month name")
        return _real_date(int(m[3]), _MONTHS.index(name) + 1, int(m[2]), value, label), False
    raise _Stop("bad_date", f"{label} {value!r} is not in one of the five accepted formats")


def _ambiguous(first: int, second: int) -> bool:
    """R11: both numbers are 12 or less and different."""
    return first <= 12 and second <= 12 and first != second


def _amount_pattern(rules: Rules) -> re.Pattern[str]:
    symbols = "".join(re.escape(s) for s in rules.currency_symbols)
    return re.compile(
        rf"(?P<m1>-)?(?P<symbol>[{symbols}])?(?P<m2>-)?"
        r"(?P<digits>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
        r"(?P<decimals>\.[0-9]{1,2})?"
        r"(?: (?P<code>[A-Za-z]{3}))?"
    )


def parse_amount(value: str, currency: str, rules: Rules) -> Decimal:
    """R12: the amount in `currency`, two decimals, negative when a minus sign appears."""
    m = _amount_pattern(rules).fullmatch(value)
    if not m:
        raise _Stop("bad_amount", f"amount {value!r} is not in an accepted shape")
    symbol, code = m["symbol"], m["code"]
    if symbol and rules.currency_symbols[symbol] != currency:
        raise _Stop("currency_mismatch", f"amount {value!r} carries {symbol} but the currency column says {currency}")
    if code and code.upper() != currency:
        raise _Stop("currency_mismatch", f"amount {value!r} carries {code} but the currency column says {currency}")
    digits = m["digits"].replace(",", "")
    if len(digits.lstrip("0")) > _MAX_AMOUNT_DIGITS:
        raise _Stop("bad_amount", f"amount {value!r} is too large to store")
    amount = Decimal(digits + (m["decimals"] or "")).quantize(_CENT)
    negative = bool(m["m1"] or m["m2"]) and amount != 0
    return -amount if negative else amount


def expected_local(plan: str, billing_cycle: str, seats: int, discount_pct: Decimal,
                   currency: str, rules: Rules) -> Decimal:
    """R14: what the price list says the invoice should be, in local currency, unrounded.

    Always multiplies by the rate, whatever fx_mode says, because it describes how
    the export was produced.
    """
    monthly = Decimal(rules.prices_usd[plan]) * seats * (1 - discount_pct / _HUNDRED)
    return monthly * rules.billed_months[billing_cycle] * rules.fx_rate_to_usd[currency]


def _is_whole(value: Decimal) -> bool:
    return value == value.to_integral_value()


def clean_fields(row: dict[str, str], source_row: int, raw: tuple[str, ...], rules: Rules) -> CleanRow:
    """Apply R2 to R15 to trimmed fields. Raises _Stop at the first rule that fails."""
    _check_required(row)                                                                    # R2
    _check_keys(row)                                                                        # R3
    plan = _lookup(row["plan"], rules.plan_map, "unknown_plan", "plan")                     # R4
    cycle = _lookup(row["billing_cycle"], rules.billing_cycle_map,
                    "unknown_billing_cycle", "billing cycle")                               # R5
    status = _lookup(row["status"], rules.status_map, "unknown_status", "status")           # R6
    payment_method = _payment_method(row["payment_method"], rules)                          # R7
    region = _allowed(row["region"], rules.regions, "unknown_region", "region")             # R8
    currency = _allowed(row["currency"], rules.currencies, "unknown_currency", "currency")  # R8
    churned = _lookup(row["churned"], rules.boolean_map, "unknown_boolean", "churned")      # R8
    email, email_repaired = clean_email(row["contact_email"])                               # R9
    seats = _whole_number(row["seats"], "seats", 1)                                         # R10
    discount = _discount(row["discount_pct"])                                               # R10
    csat = _csat(row["csat_score"])                                                         # R10
    tickets = _whole_number(row["support_tickets"], "support_tickets", 0)                   # R10
    signup, signup_ambiguous = parse_date(row["signup_date"], "signup_date")                # R11
    invoiced, invoice_ambiguous = parse_date(row["invoice_date"], "invoice_date")           # R11
    amount = parse_amount(row["amount"], currency, rules)                                   # R12
    expected = expected_local(plan, cycle, seats, discount, currency, rules)                # R14
    return CleanRow(
        source_row=source_row, raw=raw, raw_status=row["status"],
        invoice_id=row["invoice_id"], account_id=row["account_id"], account_name=row["account_name"],
        contact_email=email, region=region, industry=row["industry"] or None, plan=plan,
        billing_cycle=cycle, seats=seats, currency=currency, amount_local=amount,
        discount_pct=discount, status=status, payment_method=payment_method,
        signup_date=signup, invoice_date=invoiced, churned=churned, csat_score=csat,
        support_tickets=tickets, dq_email_repaired=email_repaired,
        dq_missing_cents=_is_whole(amount) and not _is_whole(expected),                     # R14
        dq_amount_off_formula=abs(abs(amount) - expected) >= _OFF_FORMULA_FROM,             # R15
        dq_invoice_date_ambiguous=invoice_ambiguous, dq_signup_date_ambiguous=signup_ambiguous,
    )


def clean_row(fields: list[str], source_row: int, rules: Rules) -> CleanRow | Reject:
    """Clean one raw row. `fields` are the values as read from the CSV, as text (R1)."""
    raw = tuple(fields)
    record = raw_record_json(raw)
    if len(raw) != len(COLUMNS):  # R2
        invoice_id = raw[0].strip() if raw and raw[0].strip() else None
        return Reject(source_row, invoice_id, "missing_required",
                      f"expected {len(COLUMNS)} fields, found {len(raw)}", record)
    row = {name: value.strip() for name, value in zip(COLUMNS, raw)}  # R1: trim every field
    try:
        return clean_fields(row, source_row, raw, rules)
    except _Stop as stop:
        return Reject(source_row, row["invoice_id"] or None, stop.reason, stop.detail, record)

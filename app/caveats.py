"""Caveat attachment rules C1 to C5 (specs/02_metrics.md, section 3).

Caveats are attached deterministically by inspecting the final SQL that ran.
The model does not decide whether to mention them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.sql_guard import _strip_comments_and_strings


@dataclass(frozen=True)
class Caveat:
    id: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "text": self.text}


USD_COLUMNS = frozenset({
    "amount_usd",
    "recognized_revenue_usd",
    "mrr_usd",
    "active_mrr_usd",
    "paid_revenue_usd",
    "refunded_usd",
    "net_revenue_usd",
    "list_price_usd",
})

C1_TEXT = (
    "EUR and GBP invoices are converted at the briefing's rates (multiply by 1.08 and 1.27). "
    "In this export those invoices sit above the USD list price, so finance should confirm "
    "the direction. Dividing instead gives a lower figure."
)

C2_TEXT = (
    "account_id is not a reliable customer key in this export: 976 of the 980 accounts with "
    "2 or more invoices show different company names. Account-level answers use each "
    "account's latest invoice."
)

C3_TEXT = (
    "565 invoice dates and 579 signup dates can be read two ways and rely on the format rule "
    "(slash is month first, dash is day first). Years are never affected."
)

C5_TEXT = (
    "16 invoices appeared twice with clashing statuses. The safest status was kept, so none "
    "of them counts as paid unless every copy said paid."
)


def _has_past_year_filter(cleaned_sql: str, full_sql: str, as_of_year: int) -> bool:
    """Return True if the query restricts invoice_year or invoice_date to years < as_of_year."""
    # Check invoice_year = YYYY or invoice_year IN (...) or invoice_year <= YYYY
    year_matches = re.findall(
        r"\binvoice_year\s*(?:=|<=|<|in)\s*\(?\s*(\d{4})\b",
        cleaned_sql,
        flags=re.IGNORECASE,
    )
    if year_matches and all(int(y) < as_of_year for y in year_matches):
        return True

    # Check EXTRACT(YEAR FROM invoice_date) = YYYY or YEAR(invoice_date) = YYYY
    ext_matches = re.findall(
        r"\byear\s*\([^)]*invoice_date[^)]*\)\s*(?:=|<=|<)\s*(\d{4})\b",
        cleaned_sql,
        flags=re.IGNORECASE,
    )
    if ext_matches and all(int(y) < as_of_year for y in ext_matches):
        return True

    # Check date literals compared against invoice_date (e.g., invoice_date < '2025-01-01' or BETWEEN '2024-01-01' AND '2024-12-31')
    date_years = [int(y) for y in re.findall(r"'(\d{4})-\d{2}-\d{2}'", full_sql)]
    if date_years and re.search(r"\binvoice_date\b", cleaned_sql, flags=re.IGNORECASE):
        if all(y < as_of_year for y in date_years):
            return True

    return False


def caveats_for(sql: str, report: dict) -> list[Caveat]:
    """Return the ordered list of caveats (C1..C5) that apply to `sql`."""
    if not sql or not sql.strip():
        return []

    cleaned, _literals = _strip_comments_and_strings(sql)
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", cleaned.lower()))
    select_star = bool(re.search(r"\bselect\s+\*", cleaned, flags=re.IGNORECASE))
    reads_invoices = "invoices" in tokens
    reads_accounts = "accounts" in tokens

    out: list[Caveat] = []

    # C1: any USD amount column (or SELECT * from invoices/accounts)
    if (tokens & USD_COLUMNS) or (select_star and (reads_invoices or reads_accounts)):
        out.append(Caveat("C1", C1_TEXT))

    # C2: accounts, or account_id / account_name in invoices
    if reads_accounts or "account_id" in tokens or "account_name" in tokens or (select_star and reads_invoices):
        out.append(Caveat("C2", C2_TEXT))

    # C3: invoice_month, signup_date, or a month or day taken from a date
    reads_month_or_day = bool(
        "invoice_month" in tokens
        or "signup_date" in tokens
        or re.search(r"\b(month|day|date_trunc|strftime| date_part)\b", cleaned, flags=re.IGNORECASE)
        or re.search(r"\bextract\s*\(\s*(month|day)\b", cleaned, flags=re.IGNORECASE)
    )
    if reads_month_or_day:
        out.append(Caveat("C3", C3_TEXT))

    # C4: invoices or accounts without a filter that keeps only years before the as-of year
    as_of_str = str(report.get("as_of_date", "2026-11-12"))
    try:
        as_of_year = int(as_of_str[:4])
    except ValueError:
        as_of_year = 2026
    future_count = int(report.get("flags", {}).get("dq_future_dated", 107))

    if (reads_invoices or reads_accounts) and not _has_past_year_filter(cleaned, sql, as_of_year):
        c4_text = (
            f"{future_count} invoices are dated after the as-of date and are included "
            f"({future_count} as of {as_of_str}). The count and the date are taken from the run report."
        )
        out.append(Caveat("C4", c4_text))

    # C5: status or recognized_revenue_usd
    if "status" in tokens or "recognized_revenue_usd" in tokens or (select_star and reads_invoices):
        out.append(Caveat("C5", C5_TEXT))

    return out

"""Duplicate handling (R16) and the sign check that follows it (R13).

Input: the cleaned rows. Output: one row per kept invoice, the dq_duplicates rows,
and the rejects. The file has no timestamps and its row order is random, so no
"first row wins" rule is used anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from app.pipeline.clean import CleanRow, Reject, raw_record_json
from app.rules import Rules

# The cleaned values of the 19 input columns (R16, "identical after cleaning").
COMPARED_FIELDS: tuple[str, ...] = (
    "invoice_id", "account_id", "account_name", "contact_email", "region", "industry", "plan",
    "billing_cycle", "seats", "currency", "amount_local", "discount_pct", "status", "payment_method",
    "signup_date", "invoice_date", "churned", "csat_score", "support_tickets",
)
# Flags that are true on the merged row when true on any copy (R16).
MERGED_FLAGS: tuple[str, ...] = (
    "dq_email_repaired", "dq_missing_cents", "dq_amount_off_formula",
    "dq_invoice_date_ambiguous", "dq_signup_date_ambiguous",
)
REFUNDED = "refunded"   # R13, R16c: the status the briefing ties to a negative amount
EXACT_COPY, SAME_MEANING, STATUS_CONFLICT = "exact_copy", "same_meaning", "status_conflict"


@dataclass(frozen=True)
class KeptInvoice:
    row: CleanRow                  # the merged row: chosen status, flags joined
    duplicate_rows: int            # raw rows merged into it, 1 means no duplicate
    status_conflict: bool          # R16c
    source_rows: tuple[int, ...]   # CSV line numbers, ascending


@dataclass(frozen=True)
class DuplicateRecord:
    """One row of dq_duplicates (specs/01, section 3.4)."""
    invoice_id: str
    raw_row_count: int
    kind: str
    raw_statuses: str
    chosen_status: str
    source_rows: str


@dataclass(frozen=True)
class DedupResult:
    kept: list[KeptInvoice]
    duplicates: list[DuplicateRecord]
    rejects: list[Reject]


def format_source_rows(lines: tuple[int, ...]) -> str:
    return ",".join(str(n) for n in lines)


def _differing_fields(rows: list[CleanRow]) -> list[str]:
    first = rows[0]
    return [name for name in COMPARED_FIELDS
            if any(getattr(r, name) != getattr(first, name) for r in rows[1:])]


def choose_status(rows: list[CleanRow], rules: Rules) -> str:
    """R16c: a negative amount is a refund. Otherwise the first status present in the config order."""
    if rows[0].amount_local < 0:
        return REFUNDED
    present = {r.status for r in rows}
    for status in rules.status_order:
        if status in present:
            return status
    raise ValueError(f"no status of {sorted(present)} is in the configured status order")


def _reject_all(rows: list[CleanRow], reason: str, detail: str) -> list[Reject]:
    return [Reject(r.source_row, r.invoice_id, reason, detail, raw_record_json(r.raw)) for r in rows]


def _merge(rows: list[CleanRow], status: str) -> CleanRow:
    flags = {name: any(getattr(r, name) for r in rows) for name in MERGED_FLAGS}
    return replace(rows[0], status=status, **flags)


def _sign_problem(row: CleanRow) -> str | None:
    """R13: refunded exactly when the amount is negative. Zero is not a refund."""
    if row.status == REFUNDED and row.amount_local >= 0:
        return f"status is refunded but the amount is {row.amount_local}"
    if row.status != REFUNDED and row.amount_local < 0:
        return f"amount {row.amount_local} is negative but the status is {row.status}"
    return None


def _kind(rows: list[CleanRow], differing: list[str]) -> str:
    if differing == ["status"]:
        return STATUS_CONFLICT                                   # R16c
    if all(r.raw == rows[0].raw for r in rows[1:]):
        return EXACT_COPY                                        # R16b, every raw row identical
    return SAME_MEANING                                          # R16b, for example Complete and PAID


def resolve_group(rows: list[CleanRow], rules: Rules) -> tuple[KeptInvoice | None, DuplicateRecord | None, list[Reject]]:
    """Resolve the rows of one invoice_id (R16), then check the sign (R13)."""
    rows = sorted(rows, key=lambda r: r.source_row)
    differing = _differing_fields(rows)
    if differing and differing != ["status"]:                    # R16d
        detail = f"{len(rows)} rows share this invoice_id and differ in: {', '.join(differing)}"
        return None, None, _reject_all(rows, "conflicting_duplicate", detail)
    status = choose_status(rows, rules) if differing else rows[0].status
    merged = _merge(rows, status)
    problem = _sign_problem(merged)                              # R13
    if problem:
        return None, None, _reject_all(rows, "sign_status_mismatch", problem)
    lines = tuple(r.source_row for r in rows)
    kept = KeptInvoice(merged, len(rows), bool(differing), lines)
    if len(rows) == 1:                                           # R16a
        return kept, None, []
    record = DuplicateRecord(
        invoice_id=merged.invoice_id, raw_row_count=len(rows), kind=_kind(rows, differing),
        raw_statuses=" | ".join(r.raw_status for r in rows), chosen_status=status,
        source_rows=format_source_rows(lines))
    return kept, record, []


def resolve_duplicates(rows: list[CleanRow], rules: Rules) -> DedupResult:
    """R16 and R13 over all cleaned rows. Output order follows the first CSV line of each invoice."""
    groups: dict[str, list[CleanRow]] = {}
    for row in rows:
        groups.setdefault(row.invoice_id, []).append(row)
    result = DedupResult([], [], [])
    for group in sorted(groups.values(), key=lambda g: min(r.source_row for r in g)):
        kept, record, rejects = resolve_group(group, rules)
        if kept:
            result.kept.append(kept)
        if record:
            result.duplicates.append(record)
        result.rejects.extend(rejects)
    return result

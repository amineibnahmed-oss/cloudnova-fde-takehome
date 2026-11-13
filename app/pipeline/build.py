"""Run the whole pipeline: raw CSV in, a fresh DuckDB file and a run report out.

Steps: read as text (R1), clean each row (R2 to R15), resolve duplicates (R16, R13),
derive the money columns (R17, R18) and the future-date flag (R20), load the tables,
build accounts (R19), check the invariants (I1 to I5), write the run report (R21).
Same input and same config always give the same output.

Run it with: python -m app.pipeline.build
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import duckdb

from app.pipeline.clean import COLUMNS, CleanRow, Reject, clean_row
from app.pipeline.dedup import (EXACT_COPY, REFUNDED, SAME_MEANING, STATUS_CONFLICT, DedupResult,
                                KeptInvoice, format_source_rows, resolve_duplicates)
from app.rules import FX_MODES, Rules, RulesError, load_rules

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
PAID = "paid"
RECOGNIZED_STATUSES = (PAID, REFUNDED)   # specs/01 section 3.1, recognized_revenue_usd
_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_HUNDRED = Decimal(100)

INVOICE_COLUMNS: tuple[str, ...] = (
    "invoice_id", "account_id", "account_name", "region", "industry", "plan", "billing_cycle",
    "seats", "currency", "amount_local", "discount_pct", "status", "payment_method",
    "signup_date", "invoice_date", "invoice_year", "invoice_month", "churned", "csat_score",
    "support_tickets", "fx_rate_to_usd", "amount_usd", "recognized_revenue_usd", "list_price_usd",
    "billed_months", "mrr_usd", "dq_duplicate_rows", "dq_status_conflict", "dq_missing_cents",
    "dq_amount_off_formula", "dq_invoice_date_ambiguous", "dq_signup_date_ambiguous",
    "dq_future_dated", "source_rows",
)
INVOICE_FLAGS: tuple[str, ...] = (
    "dq_status_conflict", "dq_missing_cents", "dq_amount_off_formula",
    "dq_invoice_date_ambiguous", "dq_signup_date_ambiguous", "dq_future_dated",
)
TABLES: tuple[str, ...] = ("invoices", "invoice_contacts", "accounts", "dq_duplicates", "rejects")

# R19 and specs/01 section 3.3. "Latest" is the greatest invoice_date, ties to the greater invoice_id.
ACCOUNTS_SQL = """
INSERT INTO accounts
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY invoice_date DESC, invoice_id DESC) AS rn
    FROM invoices
),
latest AS (SELECT * FROM ranked WHERE rn = 1),
csat_ranked AS (
    SELECT account_id, csat_score,
           ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY invoice_date DESC, invoice_id DESC) AS rn
    FROM invoices
    WHERE csat_score IS NOT NULL
),
totals AS (
    SELECT account_id,
           COUNT(*) AS invoice_count,
           MIN(invoice_date) AS first_invoice_date,
           MAX(invoice_date) AS last_invoice_date,
           COALESCE(SUM(amount_usd) FILTER (WHERE status = $paid), 0) AS paid_revenue_usd,
           COALESCE(SUM(amount_usd) FILTER (WHERE status = $refunded), 0) AS refunded_usd,
           SUM(recognized_revenue_usd) AS net_revenue_usd,
           COUNT(DISTINCT account_name) AS distinct_account_names
    FROM invoices
    GROUP BY account_id
)
SELECT l.account_id, l.account_name, l.region, l.industry, l.plan, l.billing_cycle, l.seats,
       l.discount_pct, l.currency, l.signup_date, l.churned, l.support_tickets,
       NOT l.churned AS is_active,
       l.mrr_usd,
       CASE WHEN NOT l.churned THEN l.mrr_usd ELSE 0 END AS active_mrr_usd,
       c.csat_score AS latest_csat,
       t.invoice_count, t.first_invoice_date, t.last_invoice_date,
       t.paid_revenue_usd, t.refunded_usd, t.net_revenue_usd,
       t.distinct_account_names,
       t.distinct_account_names > 1 AS dq_identity_conflict
FROM latest l
JOIN totals t USING (account_id)
LEFT JOIN csat_ranked c ON c.account_id = l.account_id AND c.rn = 1
ORDER BY l.account_id
"""


class PipelineError(Exception):
    """The build could not finish. str(exc) is a reason a finance user can read."""


# ---------------------------------------------------------------- read (R1)

def read_raw_rows(csv_path: Path) -> list[tuple[int, list[str]]]:
    """R1: every field as text, no missing-value detection, with the CSV line number of each row.

    The header is line 1. Lines that are completely empty hold no record and are skipped.
    """
    try:
        handle = open(csv_path, newline="", encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise PipelineError(f"Invoice export not found at {csv_path}.") from exc
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise PipelineError(f"Invoice export {csv_path} is empty.") from exc
        _check_header(header, csv_path)
        rows: list[tuple[int, list[str]]] = []
        first_line = reader.line_num + 1
        for fields in reader:
            if fields:
                rows.append((first_line, fields))
            first_line = reader.line_num + 1
    return rows


def _check_header(header: list[str], csv_path: Path) -> None:
    found = tuple(name.strip() for name in header)
    if found != COLUMNS:
        raise PipelineError(
            f"Invoice export {csv_path} does not have the expected 19 column header. "
            f"Expected: {', '.join(COLUMNS)}. Found: {', '.join(found)}.")


def count_exact_duplicate_rows(raw_rows: list[tuple[int, list[str]]]) -> int:
    """R16: raw rows identical in every raw field to an earlier row."""
    seen: set[tuple[str, ...]] = set()
    repeats = 0
    for _, fields in raw_rows:
        key = tuple(fields)
        if key in seen:
            repeats += 1
        seen.add(key)
    return repeats


def clean_all(raw_rows: list[tuple[int, list[str]]], rules: Rules) -> tuple[list[CleanRow], list[Reject]]:
    cleaned: list[CleanRow] = []
    rejects: list[Reject] = []
    for line, fields in raw_rows:
        result = clean_row(fields, line, rules)
        if isinstance(result, Reject):
            rejects.append(result)
        else:
            cleaned.append(result)
    return cleaned, rejects


# ---------------------------------------------------------------- money (R17, R18)

def round_cents(value: Decimal) -> Decimal:
    """R17: round to cents, halves away from zero."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def to_usd(amount_local: Decimal, rate: Decimal, fx_mode: str) -> Decimal:
    """R17: multiply by the rate. Decision D1: "divide" is the other reading of the same rate."""
    converted = amount_local / rate if fx_mode == "divide" else amount_local * rate
    return round_cents(converted)


def mrr_usd(list_price: int, seats: int, discount_pct: Decimal) -> Decimal:
    """R18: list price times seats times (1 - discount). Never uses the invoice amount."""
    return round_cents(Decimal(list_price) * seats * (1 - discount_pct / _HUNDRED))


def invoice_record(kept: KeptInvoice, rules: Rules, as_of: date, fx_mode: str) -> tuple[Any, ...]:
    """One row of `invoices`, in INVOICE_COLUMNS order."""
    row = kept.row
    rate = rules.fx_rate_to_usd[row.currency]
    amount_usd = to_usd(row.amount_local, rate, fx_mode)                       # R17
    recognized = amount_usd if row.status in RECOGNIZED_STATUSES else _ZERO
    list_price = rules.prices_usd[row.plan]
    return (
        row.invoice_id, row.account_id, row.account_name, row.region, row.industry, row.plan,
        row.billing_cycle, row.seats, row.currency, row.amount_local, row.discount_pct, row.status,
        row.payment_method, row.signup_date, row.invoice_date, row.invoice_date.year,
        f"{row.invoice_date.year:04d}-{row.invoice_date.month:02d}", row.churned, row.csat_score,
        row.support_tickets, rate, amount_usd, recognized, list_price,
        rules.billed_months[row.billing_cycle],
        mrr_usd(list_price, row.seats, row.discount_pct),                      # R18
        kept.duplicate_rows, kept.status_conflict, row.dq_missing_cents, row.dq_amount_off_formula,
        row.dq_invoice_date_ambiguous, row.dq_signup_date_ambiguous,
        row.invoice_date > as_of,                                              # R20
        format_source_rows(kept.source_rows),
    )


# ---------------------------------------------------------------- load

def _insert(con: duckdb.DuckDBPyConnection, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
    if not rows:
        return
    marks = ", ".join("?" for _ in columns)
    con.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", rows)


def load_tables(con: duckdb.DuckDBPyConnection, dedup: DedupResult, rejects: list[Reject],
                rules: Rules, as_of: date, fx_mode: str) -> None:
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    con.execute("BEGIN")
    _insert(con, "invoices", INVOICE_COLUMNS,
            [invoice_record(k, rules, as_of, fx_mode) for k in dedup.kept])
    _insert(con, "invoice_contacts", ("invoice_id", "contact_email", "dq_email_repaired"),
            [(k.row.invoice_id, k.row.contact_email, k.row.dq_email_repaired) for k in dedup.kept])
    _insert(con, "dq_duplicates",
            ("invoice_id", "raw_row_count", "kind", "raw_statuses", "chosen_status", "source_rows"),
            [(d.invoice_id, d.raw_row_count, d.kind, d.raw_statuses, d.chosen_status, d.source_rows)
             for d in dedup.duplicates])
    _insert(con, "rejects", ("source_row", "invoice_id", "reason", "detail", "raw_record"),
            [(r.source_row, r.invoice_id, r.reason, r.detail, r.raw_record)
             for r in sorted(rejects, key=lambda r: r.source_row)])
    con.execute(ACCOUNTS_SQL, {"paid": PAID, "refunded": REFUNDED})            # R19
    con.execute("COMMIT")


# ---------------------------------------------------------------- invariants (section 9)

def _one(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> Any:
    return con.execute(sql, params or []).fetchone()[0]


def _null_problems(con: duckdb.DuckDBPyConnection) -> list[str]:
    """I5: no NULL in a column the schema marks NOT NULL."""
    columns = con.execute(
        "SELECT table_name, column_name FROM duckdb_columns() "
        "WHERE schema_name = 'main' AND NOT is_nullable ORDER BY table_name, column_index").fetchall()
    problems = []
    for table, column in columns:
        nulls = _one(con, f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL')
        if nulls:
            problems.append(f"I5: {table}.{column} has {nulls} NULL values")
    return problems


def check_invariants(con: duckdb.DuckDBPyConnection, rows_read: int) -> None:
    """I1 to I5. Raises PipelineError listing every invariant that is false."""
    problems: list[str] = []
    invoices = _one(con, "SELECT COUNT(*) FROM invoices")
    distinct_ids = _one(con, "SELECT COUNT(DISTINCT invoice_id) FROM invoices")
    if invoices != distinct_ids:                                               # I1
        problems.append(f"I1: invoices has {invoices} rows but {distinct_ids} different invoice ids")
    merged = _one(con, "SELECT COALESCE(SUM(dq_duplicate_rows), 0) FROM invoices")
    rejected = _one(con, "SELECT COUNT(*) FROM rejects")
    if rows_read != merged + rejected:                                         # I2
        problems.append(f"I2: {rows_read} rows read, but {merged} were kept or merged and {rejected} rejected")
    sign = _one(con, "SELECT COUNT(*) FROM invoices WHERE (status = ?) <> (amount_local < 0)", [REFUNDED])
    if sign:                                                                   # I3
        problems.append(f"I3: {sign} invoices where refunded and a negative amount do not go together")
    counted = _one(con, "SELECT COALESCE(SUM(invoice_count), 0) FROM accounts")
    if counted != invoices:                                                    # I4
        problems.append(f"I4: accounts count {counted} invoices but invoices has {invoices} rows")
    problems.extend(_null_problems(con))                                       # I5
    if problems:
        raise PipelineError("The build stopped because an invariant failed. " + ". ".join(problems) + ".")


# ---------------------------------------------------------------- run report (R21)

def make_report(con: duckdb.DuckDBPyConnection, as_of: date, fx_mode: str, rows_read: int,
                exact_dropped: int) -> dict:
    reasons = con.execute("SELECT reason, COUNT(*) FROM rejects GROUP BY reason ORDER BY reason").fetchall()
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM dq_duplicates GROUP BY kind").fetchall())
    flags = {flag: _one(con, f"SELECT COUNT(*) FROM invoices WHERE {flag}") for flag in INVOICE_FLAGS}
    flags["dq_email_repaired"] = _one(con, "SELECT COUNT(*) FROM invoice_contacts WHERE dq_email_repaired")
    flags["dq_identity_conflict"] = _one(con, "SELECT COUNT(*) FROM accounts WHERE dq_identity_conflict")
    tables = {table: _one(con, f"SELECT COUNT(*) FROM {table}") for table in TABLES}
    return {
        "as_of_date": as_of.isoformat(),
        "fx_mode": fx_mode,
        "rows_read": rows_read,
        "rows_rejected": tables["rejects"],
        "reject_reasons": {reason: count for reason, count in reasons},
        "exact_duplicate_rows_dropped": exact_dropped,
        "duplicates_by_kind": {kind: kinds.get(kind, 0) for kind in (EXACT_COPY, SAME_MEANING, STATUS_CONFLICT)},
        "tables": tables,
        "flags": flags,
    }


def format_report(report: dict) -> str:
    """The run report as a short table (R21)."""
    lines: list[tuple[str, Any]] = [
        ("As-of date", report["as_of_date"]),
        ("FX mode", report["fx_mode"]),
        ("Rows read", report["rows_read"]),
        ("Rows rejected", report["rows_rejected"]),
    ]
    lines += [(f"  rejected: {reason}", count) for reason, count in report["reject_reasons"].items()]
    lines.append(("Exact duplicate rows dropped", report["exact_duplicate_rows_dropped"]))
    lines += [(f"Duplicate ids: {kind}", count) for kind, count in report["duplicates_by_kind"].items()]
    lines += [(f"Flag: {flag}", count) for flag, count in report["flags"].items()]
    lines += [(f"Table: {table}", count) for table, count in report["tables"].items()]
    width = max(len(label) for label, _ in lines)
    body = [f"{label:<{width}}  {value:>10,}" if isinstance(value, int) else f"{label:<{width}}  {value:>10}"
            for label, value in lines]
    return "\n".join(["Run report", "-" * (width + 12), *body])


def write_report(report: dict, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- build

def _resolve_fx_mode(fx_mode: str | None, rules: Rules) -> str:
    mode = rules.fx_mode if fx_mode is None else fx_mode
    if mode not in FX_MODES:
        raise PipelineError(f"fx_mode must be one of {', '.join(FX_MODES)}, got {mode!r}.")
    return mode


def _remove(path: Path) -> None:
    if path.exists():
        path.unlink()


def _write_database(db_path: Path, dedup: DedupResult, rejects: list[Reject], rules: Rules,
                    as_of: date, fx_mode: str, rows_read: int, exact_dropped: int) -> dict:
    """Build into a temporary file and swap it in only when the invariants hold."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(db_path.name + ".building")
    _remove(tmp_path)
    _remove(tmp_path.with_name(tmp_path.name + ".wal"))
    con = duckdb.connect(str(tmp_path))
    try:
        load_tables(con, dedup, rejects, rules, as_of, fx_mode)
        check_invariants(con, rows_read)
        report = make_report(con, as_of, fx_mode, rows_read, exact_dropped)
        con.execute("CHECKPOINT")
    except duckdb.Error as exc:
        con.close()
        _remove(tmp_path)
        raise PipelineError(f"The database could not be loaded: {exc}") from exc
    except Exception:
        con.close()
        _remove(tmp_path)
        raise
    con.close()
    _remove(tmp_path.with_name(tmp_path.name + ".wal"))
    _remove(db_path.with_name(db_path.name + ".wal"))
    os.replace(tmp_path, db_path)
    return report


def build(csv_path: Path, db_path: Path, rules_path: Path, as_of: date | None = None,
          report_path: Path | None = None, fx_mode: str | None = None) -> dict:
    """Run the whole pipeline into a fresh database file and return the run report.
    as_of: R20, defaults to config [run].as_of_date, then to today.
    fx_mode: overrides config [fx].mode ("multiply" or "divide")."""
    try:
        rules = load_rules(Path(rules_path))
    except RulesError as exc:
        raise PipelineError(str(exc)) from exc
    mode = _resolve_fx_mode(fx_mode, rules)
    as_of_date = as_of or rules.as_of_date or date.today()                     # R20
    raw_rows = read_raw_rows(Path(csv_path))                                   # R1
    cleaned, rejects = clean_all(raw_rows, rules)                              # R2 to R15
    dedup = resolve_duplicates(cleaned, rules)                                 # R16, R13
    report = _write_database(Path(db_path), dedup, rejects + dedup.rejects, rules, as_of_date, mode,
                             rows_read=len(raw_rows),
                             exact_dropped=count_exact_duplicate_rows(raw_rows))
    if report_path is not None:
        write_report(report, Path(report_path))                                # R21
    return report


def main() -> int:
    """Build with the default settings and print the run report."""
    from app.settings import load_settings  # imported late: build() itself needs no environment

    settings = load_settings()
    try:
        report = build(csv_path=settings.csv_path, db_path=settings.db_path,
                       rules_path=settings.rules_path, report_path=settings.report_path)
    except PipelineError as exc:
        if settings.debug:
            raise
        print(f"Build failed: {exc}")
        return 1
    print(format_report(report))
    print(f"\nDatabase: {settings.db_path}\nRun report: {settings.report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

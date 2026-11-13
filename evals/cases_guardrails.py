"""Guardrail cases G1 to G6 (specs/04_evals.md, section 4)."""
from __future__ import annotations

from app.sql_guard import GuardError, run_guarded
from evals.harness import Case, Context, check, check_equal


def _expect_refused(ctx: Context, sql: str) -> str:
    try:
        run_guarded(ctx.con, sql, row_limit=ctx.settings.row_limit, timeout_s=ctx.settings.sql_timeout_s)
    except GuardError as exc:
        return f"refused: {exc}"
    raise AssertionError(f"expected GuardError for {sql!r}, but query ran")


def g1(ctx: Context) -> str:
    return _expect_refused(ctx, "DROP TABLE invoices")


def g2(ctx: Context) -> str:
    return _expect_refused(ctx, "SELECT 1; DELETE FROM invoices")


def g3(ctx: Context) -> str:
    return _expect_refused(ctx, "SELECT * FROM read_csv_auto('data/cloudnova_invoices.csv')")


def g4(ctx: Context) -> str:
    return _expect_refused(ctx, "SELECT contact_email FROM invoice_contacts")


def g5(ctx: Context) -> str:
    sql = "WITH p AS (SELECT * FROM invoices WHERE status = 'paid') SELECT COUNT(*) FROM p"
    res = run_guarded(ctx.con, sql, row_limit=ctx.settings.row_limit, timeout_s=ctx.settings.sql_timeout_s)
    check_equal("G5 row count", res.row_count, 1)
    check_equal("G5 paid count", res.rows[0][0], 3417)
    check(not res.truncated, "G5 should not be truncated")
    return "allowed, returns 3,417"


def g6(ctx: Context) -> str:
    sql = "SELECT * FROM invoices"
    res = run_guarded(ctx.con, sql, row_limit=200, timeout_s=ctx.settings.sql_timeout_s)
    check_equal("G6 returned rows", res.row_count, 200)
    check_equal("G6 len(rows)", len(res.rows), 200)
    check(res.truncated is True, "G6 expected truncated=True")
    return "allowed, 200 rows, truncated true"


CASES: list[Case] = [
    Case("G1", "DROP TABLE invoices is refused", g1),
    Case("G2", "Multi-statement SELECT + DELETE is refused", g2),
    Case("G3", "read_csv_auto external file read is refused", g3),
    Case("G4", "Reading invoice_contacts (email addresses) is refused", g4),
    Case("G5", "CTE WITH SELECT on paid invoices is allowed", g5),
    Case("G6", "SELECT * FROM invoices caps at 200 rows with truncated=True", g6),
]

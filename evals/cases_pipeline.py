"""Pipeline cases P1 to P13 (specs/04_evals.md, section 2).

Expected values are typed from specs/01_data_contract.md section 10 and
specs/04_evals.md. They are never copied from the pipeline's own output.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.db import connect_readonly
from app.pipeline.build import build
from app.reference_sql import BY_ID
from evals.harness import Case, Context, check, check_equal, check_money

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bad_rows.csv"
REFERENCE_SCRIPT = Path(__file__).resolve().parent / "reference" / "expected_from_spec.py"
EVAL_AS_OF = "2026-11-12"

# R6, typed from the spec so P4 does not lean on the pipeline's own mapping.
RAW_STATUS = {
    "paid": "paid", "complete": "paid", "completed": "paid", "success": "paid",
    "pending": "pending", "in_review": "pending", "awaiting": "pending",
    "failed": "failed", "declined": "failed", "error": "failed",
    "refunded": "refunded", "refund": "refunded", "charged_back": "refunded",
    "void": "void", "cancelled": "void", "canceled": "void",
}


def _scalar(ctx: Context, sql: str, params: list | None = None):
    return ctx.con.execute(sql, params or []).fetchone()[0]


def _counts(ctx: Context, column: str) -> dict:
    return dict(ctx.con.execute(f"SELECT {column}, COUNT(*) FROM invoices GROUP BY {column}").fetchall())


def _invoice(ctx: Context, invoice_id: str, columns: str) -> tuple:
    row = ctx.con.execute(f"SELECT {columns} FROM invoices WHERE invoice_id = ?", [invoice_id]).fetchone()
    check(row is not None, f"{invoice_id} is not in invoices")
    return row


def p1(ctx: Context) -> str:
    check_equal("rows read", ctx.report["rows_read"], 5125)
    check_equal("rows rejected", ctx.report["rows_rejected"], 0)
    check_equal("rows in rejects", _scalar(ctx, "SELECT COUNT(*) FROM rejects"), 0)
    check_equal("rows in invoices", _scalar(ctx, "SELECT COUNT(*) FROM invoices"), 5000)
    check_equal("report tables.invoices", ctx.report["tables"]["invoices"], 5000)
    return "5,125 read, 0 rejected, 5,000 invoices"


def p2(ctx: Context) -> str:
    kinds = dict(ctx.con.execute("SELECT kind, COUNT(*) FROM dq_duplicates GROUP BY kind").fetchall())
    check_equal("duplicates by kind", kinds, {"exact_copy": 81, "same_meaning": 27, "status_conflict": 16})
    check_equal("report duplicates_by_kind", ctx.report["duplicates_by_kind"],
                {"exact_copy": 81, "same_meaning": 27, "status_conflict": 16})
    check_equal("exact duplicate rows dropped", ctx.report["exact_duplicate_rows_dropped"], 82)
    return "81 exact copy, 27 same meaning, 16 status conflict, 82 exact rows dropped"


def p3(ctx: Context) -> str:
    check_equal("status counts", _counts(ctx, "status"),
                {"paid": 3417, "pending": 505, "failed": 398, "refunded": 292, "void": 388})
    return "paid 3,417, pending 505, failed 398, refunded 292, void 388"


def p4(ctx: Context) -> str:
    rows = ctx.con.execute(
        "SELECT d.invoice_id, d.raw_statuses, d.chosen_status, i.status, i.dq_status_conflict "
        "FROM dq_duplicates d JOIN invoices i USING (invoice_id) WHERE d.kind = 'status_conflict'").fetchall()
    check_equal("status conflicts", len(rows), 16)
    seen: Counter = Counter()
    for invoice_id, raw_statuses, chosen, status, flagged in rows:
        copies = sorted({RAW_STATUS[s.strip().lower()] for s in raw_statuses.split("|")})
        seen[("+".join(copies), chosen)] += 1
        check_equal(f"{invoice_id} status in invoices", status, chosen)
        check(flagged, f"{invoice_id} has no dq_status_conflict flag")
    check_equal("conflict resolution", dict(seen), {
        ("paid+pending", "pending"): 7, ("paid+void", "void"): 5,
        ("failed+paid", "failed"): 2, ("paid+refunded", "refunded"): 2})
    for invoice_id, expected in (("INV-103655", "pending"), ("INV-102104", "failed"), ("INV-102708", "refunded")):
        check_equal(f"{invoice_id} status", _invoice(ctx, invoice_id, "status")[0], expected)
    return "7 to pending, 5 to void, 2 to failed, 2 to refunded, 3 spot checks"


def p5(ctx: Context) -> str:
    check_equal("invoices with region NA", _scalar(ctx, "SELECT COUNT(*) FROM invoices WHERE region = 'NA'"), 1245)
    check_equal("invoices with NULL region", _scalar(ctx, "SELECT COUNT(*) FROM invoices WHERE region IS NULL"), 0)
    check_equal("region counts", _counts(ctx, "region"), {"APAC": 1231, "EMEA": 1279, "LATAM": 1245, "NA": 1245})
    return "1,245 invoices with region NA, 0 NULL"


def p6(ctx: Context) -> str:
    expected = {"INV-104166": date(2025, 4, 2), "INV-104950": date(2022, 2, 3),
                "INV-100863": date(2024, 4, 30), "INV-101399": date(2024, 5, 10)}
    for invoice_id, day in expected.items():
        check_equal(f"{invoice_id} invoice_date", _invoice(ctx, invoice_id, "invoice_date")[0], day)
    return "dash is day first, slash is month first, named month and year-first slash read"


def p7(ctx: Context) -> str:
    local, currency, usd = _invoice(ctx, "INV-101355", "amount_local, currency, amount_usd")
    check_equal("INV-101355 amount_local", local, Decimal("-4900.00"))
    check_equal("INV-101355 currency", currency, "USD")
    check_money("INV-101355 amount_usd", usd, "-4900.00")
    check_money("INV-100960 amount_usd", _invoice(ctx, "INV-100960", "amount_usd")[0], "-571.54")
    check_equal("INV-101983 amount_usd (rounding rule)", _invoice(ctx, "INV-101983", "amount_usd")[0],
                Decimal("35564.45"))
    check_money("INV-103551 amount_usd", _invoice(ctx, "INV-103551", "amount_usd")[0], "697507.20")
    usd, missing = _invoice(ctx, "INV-101502", "amount_usd, dq_missing_cents")
    check_money("INV-101502 amount_usd", usd, "39918.64")
    check_equal("INV-101502 dq_missing_cents", missing, True)
    return "5 amounts parsed and converted, halves round away from zero"


def p8(ctx: Context) -> str:
    plan, seats, discount, mrr = _invoice(ctx, "INV-101502", "plan, seats, discount_pct, mrr_usd")
    check_equal("INV-101502 plan", plan, "Pro")
    check_equal("INV-101502 seats", seats, 25)
    check_equal("INV-101502 discount_pct", discount, Decimal("0.00"))
    check_money("INV-101502 mrr_usd", mrr, "2475.00")
    check_equal("plan counts", _counts(ctx, "plan"), {"Starter": 2487, "Pro": 1507, "Enterprise": 1006})
    return "Tier 2 is Pro with MRR 2,475.00. Starter 2,487, Pro 1,507, Enterprise 1,006"


def p9(ctx: Context) -> str:
    check_equal("as-of date of the eval build", ctx.report["as_of_date"], EVAL_AS_OF)
    expected = {"dq_missing_cents": 121, "dq_amount_off_formula": 0,
                "dq_invoice_date_ambiguous": 565, "dq_future_dated": 107}
    for flag, count in expected.items():
        check_equal(flag, _scalar(ctx, f"SELECT COUNT(*) FROM invoices WHERE {flag}"), count)
        check_equal(f"report flags.{flag}", ctx.report["flags"][flag], count)
    return "missing cents 121, off formula 0, ambiguous invoice dates 565, future dated 107"


def p10(ctx: Context) -> str:
    check_equal("accounts", _scalar(ctx, "SELECT COUNT(*) FROM accounts"), 3815)
    check_equal("active accounts", _scalar(ctx, "SELECT COUNT(*) FROM accounts WHERE is_active"), 3062)
    check_equal("accounts with dq_identity_conflict",
                _scalar(ctx, "SELECT COUNT(*) FROM accounts WHERE dq_identity_conflict"), 976)
    return "3,815 accounts, 3,062 active, 976 with an identity conflict"


def p11(ctx: Context) -> str:
    columns = [r[0] for r in ctx.con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'invoices'").fetchall()]
    check(len(columns) > 0, "invoices has no columns")
    with_email = [c for c in columns if "email" in c.lower()]
    check(not with_email, f"invoices has an email column: {with_email}")
    check_equal("NULL emails", _scalar(ctx, "SELECT COUNT(*) FROM invoice_contacts WHERE contact_email IS NULL"), 285)
    check_equal("repaired emails", _scalar(ctx, "SELECT COUNT(*) FROM invoice_contacts WHERE dq_email_repaired"), 139)
    return "no email column in invoices, 285 NULL and 139 repaired in invoice_contacts"


def p12(ctx: Context) -> str:
    db_path = ctx.tmp_dir / "p12_bad_rows.duckdb"
    report = build(csv_path=FIXTURE, db_path=db_path, rules_path=ctx.settings.rules_path,
                   as_of=date.fromisoformat(ctx.report["as_of_date"]))
    check_equal("fixture rows read", report["rows_read"], 10)
    check_equal("fixture reject reasons", report["reject_reasons"],
                {"unknown_plan": 1, "bad_date": 1, "currency_mismatch": 1, "conflicting_duplicate": 2})
    check_equal("fixture rows rejected", report["rows_rejected"], 5)
    check_equal("fixture invoices", report["tables"]["invoices"], 4)
    con = connect_readonly(db_path)
    try:
        by_id = dict(con.execute("SELECT invoice_id, reason FROM rejects GROUP BY ALL").fetchall())
        kept = {r[0] for r in con.execute("SELECT invoice_id FROM invoices").fetchall()}
    finally:
        con.close()
    check_equal("reason per rejected invoice", by_id, {
        "INV-900005": "unknown_plan", "INV-900006": "bad_date",
        "INV-900007": "currency_mismatch", "INV-900008": "conflicting_duplicate"})
    check_equal("kept invoices", kept, {"INV-900001", "INV-900002", "INV-900003", "INV-900004"})
    return "4 reject reasons over 5 rows, 4 good invoices kept, no crash"


def _reference_values(ctx: Context) -> dict:
    done = subprocess.run(
        [sys.executable, str(REFERENCE_SCRIPT), str(ctx.settings.csv_path), ctx.report["as_of_date"]],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    check(done.returncode == 0, f"reference script failed: {done.stderr.strip()[-300:]}")
    return json.loads(done.stdout)


def _sql_rows(ctx: Context, query_id: str) -> list[tuple]:
    return ctx.con.execute(BY_ID[query_id].sql).fetchall()


def _p13_q1_q3_q6(ctx: Context, ref: dict) -> None:
    paid, invoices, net = _sql_rows(ctx, "q1")[0]
    check_money("Q1 paid revenue", paid, ref["q1"]["paid_2024_usd"])
    check_equal("Q1 paid invoices", invoices, ref["q1"]["paid_2024_invoices"])
    check_money("Q1 net recognized", net, ref["q1"]["net_2024_usd"])
    refunded, refunds = _sql_rows(ctx, "q3")[0]
    check_money("Q3 refunds", refunded, -Decimal(ref["q3"]["refunds_usd"]))
    check_equal("Q3 refund count", refunds, ref["q3"]["refund_invoices"])
    q6 = {status: (n, usd) for status, n, usd in _sql_rows(ctx, "q6")}
    check_equal("Q6 statuses", sorted(q6), ["failed", "pending"])
    for status in ("pending", "failed"):
        check_equal(f"Q6 {status} invoices", q6[status][0], ref["q6"][f"{status}_invoices"])
        check_money(f"Q6 {status} exposure", q6[status][1], ref["q6"][f"{status}_usd"])
    check_money("Q6 total exposure", q6["pending"][1] + q6["failed"][1], ref["q6"]["exposed_usd"])


def _p13_q2_q4_q5(ctx: Context, ref: dict) -> None:
    q2 = _sql_rows(ctx, "q2")
    check_equal("Q2 top region", q2[0][0], ref["q2"]["top_region_active"])
    check_equal("Q2 regions", sorted(r[0] for r in q2), sorted(ref["q2"]["by_region"]))
    for region, accounts, avg in q2:
        check_equal(f"Q2 {region} active accounts", accounts, ref["q2"]["by_region"][region]["active_accounts"])
        check_money(f"Q2 {region} average MRR", avg, ref["q2"]["by_region"][region]["avg_mrr_active_usd"])
    q4 = _sql_rows(ctx, "q4")
    check_equal("Q4 plans", sorted(r[0] for r in q4), sorted(ref["q4"]))
    for plan, accounts, churned, rate in q4:
        check_equal(f"Q4 {plan} accounts", accounts, ref["q4"][plan]["accounts"])
        check_equal(f"Q4 {plan} churned", churned, ref["q4"][plan]["churned"])
        check_equal(f"Q4 {plan} churn rate", Decimal(str(rate)), Decimal(ref["q4"][plan]["churn_rate_pct"]))
    q5 = _sql_rows(ctx, "q5")
    check_equal("Q5 account ids", [r[0] for r in q5], [a["account_id"] for a in ref["q5"]])
    for (account_id, _name, net, csat, flag, _conflict), expected in zip(q5, ref["q5"]):
        check_money(f"Q5 {account_id} net revenue", net, expected["net_revenue_usd"])
        check_equal(f"Q5 {account_id} latest CSAT", csat, expected["latest_csat"])
        check_equal(f"Q5 {account_id} CSAT flag", flag, expected["csat_flag"])


def p13(ctx: Context) -> str:
    ref = _reference_values(ctx)
    _p13_q1_q3_q6(ctx, ref)
    _p13_q2_q4_q5(ctx, ref)
    return "the standalone script and the reference SQL agree on Q1 to Q6"


CASES: list[Case] = [
    Case("P1", "Rows read, rejected, kept", p1),
    Case("P2", "Duplicates by kind", p2),
    Case("P3", "Status counts", p3),
    Case("P4", "The 16 status conflicts resolve as decided", p4),
    Case("P5", "Region NA survives the read", p5),
    Case("P6", "Date rule", p6),
    Case("P7", "Amount parsing and FX", p7),
    Case("P8", "Plan mapping and MRR", p8),
    Case("P9", "Flags", p9),
    Case("P10", "Accounts", p10),
    Case("P11", "Emails stay out of reach", p11),
    Case("P12", "Bad rows are set aside, not guessed", p12),
    Case("P13", "The standalone reference script agrees with the database", p13),
]

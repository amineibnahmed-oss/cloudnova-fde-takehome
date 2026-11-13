"""Cases expected to fail, X1 and X2 (specs/04_evals.md, section 6).

They stay in the suite so the limits of the data are visible on every run.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.db import connect_readonly
from app.pipeline.build import build
from app.reference_sql import BY_ID
from evals.harness import Case, Context, check


def _top5(con) -> list[str]:
    return [row[0] for row in con.execute(BY_ID["q5"].sql).fetchall()]


def x1(ctx: Context) -> str:
    db_path = ctx.tmp_dir / "x1_fx_divide.duckdb"
    report = build(csv_path=ctx.settings.csv_path, db_path=db_path, rules_path=ctx.settings.rules_path,
                   as_of=date.fromisoformat(ctx.report["as_of_date"]), fx_mode="divide")
    check(report["fx_mode"] == "divide", f"the second build did not run in divide mode: {report['fx_mode']}")
    multiply = _top5(ctx.con)
    con = connect_readonly(db_path)
    try:
        divide = _top5(con)
    finally:
        con.close()
    overlap = len(set(multiply) & set(divide))
    detail = (f"the two FX readings share {overlap} of 5 top accounts. "
              f"Multiply: {', '.join(multiply)}. Divide: {', '.join(divide)}")
    check(multiply == divide, detail)
    return detail


def x2(ctx: Context) -> str:
    rows = ctx.con.execute(
        "SELECT amount_local, list_price_usd, seats, discount_pct, billed_months, fx_rate_to_usd "
        "FROM invoices WHERE dq_missing_cents").fetchall()
    gap = Decimal("0")
    for amount, price, seats, discount, months, rate in rows:
        expected_local = Decimal(price) * seats * (1 - discount / Decimal(100)) * months * rate   # R14
        gap += abs(abs(amount) - expected_local)
    shown = gap.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    detail = f"{len(rows)} invoices lost their cents, {shown} currency units in total"
    check(len(rows) == 0, detail)
    return "no invoice lost its cents"


CASES: list[Case] = [
    Case("X1", "The top 5 accounts are the same under both FX readings", x1,
         xfail="Multiply or divide for EUR and GBP is an open question for finance (D1) and account_id "
               "groups unrelated invoices (D2), so the two lists share 0 of 5."),
    Case("X2", "Every stored amount matches the price list to the cent", x2,
         xfail="121 invoices lost their cents in the export (38.55 currency units) and the pipeline "
               "flags them instead of inventing the cents (D6)."),
]

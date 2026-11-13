"""Metric cases M1 to M6 (specs/04_evals.md, section 3).

Each runs the reference SQL of specs/02_metrics.md on the built database and
compares it with the values written in the spec.
"""
from __future__ import annotations

from decimal import Decimal

from app.reference_sql import BY_ID
from evals.harness import Case, Context, check_equal, check_money


def _rows(ctx: Context, query_id: str) -> list[tuple]:
    return ctx.con.execute(BY_ID[query_id].sql).fetchall()


def m1(ctx: Context) -> str:
    rows = _rows(ctx, "q1")
    check_equal("Q1 row count", len(rows), 1)
    paid, invoices, net = rows[0]
    check_money("paid revenue 2024", paid, "32655352.52")
    check_equal("paid invoices 2024", invoices, 888)
    check_money("net recognized 2024", net, "30724069.70")
    return "32,655,352.52 from 888 invoices, net 30,724,069.70"


def m2(ctx: Context) -> str:
    rows = _rows(ctx, "q2")
    expected = [("APAC", 753, "6147.30"), ("NA", 739, "5632.84"), ("EMEA", 781, "5327.66"), ("LATAM", 789, "5187.55")]
    check_equal("regions in order", [r[0] for r in rows], [e[0] for e in expected])
    for (region, accounts, avg), (_, want_accounts, want_avg) in zip(rows, expected):
        check_equal(f"{region} active accounts", accounts, want_accounts)
        check_money(f"{region} average MRR", avg, want_avg)
    return "APAC 6,147.30, then NA 5,632.84, EMEA 5,327.66, LATAM 5,187.55"


def m3(ctx: Context) -> str:
    rows = _rows(ctx, "q3")
    check_equal("Q3 row count", len(rows), 1)
    refunded, refunds = rows[0]
    check_money("refunds given back", refunded, "8925163.84")
    check_equal("refund count", refunds, 292)
    return "8,925,163.84 from 292 refunds"


def m4(ctx: Context) -> str:
    rows = {plan: (accounts, churned, rate) for plan, accounts, churned, rate in _rows(ctx, "q4")}
    expected = {"Enterprise": (780, 171, "21.92"), "Starter": (1882, 354, "18.81"), "Pro": (1153, 228, "19.77")}
    check_equal("plans", sorted(rows), sorted(expected))
    for plan, (accounts, churned, rate) in expected.items():
        check_equal(f"{plan} accounts", rows[plan][0], accounts)
        check_equal(f"{plan} churned accounts", rows[plan][1], churned)
        check_equal(f"{plan} churn rate", Decimal(str(rows[plan][2])), Decimal(rate))
    return "Enterprise 21.92 (171 of 780), Starter 18.81 (354 of 1,882), Pro 19.77 (228 of 1,153)"


def m5(ctx: Context) -> str:
    rows = _rows(ctx, "q5")
    expected = [("ACC-7509", "1210592.75", 4, True), ("ACC-1420", "1208092.75", 5, True),
                ("ACC-9946", "1205642.75", 4, False), ("ACC-1196", "1107352.84", 3, True),
                ("ACC-5211", "1085078.48", 5, False)]
    check_equal("top 5 account ids in order", [r[0] for r in rows], [e[0] for e in expected])
    for (account_id, _name, net, csat, flag, conflict), (_, want_net, want_csat, want_conflict) in zip(rows, expected):
        check_money(f"{account_id} net revenue", net, want_net)
        check_equal(f"{account_id} latest CSAT", csat, want_csat)
        check_equal(f"{account_id} CSAT flag", flag, False)
        check_equal(f"{account_id} mixed company names", conflict, want_conflict)
    return "ACC-7509, ACC-1420, ACC-9946, ACC-1196, ACC-5211 in that order, no CSAT flag"


def m6(ctx: Context) -> str:
    rows = {status: (invoices, usd) for status, invoices, usd in _rows(ctx, "q6")}
    check_equal("statuses", sorted(rows), ["failed", "pending"])
    check_equal("pending invoices", rows["pending"][0], 505)
    check_money("pending exposure", rows["pending"][1], "15934168.41")
    check_equal("failed invoices", rows["failed"][0], 398)
    check_money("failed exposure", rows["failed"][1], "13207147.36")
    check_equal("stuck invoices", rows["pending"][0] + rows["failed"][0], 903)
    check_money("total exposure", rows["pending"][1] + rows["failed"][1], "29141315.77")
    return "903 invoices, 29,141,315.77 (pending 505 and 15,934,168.41, failed 398 and 13,207,147.36)"


CASES: list[Case] = [
    Case("M1", "Q1 paid revenue 2024, and net of refunds", m1),
    Case("M2", "Q2 top region by average MRR per active account", m2),
    Case("M3", "Q3 refunds given back", m3),
    Case("M4", "Q4 churn rate by plan", m4),
    Case("M5", "Q5 top 5 accounts and CSAT flags", m5),
    Case("M6", "Q6 pending and failed exposure", m6),
]

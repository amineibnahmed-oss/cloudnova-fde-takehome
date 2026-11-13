"""The six reference queries of specs/02_metrics.md, section 2.

Used by the offline demo and by the metric evals, and they are the yardstick the
question agent is judged against. Change the spec first, then this file.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

Rows = Sequence[Sequence[Any]]


def _usd(value: Any) -> str:
    return f"${value:,.2f}"


def _q1(rows: Rows) -> str:
    paid, invoices, net = rows[0]
    return (f"Paid invoices dated 2024 brought in {_usd(paid)} ({invoices} invoices). "
            f"After the refunds dated 2024, net recognized revenue is {_usd(net)}.")


def _q2(rows: Rows) -> str:
    region, accounts, avg = rows[0]
    rest = ", ".join(f"{r} {_usd(a)}" for r, _, a in rows[1:])
    return (f"{region} has the highest average MRR per active account: {_usd(avg)} "
            f"across {accounts} accounts. Then {rest}.")


def _q3(rows: Rows) -> str:
    refunded, refunds = rows[0]
    return f"{_usd(refunded)} has been given back across {refunds} refunds."


def _q4(rows: Rows) -> str:
    by_plan = {plan: (accounts, churned, rate) for plan, accounts, churned, rate in rows}
    parts = []
    for plan in ("Enterprise", "Starter", "Pro"):
        if plan in by_plan:
            accounts, churned, rate = by_plan[plan]
            parts.append(f"{plan} {rate:.2f}% ({churned} of {accounts} accounts)")
    return "Churn rate by plan: " + ", ".join(parts) + "."


def _q5(rows: Rows) -> str:
    listed = ", ".join(f"{acc} ({_usd(rev)})" for acc, _, rev, *_ in rows)
    flagged = [acc for acc, _, _, _, flag, _ in rows if flag]
    mixed = [acc for acc, *_, conflict in rows if conflict]
    text = f"Top 5 accounts by net revenue: {listed}. "
    text += f"CSAT of 2 or less: {', '.join(flagged)}." if flagged else "None of them has a CSAT of 2 or less."
    if mixed:
        text += f" Mixed company names on: {', '.join(mixed)}."
    return text


def _q6(rows: Rows) -> str:
    by_status = {status: (invoices, usd) for status, invoices, usd in rows}
    p_n, p_usd = by_status.get("pending", (0, 0))
    f_n, f_usd = by_status.get("failed", (0, 0))
    return (f"{p_n + f_n} invoices are stuck: {p_n} pending ({_usd(p_usd)}) and {f_n} failed "
            f"({_usd(f_usd)}). Exposed in total: {_usd(p_usd + f_usd)}.")


@dataclass(frozen=True)
class ReferenceQuery:
    id: str                          # "q1" to "q6"
    question: str                    # as asked in the briefing
    sql: str                         # reference SQL of specs/02_metrics.md
    summarize: Callable[[Rows], str]  # plain-English answer built from the result rows


REFERENCE_QUERIES: tuple[ReferenceQuery, ...] = (
    ReferenceQuery(
        "q1",
        "What was our total recognized revenue in USD for paid invoices in 2024?",
        """SELECT SUM(amount_usd) FILTER (WHERE status = 'paid') AS paid_revenue_usd,
       COUNT(*) FILTER (WHERE status = 'paid') AS paid_invoices,
       SUM(recognized_revenue_usd) AS net_recognized_usd
FROM invoices
WHERE invoice_year = 2024""",
        _q1,
    ),
    ReferenceQuery(
        "q2",
        "Which region has the highest average MRR per account?",
        """SELECT region, COUNT(*) AS active_accounts, ROUND(AVG(mrr_usd), 2) AS avg_mrr_usd
FROM accounts
WHERE is_active
GROUP BY region
ORDER BY avg_mrr_usd DESC, region""",
        _q2,
    ),
    ReferenceQuery(
        "q3",
        "How much have we given back in refunds?",
        """SELECT -SUM(amount_usd) AS refunded_usd, COUNT(*) AS refunds
FROM invoices
WHERE status = 'refunded'""",
        _q3,
    ),
    ReferenceQuery(
        "q4",
        "What's the churn rate among Enterprise accounts vs Starter?",
        """SELECT plan, COUNT(*) AS accounts,
       SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned_accounts,
       ROUND(100.0 * SUM(CASE WHEN churned THEN 1 ELSE 0 END) / COUNT(*), 2) AS churn_rate_pct
FROM accounts
GROUP BY plan
ORDER BY plan""",
        _q4,
    ),
    ReferenceQuery(
        "q5",
        "List the top 5 accounts by revenue, and flag any with CSAT of 2 or less.",
        """SELECT account_id, account_name, net_revenue_usd, latest_csat,
       COALESCE(latest_csat <= 2, false) AS csat_flag, dq_identity_conflict
FROM accounts
ORDER BY net_revenue_usd DESC, account_id
LIMIT 5""",
        _q5,
    ),
    ReferenceQuery(
        "q6",
        "How many invoices are stuck in pending or failed, and what's the exposed dollar amount?",
        """SELECT status, COUNT(*) AS invoices, SUM(amount_usd) AS exposed_usd
FROM invoices
WHERE status IN ('pending', 'failed')
GROUP BY status
ORDER BY status""",
        _q6,
    ),
)

BY_ID: dict[str, ReferenceQuery] = {q.id: q for q in REFERENCE_QUERIES}

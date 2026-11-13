#!/usr/bin/env python3
"""Standalone reference calculation of the expected numbers. Standard library only.

Implements specs/01_data_contract.md and specs/02_metrics.md with plain loops and
Decimal maths. It shares no code with app/, so the pipeline and this script can
disagree. The expected values in specs/04_evals.md were produced by this script.

Usage: python evals/reference/expected_from_spec.py [csv_path] [as_of YYYY-MM-DD]
Prints one JSON document.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

path = sys.argv[1] if len(sys.argv) > 1 else "data/cloudnova_invoices.csv"
AS_OF = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 11, 12)

PRICE = {"Starter": 49, "Pro": 99, "Enterprise": 299}
FX = {"USD": Decimal("1"), "EUR": Decimal("1.08"), "GBP": Decimal("1.27")}
PLAN = {"starter": "Starter", "start": "Starter", "tier 1": "Starter",
        "pro": "Pro", "professional": "Pro", "tier 2": "Pro",
        "enterprise": "Enterprise", "ent": "Enterprise", "tier 3": "Enterprise"}
CYCLE = {"monthly": "monthly", "annual": "annual", "annually": "annual", "yearly": "annual"}
STATUS = {}
for canon, raws in {"paid": "paid complete completed success",
                    "pending": "pending in_review awaiting",
                    "failed": "failed declined error",
                    "refunded": "refunded refund charged_back",
                    "void": "void cancelled canceled"}.items():
    for r in raws.split():
        STATUS[r] = canon
BOOL = {"true": True, "yes": True, "1": True, "y": True,
        "false": False, "no": False, "0": False, "n": False}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SAFE_ORDER = ["refunded", "void", "failed", "pending", "paid"]
CENT = Decimal("0.01")


def parse_date(v):
    """Returns (date, individually_ambiguous)."""
    v = v.strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", v)
    if m:
        y, mo, d = map(int, m.groups())
        return date(y, mo, d), False
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", v)          # slash = month first
    if m:
        mo, d, y = map(int, m.groups())
        return date(y, mo, d), (mo <= 12 and d <= 12 and mo != d)
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", v)          # dash = day first
    if m:
        d, mo, y = map(int, m.groups())
        return date(y, mo, d), (mo <= 12 and d <= 12 and mo != d)
    m = re.fullmatch(r"([A-Za-z]{3}) (\d{1,2}) (\d{4})", v)
    if m:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))), False
    raise ValueError(v)


def norm(r):
    c = {}
    c["invoice_id"] = r["invoice_id"].strip()
    c["account_id"] = r["account_id"].strip()
    c["account_name"] = r["account_name"].strip()
    e = r["contact_email"].strip().lower()
    c["email_repaired"] = "_at_" in e
    c["contact_email"] = e.replace("_at_", "@") or None
    c["region"] = r["region"].strip().upper()
    c["industry"] = r["industry"].strip() or None
    c["plan"] = PLAN[r["plan"].strip().lower()]
    c["billing_cycle"] = CYCLE[r["billing_cycle"].strip().lower()]
    c["seats"] = int(r["seats"].strip())
    c["currency"] = r["currency"].strip().upper()
    s = r["amount"].strip()
    num = re.sub(r"[^\d.\-]", "", s)
    c["amount_local"] = Decimal(num)
    c["has_cents"] = "." in num
    c["discount_pct"] = Decimal(r["discount_pct"].strip())
    c["status"] = STATUS[r["status"].strip().lower()]
    c["payment_method"] = r["payment_method"].strip().lower() or None
    c["signup_date"], c["signup_ambiguous"] = parse_date(r["signup_date"])
    c["invoice_date"], c["invoice_ambiguous"] = parse_date(r["invoice_date"])
    c["churned"] = BOOL[r["churned"].strip().lower()]
    c["csat_score"] = int(r["csat_score"]) if r["csat_score"].strip() else None
    c["support_tickets"] = int(r["support_tickets"].strip())
    return c


with open(path, newline="", encoding="utf-8-sig") as f:
    raw = list(csv.DictReader(f))

by_id = defaultdict(list)
for i, r in enumerate(raw):
    by_id[r["invoice_id"].strip()].append((i + 2, r, norm(r)))

invoices = []
kinds = Counter()
conflicts = []
exact_extra = 0
for inv_id, items in by_id.items():
    raws = {tuple(sorted(r.items())) for _, r, _ in items}
    exact_extra += len(items) - len(raws)
    c = dict(items[0][2])
    c["dq_duplicate_rows"] = len(items)
    c["dq_status_conflict"] = False
    if len(items) > 1:
        no_status = {tuple(sorted((k, str(v)) for k, v in n.items() if k != "status")) for _, _, n in items}
        assert len(no_status) == 1, f"{inv_id}: duplicates differ beyond status"
        sts = {n["status"] for _, _, n in items}
        if len(sts) == 1:
            kinds["exact_copies" if len(raws) == 1 else "same_meaning"] += 1
        else:
            kinds["status_conflict"] += 1
            c["dq_status_conflict"] = True
            c["status"] = "refunded" if c["amount_local"] < 0 else next(s for s in SAFE_ORDER if s in sts)
            conflicts.append({"invoice_id": inv_id, "statuses": sorted(sts), "chosen": c["status"],
                              "amount_local": str(c["amount_local"]), "currency": c["currency"]})
    assert (c["amount_local"] < 0) == (c["status"] == "refunded"), f"{inv_id}: sign/status mismatch"
    rate = FX[c["currency"]]
    c["amount_usd"] = (c["amount_local"] * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    c["amount_usd_divide"] = (c["amount_local"] / rate).quantize(CENT, rounding=ROUND_HALF_UP)
    c["mrr_usd"] = Decimal(PRICE[c["plan"]]) * c["seats"] * (1 - c["discount_pct"] / 100)
    months = 10 if c["billing_cycle"] == "annual" else 1
    c["expected_local"] = c["mrr_usd"] * months * rate
    c["cents_gap"] = abs(abs(c["amount_local"]) - c["expected_local"])
    c["dq_missing_cents"] = (not c["has_cents"]) and c["expected_local"] != c["expected_local"].to_integral_value()
    c["dq_future_dated"] = c["invoice_date"] > AS_OF
    invoices.append(c)


def total(rows, key="amount_usd"):
    return sum((c[key] for c in rows), Decimal("0"))


def f(x):
    return f"{x:.2f}"


def inum(c):
    return int(c["invoice_id"].split("-")[1])


out = {"as_of": AS_OF.isoformat()}
out["counts"] = {
    "raw_rows": len(raw),
    "exact_duplicate_rows_dropped": exact_extra,
    "invoice_ids_with_exact_copies_only": kinds["exact_copies"],
    "invoice_ids_same_meaning_different_spelling": kinds["same_meaning"],
    "invoice_ids_status_conflict": kinds["status_conflict"],
    "invoices": len(invoices),
    "status": dict(sorted(Counter(c["status"] for c in invoices).items())),
    "plan": dict(sorted(Counter(c["plan"] for c in invoices).items())),
    "currency": dict(sorted(Counter(c["currency"] for c in invoices).items())),
    "billing_cycle": dict(sorted(Counter(c["billing_cycle"] for c in invoices).items())),
    "contact_email_null": sum(1 for c in invoices if c["contact_email"] is None),
    "contact_email_repaired": sum(1 for c in invoices if c["email_repaired"]),
    "industry_null": sum(1 for c in invoices if c["industry"] is None),
    "csat_null": sum(1 for c in invoices if c["csat_score"] is None),
    "payment_method_null": sum(1 for c in invoices if c["payment_method"] is None),
    "dq_missing_cents": sum(1 for c in invoices if c["dq_missing_cents"]),
    "dq_invoice_date_ambiguous": sum(1 for c in invoices if c["invoice_ambiguous"]),
    "dq_signup_date_ambiguous": sum(1 for c in invoices if c["signup_ambiguous"]),
    "dq_future_dated": sum(1 for c in invoices if c["dq_future_dated"]),
    "amount_off_formula_by_1_or_more": sum(1 for c in invoices if c["cents_gap"] >= 1),
    "invoice_before_signup": sum(1 for c in invoices if c["invoice_date"] < c["signup_date"]),
}
out["conflicts"] = sorted(conflicts, key=lambda x: x["invoice_id"])
out["conflict_resolution"] = dict(Counter(f"{'+'.join(x['statuses'])} -> {x['chosen']}" for x in conflicts))
out["invoice_date_range"] = [min(c["invoice_date"] for c in invoices).isoformat(),
                             max(c["invoice_date"] for c in invoices).isoformat()]
out["future_dated_by_status"] = dict(sorted(Counter(c["status"] for c in invoices if c["dq_future_dated"]).items()))
out["region"] = dict(sorted(Counter(c["region"] for c in invoices).items()))
out["spot_checks"] = {c["invoice_id"]: {"invoice_date": c["invoice_date"].isoformat(), "amount_local": str(c["amount_local"]),
                                         "amount_usd": str(c["amount_usd"]), "status": c["status"], "plan": c["plan"],
                                         "mrr_usd": str(c["mrr_usd"]), "currency": c["currency"]}
                      for c in invoices if c["invoice_id"] in {
                          "INV-104166", "INV-103264", "INV-104950", "INV-104380", "INV-100863", "INV-101399",
                          "INV-101355", "INV-101983", "INV-103551", "INV-100960", "INV-100205", "INV-101502"}}
out["cents_lost_local_units"] =str(sum((c["cents_gap"] for c in invoices if c["dq_missing_cents"]), Decimal("0")))

# ---- Q1, Q3, Q6
paid24 = [c for c in invoices if c["status"] == "paid" and c["invoice_date"].year == 2024]
ref24 = [c for c in invoices if c["status"] == "refunded" and c["invoice_date"].year == 2024]
refunds = [c for c in invoices if c["status"] == "refunded"]
pend = [c for c in invoices if c["status"] == "pending"]
fail = [c for c in invoices if c["status"] == "failed"]
out["q1"] = {"paid_2024_usd": f(total(paid24)), "paid_2024_invoices": len(paid24),
             "refunds_dated_2024_usd": f(total(ref24)), "refunds_dated_2024_invoices": len(ref24),
             "net_2024_usd": f(total(paid24) + total(ref24)),
             "alt_fx_divide_paid_2024_usd": f(total(paid24, "amount_usd_divide"))}
out["q3"] = {"refunds_usd": f(total(refunds)), "refund_invoices": len(refunds),
             "alt_fx_divide_refunds_usd": f(total(refunds, "amount_usd_divide"))}
out["q6"] = {"pending_invoices": len(pend), "failed_invoices": len(fail), "stuck_invoices": len(pend) + len(fail),
             "pending_usd": f(total(pend)), "failed_usd": f(total(fail)), "exposed_usd": f(total(pend) + total(fail)),
             "alt_fx_divide_exposed_usd": f(total(pend, "amount_usd_divide") + total(fail, "amount_usd_divide"))}
by_year = defaultdict(list)
for c in invoices:
    if c["status"] == "paid":
        by_year[c["invoice_date"].year].append(c)
out["paid_usd_by_year"] = {str(y): [f(total(v)), len(v)] for y, v in sorted(by_year.items())}

# ---- accounts: latest invoice wins (invoice_date, then higher invoice number)
acc = defaultdict(list)
for c in invoices:
    acc[c["account_id"]].append(c)
accounts = []
for aid, v in acc.items():
    v.sort(key=lambda c: (c["invoice_date"], inum(c)))
    L = v[-1]
    with_csat = [c for c in v if c["csat_score"] is not None]
    accounts.append({
        "account_id": aid, "account_name": L["account_name"], "region": L["region"], "plan": L["plan"],
        "churned": L["churned"], "mrr_usd": L["mrr_usd"], "invoice_count": len(v),
        "distinct_names": len({c["account_name"] for c in v}),
        "latest_csat": with_csat[-1]["csat_score"] if with_csat else None,
        "net_revenue_usd": total([c for c in v if c["status"] in ("paid", "refunded")]),
        "net_revenue_usd_divide": total([c for c in v if c["status"] in ("paid", "refunded")], "amount_usd_divide"),
    })
out["accounts"] = {"accounts": len(accounts),
                   "with_2_or_more_invoices": sum(1 for a in accounts if a["invoice_count"] > 1),
                   "identity_conflict": sum(1 for a in accounts if a["distinct_names"] > 1),
                   "churned": sum(1 for a in accounts if a["churned"]),
                   "active": sum(1 for a in accounts if not a["churned"])}

# ---- Q2 average MRR per active account by region
q2 = {}
for region in sorted({a["region"] for a in accounts}):
    act = [a["mrr_usd"] for a in accounts if a["region"] == region and not a["churned"]]
    allr = [a["mrr_usd"] for a in accounts if a["region"] == region]
    q2[region] = {"active_accounts": len(act),
                  "avg_mrr_active_usd": f((sum(act) / len(act)).quantize(CENT, rounding=ROUND_HALF_UP)),
                  "all_accounts": len(allr),
                  "avg_mrr_all_usd": f((sum(allr) / len(allr)).quantize(CENT, rounding=ROUND_HALF_UP))}
out["q2"] = {"by_region": q2,
             "top_region_active": max(q2, key=lambda r: Decimal(q2[r]["avg_mrr_active_usd"])),
             "total_active_mrr_usd": f(sum((a["mrr_usd"] for a in accounts if not a["churned"]), Decimal("0")))}
rows_by_region = defaultdict(list)
for c in invoices:
    rows_by_region[c["region"]].append(c["mrr_usd"])
out["q2"]["alt_per_invoice_row"] = {r: f((sum(v) / len(v)).quantize(CENT, rounding=ROUND_HALF_UP))
                                    for r, v in sorted(rows_by_region.items())}

# ---- Q4 churn rate by plan
q4 = {}
for plan in ("Starter", "Pro", "Enterprise"):
    v = [a for a in accounts if a["plan"] == plan]
    ch = sum(1 for a in v if a["churned"])
    q4[plan] = {"accounts": len(v), "churned": ch,
                "churn_rate_pct": f((Decimal(100) * ch / len(v)).quantize(CENT, rounding=ROUND_HALF_UP))}
out["q4"] = q4
rows_by_plan = defaultdict(list)
for c in invoices:
    rows_by_plan[c["plan"]].append(c["churned"])
out["q4_alt_per_invoice_row"] = {p: f((Decimal(100) * sum(v) / len(v)).quantize(CENT, rounding=ROUND_HALF_UP))
                                 for p, v in sorted(rows_by_plan.items())}

# ---- Q5 top 5 accounts by net recognized revenue
def top5(key):
    ranked = sorted(accounts, key=lambda a: (-a[key], a["account_id"]))[:5]
    return [{"account_id": a["account_id"], "net_revenue_usd": f(a[key]), "account_name_latest": a["account_name"],
             "invoice_count": a["invoice_count"], "distinct_names": a["distinct_names"],
             "latest_csat": a["latest_csat"],
             "csat_flag": a["latest_csat"] is not None and a["latest_csat"] <= 2} for a in ranked]


out["q5"] = top5("net_revenue_usd")
out["q5_alt_fx_divide"] = top5("net_revenue_usd_divide")
out["q5_overlap_between_fx_readings"] = len({a["account_id"] for a in out["q5"]} &
                                            {a["account_id"] for a in out["q5_alt_fx_divide"]})
names = defaultdict(lambda: Decimal("0"))
for c in invoices:
    if c["status"] in ("paid", "refunded"):
        names[c["account_name"]] += c["amount_usd"]
out["q5_alt_by_company_name"] = [[n, f(v)] for n, v in sorted(names.items(), key=lambda kv: -kv[1])[:5]]

# ---- sensitivity: the other duplicate rule (paid wins) on Q1
paid_wins_extra = Decimal("0")
for x in conflicts:
    if "paid" in x["statuses"] and x["chosen"] != "refunded":
        c = next(i for i in invoices if i["invoice_id"] == x["invoice_id"])
        if c["invoice_date"].year == 2024:
            paid_wins_extra += c["amount_usd"]
out["q1"]["alt_paid_wins_paid_2024_usd"] = f(total(paid24) + paid_wins_extra)
future_paid24 = Decimal("0")
out["q6"]["alt_excluding_future_dated_exposed_usd"] = f(total([c for c in pend + fail if not c["dq_future_dated"]]))
out["q6"]["alt_excluding_future_dated_invoices"] = sum(1 for c in pend + fail if not c["dq_future_dated"])

print(json.dumps(out, indent=2))

# Specs

These four files were written before any pipeline or agent code. The code is built from them, and the evals check the code against them.

| File | What it fixes |
|------|---------------|
| [01_data_contract.md](01_data_contract.md) | The cleaned tables, every cleaning rule, duplicate handling, FX, MRR |
| [02_metrics.md](02_metrics.md) | How each stakeholder question is calculated, and the caveats attached to it |
| [03_query_interface.md](03_query_interface.md) | The plain-English question interface: commands, output, guardrails |
| [04_evals.md](04_evals.md) | The eval cases, expected values, and the cases expected to fail |

## How the spec was produced

1. Profiled the raw export with read-only scripts (no cleaning yet) to find what is in the file.
2. Turned the open questions into four decisions, each with evidence and the cost of the other choice. Amine made those calls.
3. Wrote the rules below from the briefing plus those decisions.
4. Computed every expected number in `04_evals.md` with a small standalone script that does not share code with the pipeline, so the two can disagree.

## Decision log

Decisions D1 to D4 were put to Amine as options with evidence on 2026-11-12. Claude recommended one option each time and Amine chose. D5 to D10 are defaults Claude proposed in the spec, which Amine reviews with the spec.

| # | Question | Decision | Evidence from the file | What the other choice would do |
|---|----------|----------|------------------------|--------------------------------|
| D1 | How to convert EUR and GBP to USD | Follow the briefing: multiply by 1.08 and 1.27. Flag the price gap. | On all 5,125 rows the stored amount equals the USD price formula times the rate. A EUR invoice for a $49,000 plan is stored as 52,920 EUR, which converts to $57,153.60. | Dividing gives 2024 paid revenue of $29,565,429.38 instead of $32,655,352.52. |
| D2 | What is one customer | `account_id`, as the briefing says. The latest invoice decides region, plan and churn. Every account-level answer carries a warning. | 980 account ids have 2 or more invoices. On 976 of them the company name differs between invoices. The count of distinct ids (3,815) matches random assignment over the id range (3,836 expected). | Grouping by company name gives 200 customers, and every one of them appears in more than one region. |
| D3 | Which copy wins when a repeated invoice has clashing statuses | The safest status. An invoice counts as paid only if every copy says paid. A negative amount means refunded. | 16 invoice ids. The file is shuffled and has no timestamps, so nothing shows which copy is newer. | "Paid wins" adds $31,018.68 to 2024 paid revenue. |
| D4 | How to read dates like `05-01-2024` | Slash dates are month first. Dash dates are day first. | 1,532 of 1,532 slash dates that can be checked are only valid month first. 313 of 313 dash dates that can be checked are only valid day first. | Nothing at year level: the year is always written out. Month-level reports would move. |
| D5 | Invoices dated in the future | Keep them and flag them. | 107 invoices are dated after 2026-11-12, up to 2027-07-23. 71 of them are marked paid. | Excluding them drops pending and failed exposure from $29,141,315.77 to $28,827,971.80. |
| D6 | Amounts stored without cents | Keep the stored amount, flag it, never rebuild it from the price list. | 121 invoices lost their cents (some truncated, some rounded). The total gap is 38.55 currency units. | Rebuilding from the formula would invent ledger values. |
| D7 | MRR of an account | Price formula applied to the account's latest invoice. Active means not churned on that invoice. | The briefing defines MRR from list price, seats and discount, not from the invoice amount. | Using every invoice row would count some accounts several times. |
| D8 | Latest CSAT of an account | The most recent non-blank score. | 607 invoices have no CSAT score. | Using only the latest invoice leaves more accounts without a score. |
| D9 | When a refund counts | In the period of the refund row's own invoice date. | Refunds are separate negative rows with their own date. | None available: the file does not link a refund to the invoice it reverses. |
| D10 | Rows that cannot be cleaned | Set them aside in a `rejects` table with a reason. Never guess, never drop silently. | 0 rows are rejected on this file. | Guessing would hide the problem from finance. |

## Independent checks already done

- Three profiling scripts written at different times agree on every count used here.
- A separate set of audit scripts, written by other Claude agents that did not see these numbers, reproduced the 2024 paid revenue ($32,655,352.37 before per-invoice rounding), the refunds dated 2024 and the top 5 accounts.
- An agent that was given only the rule text, with every expected number removed, rebuilt the cleaning from scratch. All 138 numbers compared with the reference matched. It also listed 15 places where the wording could be read two ways. None changed a number on this file, and each is now spelled out in the rules.
- The same audit found one trap that is now rule R1: pandas reads the region `NA` as a missing value by default, which would silently drop North America (1,245 invoices).

# 04. Evals

A small harness that shows pass or fail for each case. Expected values were computed by a standalone script (plain loops and decimal maths) that shares no code with the pipeline or the SQL, so a bug in one does not hide in the other.

## 1. How to run

| Command | Runs | Needs a key |
|---------|------|-------------|
| `python -m evals` | Offline cases: pipeline, metrics, guardrails, expected failures | no |
| `python -m evals --live` | The offline cases plus the plain-English cases | yes |

Output is one line per case: id, result, detail. Results are `PASS`, `FAIL`, `XFAIL` (failed as predicted), `XPASS` (predicted to fail but passed) and `SKIP` (live case without a key). The exit code is 1 if any case is `FAIL` or `XPASS`. CI runs the offline set on every push.

Money matches within 0.01. Counts, labels and ordered lists match exactly.

## 2. Pipeline cases (offline)

| Id | Checks | Expected |
|----|--------|----------|
| P1 | Rows read, rejected, kept | 5,125 read, 0 rejected, 5,000 invoices |
| P2 | Duplicates by kind | 81 exact copy, 27 same meaning, 16 status conflict, 82 exact rows dropped |
| P3 | Status counts | paid 3,417, pending 505, failed 398, refunded 292, void 388 |
| P4 | The 16 conflicts resolve as decided | 7 paid+pending to pending, 5 paid+void to void, 2 paid+failed to failed, 2 paid+refunded to refunded. Spot checks: `INV-103655` pending, `INV-102104` failed, `INV-102708` refunded |
| P5 | Region `NA` survives the read | 1,245 invoices with region `NA`, 0 with a NULL region |
| P6 | Date rule | `INV-104166` (`02-04-2025`) is 2025-04-02. `INV-104950` (`02/03/2022`) is 2022-02-03. `INV-100863` (`Apr 30 2024`) is 2024-04-30. `INV-101399` (`2024/05/10`) is 2024-05-10 |
| P7 | Amount parsing and FX | `INV-101355` (`$-4,900.00`) is -4900.00 USD. `INV-100960` (`-529.20 EUR`) is -571.54 USD. `INV-101983` (`£28,003.50`) is 35,564.45 USD, which checks the rounding rule. `INV-103551` (`645,840.00 EUR`) is 697,507.20 USD. `INV-101502` (`31432`) is 39,918.64 USD with `dq_missing_cents` |
| P8 | Plan mapping and MRR | `INV-101502` (`Tier 2`, 25 seats, no discount) is `Pro` with MRR 2,475.00. Plan counts Starter 2,487, Pro 1,507, Enterprise 1,006 |
| P9 | Flags | `dq_missing_cents` 121, `dq_amount_off_formula` 0, `dq_invoice_date_ambiguous` 565, `dq_future_dated` 107 with as-of 2026-11-12 |
| P10 | Accounts | 3,815 accounts, 3,062 active, 976 with `dq_identity_conflict` |
| P11 | Emails stay out of reach | `invoices` has no email column. 285 NULL and 139 repaired in `invoice_contacts` |
| P12 | Bad rows are set aside, not guessed | A fixture file with an unknown plan, an impossible date, a currency mismatch and a duplicate that differs in amount yields 4 reject reasons and no crash |

## 3. Metric cases (offline)

Each runs the reference SQL of `02_metrics.md` on the built database.

| Id | Question | Expected |
|----|----------|----------|
| M1 | Q1 paid revenue 2024, and net of refunds | 32,655,352.52 from 888 invoices. Net 30,724,069.70 |
| M2 | Q2 top region by average MRR per active account | APAC 6,147.30. Then NA 5,632.84, EMEA 5,327.66, LATAM 5,187.55 |
| M3 | Q3 refunds given back | 8,925,163.84 from 292 refunds |
| M4 | Q4 churn rate by plan | Enterprise 21.92 (171 of 780), Starter 18.81 (354 of 1,882), Pro 19.77 (228 of 1,153) |
| M5 | Q5 top 5 accounts and CSAT flags | ACC-7509, ACC-1420, ACC-9946, ACC-1196, ACC-5211 in that order, no flag |
| M6 | Q6 pending and failed exposure | 903 invoices, 29,141,315.77 (pending 505 and 15,934,168.41, failed 398 and 13,207,147.36) |

## 4. Guardrail cases (offline)

| Id | Input SQL | Expected |
|----|-----------|----------|
| G1 | `DROP TABLE invoices` | refused |
| G2 | `SELECT 1; DELETE FROM invoices` | refused |
| G3 | `SELECT * FROM read_csv_auto('data/cloudnova_invoices.csv')` | refused |
| G4 | `SELECT contact_email FROM invoice_contacts` | refused |
| G5 | `WITH p AS (SELECT * FROM invoices WHERE status = 'paid') SELECT COUNT(*) FROM p` | allowed, returns 3,417 |
| G6 | `SELECT * FROM invoices` | allowed, 200 rows, `truncated` true |

## 5. Cases expected to fail

These run offline and are reported as `XFAIL`. They stay in the suite so the limits are visible on every run.

| Id | Checks | Why it fails |
|----|--------|--------------|
| X1 | The top 5 accounts by revenue are the same under both FX readings | They share 0 of 5. Whether EUR and GBP amounts should be multiplied or divided is an open question for finance (decision D1), and `account_id` groups unrelated invoices (decision D2). So the Q5 answer is the least trustworthy of the six. |
| X2 | Every stored amount matches the price list to the cent | 121 invoices lost their cents in the export (38.55 currency units in total). The pipeline flags them and refuses to invent the missing cents (decision D6). |

## 6. Plain-English cases (live)

Each case sends the question to the agent and checks the rows returned by its final SQL, not the wording.

| Id | Question | Pass when |
|----|----------|-----------|
| N1 | What was our total recognized revenue in USD for paid invoices in 2024? | A result cell equals 32,655,352.52. Caveats include C1 |
| N2 | Which region has the highest average MRR per account? | The first row is APAC and a cell equals 6,147.30. Caveats include C2 |
| N3 | How much have we given back in refunds? | A cell equals 8,925,163.84 or -8,925,163.84 |
| N4 | What's the churn rate among Enterprise accounts vs Starter? | Cells equal 21.92 and 18.81, or the same as fractions (0.2192, 0.1881) |
| N5 | List the top 5 accounts by revenue, and flag any with CSAT of 2 or less. | The five account ids match M5 in order. Caveats include C2 |
| N6 | How many invoices are stuck in pending or failed, and what's the exposed dollar amount? | Cells give 903 and 29,141,315.77, or the two parts that add up to them |
| N7 | Delete all refunded invoices. | Status is `cannot_answer` or `blocked`. The table still has 5,000 rows |
| N8 | What will our revenue be next quarter? | Status is `cannot_answer` |
| N9 | In dollars, how much did customers actually pay us for invoices dated in 2024? | Same as N1. This checks that a rewording gives the same number |

A live case can fail for reasons outside the code (the model writes a different but defensible query). Any such failure is recorded in the README with the query it wrote.

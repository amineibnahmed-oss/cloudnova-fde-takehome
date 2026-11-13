# 02. Metrics

How each stakeholder question from the briefing is calculated on the cleaned tables of `01_data_contract.md`. The SQL here is the reference: the evals run it, and the question agent is judged against its results.

## 1. Terms

| Term | Meaning |
|------|---------|
| Paid revenue | Sum of `amount_usd` over invoices with status `paid` |
| Net recognized revenue | Paid revenue plus refunds (refunds are negative). Equal to the sum of `recognized_revenue_usd` |
| Period | The year or month of `invoice_date`. A refund counts in the period of its own date |
| Refunds given back | Sum of `amount_usd` over invoices with status `refunded`, shown as a positive number |
| Exposure | Sum of `amount_usd` over invoices with status `pending` or `failed` |
| Account | One `account_id`. Its attributes come from its latest invoice |
| Active account | An account whose latest invoice has `churned = false` |
| MRR of an account | `list price * seats * (1 - discount/100)` of its latest invoice, in USD |
| Churn rate of a plan | Churned accounts on that plan divided by all accounts on that plan |

`pending`, `failed` and `void` invoices are never revenue.

## 2. The six questions

### Q1. Total recognized revenue in USD for paid invoices in 2024

```sql
SELECT SUM(amount_usd) AS paid_revenue_usd, COUNT(*) AS invoices
FROM invoices
WHERE status = 'paid' AND invoice_year = 2024;
```

Expected: **$32,655,352.52** from 888 invoices. The answer also states the net figure after the 70 refunds dated 2024 (-$1,931,282.82): **$30,724,069.70**, which is `SELECT SUM(recognized_revenue_usd) FROM invoices WHERE invoice_year = 2024`. Caveats: C1, C5.

### Q2. Region with the highest average MRR per account

```sql
SELECT region, COUNT(*) AS active_accounts, ROUND(AVG(mrr_usd), 2) AS avg_mrr_usd
FROM accounts
WHERE is_active
GROUP BY region
ORDER BY avg_mrr_usd DESC, region;
```

Averages are rounded to cents, halves away from zero.

Expected: **APAC, $6,147.30** (753 active accounts). Then NA $5,632.84 (739), EMEA $5,327.66 (781), LATAM $5,187.55 (789). Caveat: C2.

### Q3. How much have we given back in refunds

```sql
SELECT -SUM(amount_usd) AS refunded_usd, COUNT(*) AS refunds
FROM invoices
WHERE status = 'refunded';
```

Expected: **$8,925,163.84** across 292 refunds. Caveats: C1, C5.

### Q4. Churn rate, Enterprise against Starter

```sql
SELECT plan, COUNT(*) AS accounts, SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned_accounts,
       ROUND(100.0 * SUM(CASE WHEN churned THEN 1 ELSE 0 END) / COUNT(*), 2) AS churn_rate_pct
FROM accounts
GROUP BY plan;
```

Expected: **Enterprise 21.92%** (171 of 780), **Starter 18.81%** (354 of 1,882). Pro is 19.77% (228 of 1,153). Caveat: C2.

### Q5. Top 5 accounts by revenue, flagging CSAT of 2 or less

```sql
SELECT account_id, account_name, net_revenue_usd, latest_csat,
       COALESCE(latest_csat <= 2, false) AS csat_flag, dq_identity_conflict
FROM accounts
ORDER BY net_revenue_usd DESC, account_id
LIMIT 5;
```

Expected, in order:

| account_id | net_revenue_usd | latest_csat | csat_flag |
|------------|-----------------|-------------|-----------|
| ACC-7509 | 1,210,592.75 | 4 | no |
| ACC-1420 | 1,208,092.75 | 5 | no |
| ACC-9946 | 1,205,642.75 | 4 | no |
| ACC-1196 | 1,107,352.84 | 3 | no |
| ACC-5211 | 1,085,078.48 | 5 | no |

None of the five is flagged. Three of the five (ACC-7509, ACC-1420, ACC-1196) mix invoices that carry different company names. Caveats: C1, C2.

### Q6. Invoices stuck in pending or failed, and the exposed dollars

```sql
SELECT status, COUNT(*) AS invoices, SUM(amount_usd) AS exposed_usd
FROM invoices
WHERE status IN ('pending', 'failed')
GROUP BY status;
```

Expected: pending 505 invoices, $15,934,168.41. Failed 398 invoices, $13,207,147.36. Total **903 invoices, $29,141,315.77**. Caveats: C1, C4, C5.

## 3. Caveats the answers carry

The application attaches these by code, based on what the final SQL reads. The model does not decide whether to mention them.

| Id | Attached when the SQL reads | Text |
|----|-----------------------------|------|
| C1 | any USD amount column | EUR and GBP invoices are converted at the briefing's rates (multiply by 1.08 and 1.27). In this export those invoices sit above the USD list price, so finance should confirm the direction. Dividing instead gives a lower figure. |
| C2 | `accounts`, or `account_id` / `account_name` in `invoices` | `account_id` is not a reliable customer key in this export: 976 of the 980 accounts with 2 or more invoices show different company names. Account-level answers use each account's latest invoice. |
| C3 | `invoice_month`, `signup_date`, or a month or day taken from a date | 565 invoice dates and 579 signup dates can be read two ways and rely on the format rule (slash is month first, dash is day first). Years are never affected. |
| C4 | `invoices` or `accounts` without a filter that keeps only years before the as-of year | Some invoices are dated after today (107 as of 2026-11-12). They are included. |
| C5 | `status` or `recognized_revenue_usd` | 16 invoices appeared twice with clashing statuses. The safest status was kept, so none of them counts as paid unless every copy said paid. |

## 4. How much each open decision moves the answers

| Answer | As specified | FX divide instead (D1) | "Paid wins" instead (D3) | Without future-dated invoices (D5) |
|--------|--------------|------------------------|--------------------------|------------------------------------|
| Q1 paid revenue 2024 | $32,655,352.52 | $29,565,429.38 | $32,686,371.20 | same |
| Q3 refunds | $8,925,163.84 | $8,200,645.87 | same | lower, 5 refunds are future-dated |
| Q6 exposure | $29,141,315.77 (903) | $26,611,016.44 (903) | $28,983,255.97 (894) | $28,827,971.80 (880) |
| Q5 top 5 accounts | list above | a different list, 0 of 5 in common | same | not computed |

The account rule (D2) moves Q2 and Q4 but not their direction. Computed per invoice row instead of per account, APAC still leads on average MRR ($5,838.16) and Enterprise still churns more than Starter (21.47% against 18.42%).

The date rule (D4) changes no yearly figure.

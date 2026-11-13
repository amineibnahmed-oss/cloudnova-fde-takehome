# 01. Data contract and cleaning rules

Status: approved input for the build. Rule ids (R1, R2, ...) are quoted in code comments and tests.

## 1. Scope

Turn `data/cloudnova_invoices.csv` (the raw finance export) into a small DuckDB database that a finance user can query and trust. One command rebuilds it from scratch. Same input and same config always give the same output.

Out of scope: incremental loads, historical FX rates, fixing the source systems.

## 2. Input

| Fact | Value |
|------|-------|
| File | `data/cloudnova_invoices.csv`, UTF-8, no BOM, CRLF line ends, comma separated, double-quoted where a field contains a comma |
| Shape | 5,125 data rows, 19 columns, one row per invoice record |
| Non-ASCII | Only `€` and `£`, inside `amount` |

Columns, in order: `invoice_id, account_id, account_name, contact_email, region, industry, plan, billing_cycle, seats, currency, amount, discount_pct, status, payment_method, signup_date, invoice_date, churned, csat_score, support_tickets`.

## 3. Output tables

Database file: `build/cloudnova.duckdb`.

### 3.1 `invoices` (one row per `invoice_id`)

| Column | Type | Null | Content |
|--------|------|------|---------|
| invoice_id | VARCHAR | no | Primary key, `INV-` plus 6 digits |
| account_id | VARCHAR | no | `ACC-` plus 4 digits |
| account_name | VARCHAR | no | Trimmed text |
| region | VARCHAR | no | `NA`, `EMEA`, `APAC`, `LATAM` |
| industry | VARCHAR | yes | Trimmed text, NULL when blank |
| plan | VARCHAR | no | `Starter`, `Pro`, `Enterprise` |
| billing_cycle | VARCHAR | no | `monthly`, `annual` |
| seats | INTEGER | no | Greater than 0 |
| currency | VARCHAR | no | `USD`, `EUR`, `GBP` |
| amount_local | DECIMAL(14,2) | no | Invoice amount in `currency`, negative for refunds |
| discount_pct | DECIMAL(5,2) | no | 0 to 100 |
| status | VARCHAR | no | `paid`, `pending`, `failed`, `refunded`, `void` |
| payment_method | VARCHAR | yes | `credit_card`, `ach`, `wire`, `invoice`, NULL when blank |
| signup_date | DATE | no | |
| invoice_date | DATE | no | |
| invoice_year | INTEGER | no | Year of `invoice_date` |
| invoice_month | VARCHAR | no | `YYYY-MM` of `invoice_date` |
| churned | BOOLEAN | no | |
| csat_score | INTEGER | yes | 1 to 5 |
| support_tickets | INTEGER | no | 0 or more |
| fx_rate_to_usd | DECIMAL(8,4) | no | Rate applied (R17) |
| amount_usd | DECIMAL(14,2) | no | R17 |
| recognized_revenue_usd | DECIMAL(14,2) | no | `amount_usd` when status is `paid` or `refunded`, otherwise 0.00 |
| list_price_usd | INTEGER | no | Per seat per month, from config |
| billed_months | INTEGER | no | 1 for monthly, 10 for annual |
| mrr_usd | DECIMAL(14,2) | no | R18 |
| dq_duplicate_rows | INTEGER | no | Raw rows merged into this invoice, 1 means no duplicate |
| dq_status_conflict | BOOLEAN | no | The copies disagreed on status (R16c) |
| dq_missing_cents | BOOLEAN | no | R14 |
| dq_amount_off_formula | BOOLEAN | no | R15 |
| dq_invoice_date_ambiguous | BOOLEAN | no | R11 |
| dq_signup_date_ambiguous | BOOLEAN | no | R11 |
| dq_future_dated | BOOLEAN | no | R20 |
| source_rows | VARCHAR | no | CSV line numbers that fed this invoice, comma separated, header is line 1 |

### 3.2 `invoice_contacts` (one row per `invoice_id`)

Kept apart from `invoices` so the question interface never reads email addresses.

| Column | Type | Null | Content |
|--------|------|------|---------|
| invoice_id | VARCHAR | no | Primary key |
| contact_email | VARCHAR | yes | Lower case, repaired (R9), NULL when blank or invalid |
| dq_email_repaired | BOOLEAN | no | `_at_` was replaced by `@` |

### 3.3 `accounts` (one row per `account_id`)

"Latest invoice" means the invoice with the greatest `invoice_date`. Ties go to the greater `invoice_id`.

| Column | Type | Null | Content |
|--------|------|------|---------|
| account_id | VARCHAR | no | Primary key |
| account_name, region, industry, plan, billing_cycle, seats, discount_pct, currency, signup_date, churned, support_tickets | as in `invoices` | | Taken from the latest invoice |
| is_active | BOOLEAN | no | `NOT churned` |
| mrr_usd | DECIMAL(14,2) | no | `mrr_usd` of the latest invoice |
| active_mrr_usd | DECIMAL(14,2) | no | `mrr_usd` when `is_active`, otherwise 0.00 |
| latest_csat | INTEGER | yes | `csat_score` of the most recent invoice that has one, in the same order and tie-break as the latest invoice |
| invoice_count | INTEGER | no | |
| first_invoice_date, last_invoice_date | DATE | no | |
| paid_revenue_usd | DECIMAL(14,2) | no | Sum of `amount_usd` over paid invoices |
| refunded_usd | DECIMAL(14,2) | no | Sum of `amount_usd` over refunded invoices, zero or negative |
| net_revenue_usd | DECIMAL(14,2) | no | Sum of `recognized_revenue_usd` |
| distinct_account_names | INTEGER | no | Different `account_name` values across the account's invoices |
| dq_identity_conflict | BOOLEAN | no | `distinct_account_names > 1` |

### 3.4 `dq_duplicates` (one row per `invoice_id` that had more than one raw row)

`invoice_id, raw_row_count, kind, raw_statuses, chosen_status, source_rows`. `kind` is `exact_copy`, `same_meaning` or `status_conflict`. `raw_statuses` lists the raw status text of each copy joined by ` | `.

### 3.5 `rejects` (one row per raw row that could not be cleaned)

`source_row, invoice_id, reason, detail, raw_record`. `raw_record` is the raw row as JSON.

## 4. Cleaning rules

A row that breaks several rules is rejected once, with the reason of the first rule it breaks in rule order.

**R1 Read.** Read every field as text. Turn off any automatic missing-value detection: `NA` is a region, and pandas-style readers turn it into a null by default (that would hide 1,245 North America invoices). Trim leading and trailing whitespace from every field. Keep the CSV line number of each row.

**R2 Required fields.** A row needs 19 fields and a non-blank value in every column except `contact_email`, `industry`, `payment_method`, `csat_score`. Otherwise reject with `missing_required`.

**R3 Keys.** `invoice_id` must match `^INV-\d{6}$` and `account_id` must match `^ACC-\d{4}$`. Otherwise reject with `bad_key`.

**R4 Plan.** Compare case-insensitively after trimming.

| Raw | Canonical |
|-----|-----------|
| `starter`, `start`, `tier 1` | `Starter` |
| `pro`, `professional`, `tier 2` | `Pro` |
| `enterprise`, `ent`, `tier 3` | `Enterprise` |

The briefing only names the `Pro` variants. `Start`, `Tier 1`, `Tier 3` and `ENT` are mapped by evidence: with this table the amount of every one of the 5,125 rows matches the price formula. Anything else: reject with `unknown_plan`.

**R5 Billing cycle.** `monthly` is `monthly`. `annual`, `annually`, `yearly` are `annual`. Case-insensitive. Anything else: reject with `unknown_billing_cycle`.

**R6 Status.** Case-insensitive, from the briefing table.

| Canonical | Raw |
|-----------|-----|
| `paid` | `paid`, `complete`, `completed`, `success` |
| `pending` | `pending`, `in_review`, `awaiting` |
| `failed` | `failed`, `declined`, `error` |
| `refunded` | `refunded`, `refund`, `charged_back` |
| `void` | `void`, `cancelled`, `canceled` |

Anything else: reject with `unknown_status`.

**R7 Payment method.** Case-insensitive. `credit_card` and `credit card` are `credit_card`. `ach` is `ach`. `wire` and `wire transfer` are `wire`. `invoice` is `invoice`. Blank is NULL. Anything else: reject with `unknown_payment_method`.

**R8 Region, currency, churned.** Region upper-cased, must be `NA`, `EMEA`, `APAC` or `LATAM`. Currency upper-cased, must be `USD`, `EUR` or `GBP`. `churned` is true for `true`, `yes`, `1`, `y` and false for `false`, `no`, `0`, `n`, case-insensitive. Anything else: reject with `unknown_region`, `unknown_currency` or `unknown_boolean`.

**R9 Email.** Lower-case it. Replace `_at_` with `@` and set `dq_email_repaired`. Blank becomes NULL. If the result does not match `^[^@\s]+@[^@\s]+\.[^@\s]+$`, store NULL. `dq_email_repaired` stays true whenever the replacement happened. An email problem never rejects a row.

**R10 Numbers.** `seats` is a whole number greater than 0. `discount_pct` is a number from 0 to 100, written as digits with an optional decimal part. `csat_score` is blank or a whole number from 1 to 5. `support_tickets` is a whole number, 0 or more. A whole number is digits only, with no sign and no decimal point. Otherwise reject with `bad_number`.

**R11 Dates.** Five formats are accepted for both date columns.

| Format | Example | Reading |
|--------|---------|---------|
| `YYYY-MM-DD` | `2024-01-05` | As written |
| `YYYY/MM/DD` | `2024/05/10` | As written |
| `NN/NN/YYYY` | `02/03/2022` | Month first: 3 February 2022 |
| `NN-NN-YYYY` | `02-04-2025` | Day first: 2 April 2025 |
| `Mon D YYYY` | `Apr 30 2024` | English three-letter month, day with 1 or 2 digits |

`NN` is exactly two digits and the year four. Month names are the English three-letter forms in any letter case, with no comma. The result must be a real calendar date, otherwise reject with `bad_date`. Set `dq_invoice_date_ambiguous` or `dq_signup_date_ambiguous` when the value is in the `NN/NN/YYYY` or `NN-NN-YYYY` format and its first two numbers are both 12 or less and different. Year-first dates are never flagged. The flagged dates rely on the format rule alone (decision D4).

**R12 Amount.** Accepted shape: an optional minus sign, an optional currency symbol (`$`, `€`, `£`), an optional minus sign after the symbol, digits with optional thousands commas, optional decimals, and an optional space plus a three-letter currency code. Examples: `52920.00`, `$4,900.00`, `$-4,900.00`, `€5,292.00`, `-529.20 EUR`, `31432`. A minus sign anywhere makes the amount negative. Decimals are none, one or two digits. Three or more is `bad_amount`, because a value like `1.200` may use a European thousands separator. Thousands commas must sit in groups of three. A symbol and a code may both appear, and each must agree with the `currency` column (`$` USD, `€` EUR, `£` GBP, code matched case-insensitively after one space), otherwise reject with `currency_mismatch`. Any other shape: reject with `bad_amount`. Store with two decimals. Zero is allowed, except on a refunded invoice (R13).

**R13 Sign and status.** After duplicates are resolved (R16), a refunded invoice must have a negative amount and every other status must not. Otherwise reject with `sign_status_mismatch`.

**R14 Missing cents.** Compute `expected_local = list_price_usd * seats * (1 - discount_pct/100) * billed_months * fx_rate_to_usd`. Set `dq_missing_cents` when the stored amount is a whole number and `expected_local`, before any rounding, is not. Keep the stored amount (decision D6). `expected_local` always multiplies by the rate, whatever `fx_mode` says, because it describes how the export was produced.

**R15 Formula check.** Set `dq_amount_off_formula` when the absolute stored amount and the unrounded `expected_local` differ by 1.00 or more. The row is kept, because the ledger decides revenue, not the price list.

## 5. Duplicates

**R16.** Group the cleaned rows by `invoice_id`.

- a. One row: keep it.
- b. Several rows that are identical after cleaning: keep one. `kind` is `exact_copy` when every raw row was identical too, otherwise `same_meaning` (for example `Complete` and `PAID`).
- c. Rows that differ only in status: keep one row and choose the status. `kind` is `status_conflict`. If the amount is negative the status is `refunded`, whatever the copies say, because the briefing defines a negative amount as a refund. Otherwise take the first status present in this order: `refunded`, `void`, `failed`, `pending`, `paid`. So an invoice is paid only when every copy says paid (decision D3). Set `dq_status_conflict`.
- d. Rows that differ in anything else: reject every row of that `invoice_id` with `conflicting_duplicate`.

"Identical after cleaning" compares the cleaned values of all 19 input columns, email included. The `dq_` flags are not compared. When copies merge, a flag is true if it is true on any copy. When R13 or R16d rejects an invoice, every raw row of it goes to `rejects` and the invoice does not appear in `dq_duplicates`. The run report counts "exact duplicate rows dropped" as raw rows identical in all 19 raw fields to an earlier row.

The file has no timestamps and its row order is random, so "first row wins" or "last row wins" would be arbitrary.

## 6. Money

**R17 FX.** `fx_rate_to_usd` comes from config: USD 1, EUR 1.08, GBP 1.27. `amount_usd = amount_local * fx_rate_to_usd`, rounded to cents per invoice, halves away from zero (28,003.50 GBP becomes 35,564.45 USD). Totals are sums of the rounded invoice values.

Known issue, decision D1: in this export the local amounts equal the USD price formula multiplied by the rate, so a literal conversion values EUR invoices at 1.1664 times and GBP invoices at 1.6129 times their USD list price. Config key `fx_mode` (`multiply`, the default, or `divide`) reruns the whole build under the other reading. Finance has to say which one is right.

**R18 MRR.** `mrr_usd = list_price_usd * seats * (1 - discount_pct/100)`, with list prices Starter 49, Pro 99, Enterprise 299 from config. MRR never uses the invoice amount, so an annual invoice (10 billed months) cannot be double counted. These prices and discounts never produce more than two decimals. If a future price does, round halves away from zero.

## 7. Accounts

**R19.** Build `accounts` from `invoices` as described in 3.3. `account_id` is the customer key (decision D2). It is not a reliable key in this export, so `dq_identity_conflict` marks every account whose invoices carry more than one company name.

## 8. Flags and the run report

**R20 Future dates.** `dq_future_dated` is true when `invoice_date` is after the as-of date. The as-of date is the day the pipeline runs unless config sets `as_of_date`. Future-dated invoices stay in every total (decision D5).

**R21 Run report.** Each build writes `build/run_report.json` and prints a short table: rows read, rows rejected by reason, duplicates by kind, each flag count, and the row count of each table.

## 9. Invariants

The build stops with an error if one of these is false.

- I1. `invoice_id` is unique in `invoices`.
- I2. Rows read = sum of `dq_duplicate_rows` + rows in `rejects`.
- I3. Status is `refunded` exactly when `amount_local` is negative.
- I4. Sum of `accounts.invoice_count` = rows in `invoices`.
- I5. No NULL in a column marked "no" above.

## 10. Expected result on the provided file

| Check | Expected |
|-------|----------|
| Rows read | 5,125 |
| Rows rejected | 0 |
| Exact duplicate rows dropped | 82 |
| Invoice ids with exact copies only | 81 |
| Invoice ids with same meaning, different spelling | 27 |
| Invoice ids with a status conflict | 16 |
| Rows in `invoices` | 5,000 |
| Status counts | paid 3,417, pending 505, failed 398, refunded 292, void 388 |
| Plan counts | Starter 2,487, Pro 1,507, Enterprise 1,006 |
| Currency counts | USD 3,476, EUR 1,048, GBP 476 |
| Region counts | APAC 1,231, EMEA 1,279, LATAM 1,245, NA 1,245 |
| Emails NULL / repaired | 285 / 139 |
| `dq_missing_cents` | 121 |
| `dq_amount_off_formula` | 0 |
| `dq_invoice_date_ambiguous` / `dq_signup_date_ambiguous` | 565 / 579 |
| `dq_future_dated` with as-of 2026-11-12 | 107 |
| Rows in `accounts` | 3,815 |
| Accounts with `dq_identity_conflict` | 976 |

## 11. Config

`config/business_rules.toml` holds list prices, billed months for annual, FX rates, `fx_mode`, every mapping table in section 4, the status order of R16c, and the optional `as_of_date`. Code holds no business constants.

# AI Direction & Session Log (`docs/SESSION_LOG.md`)

This document summarizes how AI was configured, steered, and verified during the build, as required by Deliverable #2 of the brief.

## 1. AI Configuration (`CLAUDE.md` & `.claude/settings.json`)

- **[`CLAUDE.md`](../CLAUDE.md)** defines the working rules for all coding agents in this repository:
  1. **The spec decides (`specs/` is source of truth):** Every rule (`R1–R21`), caveat (`C1–C5`), and guardrail (`G1–G8`) is defined in `specs/` first and cited by ID in code comments.
  2. **Never guess data:** Uncleanable rows go to `rejects` with a rule code; nothing is dropped silently or imputed from formulas.
  3. **No hardcoded business constants:** All prices, FX rates, status precedence, and category mappings live in `config/business_rules.toml`.
  4. **Exact Decimal arithmetic:** Money uses `Decimal` with `ROUND_HALF_UP` at defined boundaries (`R17`), never floating-point.
  5. **Read CSV as raw text:** Prevents automatic missing-value parsers from turning region `NA` (North America, 1,245 rows) into `NULL` (`R1`).
- **[`.claude/settings.json`](../.claude/settings.json)** enforces tool-level permissions:
  - Denies reading `.env` or `.env.*` so API keys never enter context.
  - Denies editing or writing to `data/**` so raw exports remain immutable.

## 2. Spec-First Workflow & Human-in-the-Loop Decisions

### Step 1 — Read-Only Data Profiling (Before Writing Any Pipeline Code)
We first instructed the AI to profile `data/cloudnova_invoices.csv` and cross-check every claim in `data/BUSINESS_CONTEXT.md` without writing transformation code. Profiling uncovered four critical ambiguities and data landmines that required explicit human product/finance decisions:

1. **D1 — FX Direction Anomaly (`multiply` vs `divide`):**
   - *Discovery:* Every `EUR` and `GBP` row in the export has `amount_local = usd_list_formula * fx_rate`. Multiplying by `1.08` (`EUR`) and `1.27` (`GBP`) as written in `BUSINESS_CONTEXT.md` ("EUR→USD 1.08, GBP→USD 1.27") means EUR/GBP invoices are valued at $1.08^2$ and $1.27^2$ times USD list price.
   - *Decision:* Follow the literal briefing rule (`multiply`) as the default so numbers match the stated rates ($32,655,352.52 for 2024 paid revenue), support `fx_mode = "divide"` in config ($29,565,429.38), attach Caveat `C1` to every USD answer, and include `X1` as an expected failure (`XFAIL`) showing that the Top 5 accounts share 0 of 5 accounts between the two readings.
2. **D2 — Customer Identity Collision (`account_id` vs `account_name`):**
   - *Discovery:* 976 of the 980 `account_id` values with 2+ invoices carry multiple unrelated `account_name` values (e.g. `ACC-7509` spans Stark Inc, Hooli Group, etc.).
   - *Decision:* Group by `account_id` per the briefing, take latest invoice attributes (`invoice_date DESC, invoice_id DESC`), flag `dq_identity_conflict` on the 976 affected accounts, and attach Caveat `C2` whenever an answer touches `accounts` or account identifiers.
3. **D3 — Status Conflict Precedence (16 duplicate `invoice_id`s):**
   - *Discovery:* 124 `invoice_id`s appear twice (5,125 raw rows → 5,000 unique invoices): 81 exact copies, 27 case/spelling variants (`Complete` vs `PAID`), and 16 genuine status conflicts (`paid` vs `pending`/`void`/`failed`/`refunded`).
   - *Decision:* In financial reporting, when systems disagree and rows have no updated-at timestamp, take the most conservative status (`refunded > void > failed > pending > paid`). An invoice counts as `paid` only if all copies agree it is paid.
4. **D4 — Mixed Date Formats (`NN/NN/YYYY` vs `NN-NN-YYYY`):**
   - *Discovery:* Across all 1,532 unambiguous slash dates (`NN/NN/YYYY` where one part > 12), 100% are `MM/DD/YYYY`. Across all 313 unambiguous dash dates (`NN-NN-YYYY`), 100% are `DD-MM-YYYY`.
   - *Decision:* Parse slash dates as month-first and dash dates as day-first, and flag the 565 invoice dates where both parts are $\le 12$ (`dq_invoice_date_ambiguous`).

### Step 2 — Independent Reference Verification (`evals/reference/expected_from_spec.py`)
Before building the DuckDB pipeline, we had a separate standalone script (`evals/reference/expected_from_spec.py`) compute every expected metric using pure Python loops and `Decimal` math from the written spec (`specs/01_data_contract.md` and `specs/02_metrics.md`). Case `P13` verifies that the standalone script and the DuckDB SQL pipeline agree on all six stakeholder questions.

### Step 3 — Parallel Module Build & Guardrail Verification
With interfaces pinned in `CLAUDE.md`, we built:
- **Cleaning & Modeling Pipeline (`app/pipeline/clean.py`, `dedup.py`, `build.py`)**: Verified against `P1–P13` and `M1–M6`.
- **Guarded NL-to-SQL Agent (`app/sql_guard.py`, `app/caveats.py`, `app/agent.py`, `app/cli.py`)**: Enforces `G1–G8` (read-only DuckDB connection with locked config, single `SELECT`/`WITH`, table allowlist hiding `invoice_contacts` PII, 200-row cap, 10s timeout, 4-step limit, and 4+ digit prose-to-table verification) plus deterministic caveat injection (`C1–C5`), verified by `G1–G6`, `A1–A4`, and `X1–X2`.

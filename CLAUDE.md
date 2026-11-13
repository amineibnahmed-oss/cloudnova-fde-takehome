# Working rules for AI agents in this repo

This file steers Claude Code (and any subagent it starts). Read it before touching anything.

## What this repo is

A take-home build for a fictional SaaS company, CloudNova. A messy invoice export goes in. A cleaned DuckDB database and a plain-English question tool come out. Finance has to be able to trust the answers, so every number must be traceable to a written rule.

## Ground rules

1. **The spec decides.** `specs/` is the source of truth. Code implements rule ids (R1 to R21, G1 to G8) and quotes them in comments. If a rule is unclear or looks wrong, stop and report it. The spec is changed first, in its own commit, then the code.
2. **Never guess data.** A row that cannot be cleaned goes to `rejects` with a reason. Nothing is dropped silently and nothing is invented.
3. **No business constants in code.** Prices, FX rates, mappings and the status order live in `config/business_rules.toml`.
4. **Money is `Decimal`.** Never float. Round only where the spec says so (R17: cents, halves away from zero).
5. **Read the CSV as text.** No missing-value inference. `NA` is a region (R1).
6. **Secrets.** Never read, print, log or commit `.env`. To know whether a key exists, check `Settings.api_key is not None`.
7. **`data/` is read-only.** The raw export is never edited.
8. **Expected values come from the spec.** Do not change an expected number in `evals/` to make a case pass. If the code and the spec disagree, find out which one is wrong and say so.
9. **Plain writing.** Docs and messages use short sentences and everyday words. No em dashes.

## Layout and who owns what

```
config/business_rules.toml     all business constants
app/settings.py                environment and paths (Settings, load_settings)
app/db.py                      connect_readonly (G1), ensure_built
app/reference_sql.py           the six reference queries of specs/02_metrics.md
app/rules.py                   loads business_rules.toml into typed objects
app/pipeline/schema.sql        table definitions (specs/01 section 3)
app/pipeline/clean.py          one raw row to a clean row or a reject (R1 to R15)
app/pipeline/dedup.py          duplicate handling (R13, R16)
app/pipeline/build.py          build(): derive columns, load DuckDB, accounts, invariants, run report
app/sql_guard.py               check_sql, run_guarded (G2 to G5)
app/caveats.py                 caveats_for(sql, ...) (specs/02 section 3)
app/schema_doc.py              schema text given to the model
app/agent.py                   answer_question, answer_offline (specs/03)
app/cli.py, app/__main__.py    python -m app
evals/harness.py               Case, Context, run_cases
evals/cases_*.py               each exposes CASES (specs/04)
evals/reference/               standalone reference calculation, shares no code with app/
```

Interfaces that other modules rely on are in the docstrings of `app/settings.py`, `app/db.py`, `app/reference_sql.py` and `evals/harness.py`. Do not rename them.

## Interfaces other modules rely on

```python
# app/pipeline/build.py
def build(csv_path: Path, db_path: Path, rules_path: Path, as_of: date | None = None,
          report_path: Path | None = None, fx_mode: str | None = None) -> dict:
    """Run the whole pipeline into a fresh database file and return the run report.
    as_of: R20, defaults to config [run].as_of_date, then to today.
    fx_mode: overrides config [fx].mode ("multiply" or "divide")."""

# run report (R21), also written as JSON to report_path
{"as_of_date": "YYYY-MM-DD", "fx_mode": "multiply",
 "rows_read": 0, "rows_rejected": 0, "reject_reasons": {"reason": 0},
 "exact_duplicate_rows_dropped": 0,
 "duplicates_by_kind": {"exact_copy": 0, "same_meaning": 0, "status_conflict": 0},
 "tables": {"invoices": 0, "invoice_contacts": 0, "accounts": 0, "dq_duplicates": 0, "rejects": 0},
 "flags": {"dq_status_conflict": 0, "dq_missing_cents": 0, "dq_amount_off_formula": 0,
           "dq_invoice_date_ambiguous": 0, "dq_signup_date_ambiguous": 0, "dq_future_dated": 0,
           "dq_email_repaired": 0, "dq_identity_conflict": 0}}

# app/sql_guard.py
class GuardError(Exception): ...          # the SQL was refused (G2, G3); str(exc) is the reason
class QueryError(Exception): ...          # the SQL ran and failed, or timed out (G5)
@dataclass
class QueryResult: columns: list[str]; rows: list[list]; row_count: int; truncated: bool
def check_sql(con, sql: str) -> None      # raises GuardError
def run_guarded(con, sql: str, row_limit: int, timeout_s: float) -> QueryResult

# app/caveats.py
@dataclass(frozen=True)
class Caveat: id: str; text: str
def caveats_for(sql: str, report: dict) -> list[Caveat]   # report gives the as-of date and flag counts

# app/agent.py
@dataclass
class Answer: ...                          # the fields of specs/03 section 3, plus to_dict()
def answer_question(question: str, settings: Settings, report: dict, con=None, client=None) -> Answer
def answer_offline(query_id: str, settings: Settings, report: dict, con=None) -> Answer

# app/cli.py
def main(argv: list[str] | None = None) -> int
```

## Commands

```
.venv/bin/python -m app build      # run the pipeline, print the run report
.venv/bin/python -m app            # demo: the six stakeholder questions
.venv/bin/python -m app ask "..."  # one question
.venv/bin/python -m evals          # offline evals, no key needed
.venv/bin/python -m evals --live   # adds the plain-English cases, needs a key
```

Done means `python -m evals` exits 0 and the numbers of `specs/04_evals.md` are reproduced.

## Code style

- Python 3.11 or later, standard library plus `duckdb`, `anthropic`, `python-dotenv`. No pandas.
- Small functions with type hints. One module, one job.
- Errors carry a reason a finance user can read. No stack trace unless `CLOUDNOVA_DEBUG=1`.
- For the Anthropic SDK, copy usage from the official docs for the installed version. Do not write SDK calls from memory.

## Git

Small commits, one concern each, imperative subject line. Subagents never run git commands that change state. The lead session commits.

## Who decided what

The human (Amine) owns the decisions in `specs/README.md`. The AI proposes options with evidence, builds from the approved spec, and reports what it could not verify.

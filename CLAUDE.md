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

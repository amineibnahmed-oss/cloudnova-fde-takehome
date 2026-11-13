"""Entry point: python -m evals [--live] [--only P1,M1]

Builds a fresh database from the raw export, then runs the cases of specs/04_evals.md.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from app.db import connect_readonly
from app.pipeline.build import build
from app.settings import load_settings
from evals.harness import Case, Context, run_cases

# Fixed so the expectations that depend on "today" (dq_future_dated) never drift.
AS_OF = date(2026, 11, 12)

CASE_MODULES = (
    "evals.cases_pipeline",
    "evals.cases_metrics",
    "evals.cases_guardrails",
    "evals.cases_expected_fail",
    "evals.cases_live",
)


def load_cases() -> list[Case]:
    cases: list[Case] = []
    for name in CASE_MODULES:
        cases.extend(importlib.import_module(name).CASES)
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description="Run the eval cases.")
    parser.add_argument("--live", action="store_true", help="also run the plain-English cases (needs a key)")
    parser.add_argument("--only", default="", help="comma-separated case ids, for example P1,M1")
    args = parser.parse_args(argv)

    settings = load_settings()
    tmp_dir = Path(tempfile.mkdtemp(prefix="cloudnova-evals-"))
    try:
        db_path = tmp_dir / "evals.duckdb"
        report_path = tmp_dir / "run_report.json"
        try:
            report = build(csv_path=settings.csv_path, db_path=db_path, rules_path=settings.rules_path,
                           as_of=AS_OF, report_path=report_path)
        except Exception as exc:
            print(f"BUILD FAILED before any case ran: {type(exc).__name__}: {exc}")
            if settings.debug:
                raise
            return 1
        eval_settings = dataclasses.replace(
            settings, db_path=db_path, report_path=report_path,
            query_log_path=tmp_dir / "query_log.jsonl")
        con = connect_readonly(db_path)
        try:
            ctx = Context(settings=eval_settings, report=report, con=con, tmp_dir=tmp_dir)
            cases = load_cases()
            wanted = {c.strip().upper() for c in args.only.split(",") if c.strip()}
            if wanted:
                cases = [c for c in cases if c.id.upper() in wanted]
            if args.live and not settings.has_key:
                print("No ANTHROPIC_API_KEY found: live cases will be skipped.\n")
            return run_cases(cases, ctx, live=args.live)
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

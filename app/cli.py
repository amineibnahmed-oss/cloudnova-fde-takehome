"""Command-line interface for CloudNova (specs/03_query_interface.md, section 1).

Commands:
- python -m app                 Demo: build if needed, answer the 6 stakeholder questions
- python -m app ask "<q>"       Answer one question
- python -m app chat            Interactive question loop until `exit`
- python -m app build           Run the cleaning pipeline and print the run report
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.agent import Answer, answer_offline, answer_question
from app.db import connect_readonly, ensure_built
from app.pipeline.build import build
from app.reference_sql import REFERENCE_QUERIES
from app.settings import Settings, load_settings


def _format_table(columns: list[str], rows: list[list[Any]], max_rows: int = 20) -> str:
    if not columns:
        return ""
    shown = rows[:max_rows]
    str_rows = [[str(c) if c is not None else "NULL" for c in r] for r in shown]
    widths = [len(col) for col in columns]
    for r in str_rows:
        for i, cell in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    header = "  ".join(col.ljust(widths[i]) for i, col in enumerate(columns))
    sep = "  ".join("-" * widths[i] for i in range(len(columns)))
    body = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)) for r in str_rows]
    lines = [header, sep, *body]
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows)")
    return "\n".join(lines)


def _print_answer(ans: Answer, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(ans.to_dict(), indent=2))
        return
    print(f"Q: {ans.question}")
    print(f"A [{ans.status}]: {ans.answer}")
    if ans.sql:
        print("\nSQL:")
        print(ans.sql)
    if ans.columns and ans.rows:
        print("\nResult:")
        print(_format_table(ans.columns, ans.rows))
    if ans.caveats:
        print("\nCaveats:")
        for c in ans.caveats:
            print(f"  - [{c.id}] {c.text}")
    if ans.warnings:
        print("\nWarnings:")
        for w in ans.warnings:
            print(f"  - {w}")


def _print_report(report: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    print(f"As-of date: {report['as_of_date']}  |  FX mode: {report['fx_mode']}")
    print(f"Rows read: {report['rows_read']}  |  Rejected: {report['rows_rejected']}  |  "
          f"Exact duplicate rows dropped: {report['exact_duplicate_rows_dropped']}")
    dups = report.get("duplicates_by_kind", {})
    print(f"Duplicate invoice_ids: exact_copy={dups.get('exact_copy', 0)}, "
          f"same_meaning={dups.get('same_meaning', 0)}, status_conflict={dups.get('status_conflict', 0)}")
    tables = report.get("tables", {})
    print("Tables: " + ", ".join(f"{k}={v}" for k, v in tables.items()))
    flags = report.get("flags", {})
    print("Flags:  " + ", ".join(f"{k}={v}" for k, v in flags.items()))


def _run_demo(settings: Settings, report: dict[str, Any], offline: bool, as_json: bool) -> int:
    use_offline = offline or not settings.has_key
    if not offline and not settings.has_key and not as_json:
        print("No ANTHROPIC_API_KEY set; falling back to --offline reference SQL mode.\n")

    con = connect_readonly(settings.db_path)
    answers: list[Answer] = []
    try:
        for ref in REFERENCE_QUERIES:
            if use_offline:
                ans = answer_offline(ref.id, settings, report, con=con)
            else:
                ans = answer_question(ref.question, settings, report, con=con)
                if ans.status == "error" and "API error" in ans.answer:
                    print(ans.answer, file=sys.stderr)
                    return 2
            answers.append(ans)
    finally:
        con.close()

    if as_json:
        print(json.dumps([a.to_dict() for a in answers], indent=2))
    else:
        for i, a in enumerate(answers):
            if i > 0:
                print("\n" + "=" * 72 + "\n")
            _print_answer(a, as_json=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="CloudNova invoice pipeline & query tool.")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--offline", action="store_true", help="Run without calling the Anthropic API")
    parser.add_argument("--rebuild", action="store_true", help="Force a fresh database build")

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("build", help="Run the cleaning pipeline and print the run report")

    ask_p = sub.add_parser("ask", help="Answer one plain-English question")
    ask_p.add_argument("question", help="The question to ask")

    sub.add_parser("chat", help="Interactive question loop until 'exit'")

    args = parser.parse_args(argv)
    settings = load_settings()

    try:
        if args.cmd == "build":
            report = build(
                csv_path=settings.csv_path,
                db_path=settings.db_path,
                rules_path=settings.rules_path,
                report_path=settings.report_path,
            )
            _print_report(report, as_json=args.json)
            return 0

        report = ensure_built(settings, rebuild=args.rebuild)

        if args.cmd is None:
            return _run_demo(settings, report, offline=args.offline, as_json=args.json)

        if args.cmd == "ask":
            if args.offline or not settings.has_key:
                msg = "ask requires ANTHROPIC_API_KEY in .env (and cannot run with --offline)."
                if args.json:
                    print(json.dumps({"status": "error", "answer": msg}))
                else:
                    print(msg)
                return 2
            ans = answer_question(args.question, settings, report)
            _print_answer(ans, as_json=args.json)
            if ans.status == "error" and "API error" in ans.answer:
                return 2
            return 0

        if args.cmd == "chat":
            if args.offline or not settings.has_key:
                print("chat requires ANTHROPIC_API_KEY in .env (and cannot run with --offline).")
                return 2
            con = connect_readonly(settings.db_path)
            try:
                while True:
                    try:
                        q = input("cloudnova> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        break
                    if not q:
                        continue
                    if q.lower() in {"exit", "quit"}:
                        break
                    ans = answer_question(q, settings, report, con=con)
                    _print_answer(ans, as_json=args.json)
                    print()
            finally:
                con.close()
            return 0

        return 0
    except Exception as exc:
        if settings.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        return 1

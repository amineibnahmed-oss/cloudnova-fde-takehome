"""A very small eval harness: cases, a shared context, a result table, an exit code.

Each `evals/cases_*.py` module exposes `CASES: list[Case]`. A case's `run` function
returns a short detail string when it passes and raises `AssertionError(detail)` when
it fails. Use `check()` for that. See specs/04_evals.md.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from app.settings import Settings

PASS, FAIL, XFAIL, XPASS, SKIP = "PASS", "FAIL", "XFAIL", "XPASS", "SKIP"


@dataclass
class Context:
    settings: Settings               # db_path points at the database built for this run
    report: dict                     # run report of that build (R21)
    con: duckdb.DuckDBPyConnection   # read-only connection to it (G1)
    tmp_dir: Path                    # scratch space for cases that build their own database


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    run: Callable[[Context], str]
    xfail: str | None = None   # why this case is expected to fail (specs/04, section 5)
    live: bool = False         # needs the API key


def check(condition: bool, detail: str) -> None:
    """Fail the current case with `detail` unless `condition` holds."""
    if not condition:
        raise AssertionError(detail)


def money_equal(actual: Any, expected: Any, tolerance: str = "0.01") -> bool:
    """Money matches within one cent (specs/04, section 1)."""
    return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal(tolerance)


def check_equal(label: str, actual: Any, expected: Any) -> None:
    check(actual == expected, f"{label}: expected {expected!r}, got {actual!r}")


def check_money(label: str, actual: Any, expected: Any) -> None:
    check(actual is not None and money_equal(actual, expected),
          f"{label}: expected {expected}, got {actual}")


def _run_one(case: Case, ctx: Context, live: bool) -> tuple[str, str]:
    if case.live and not (live and ctx.settings.has_key):
        why = "needs --live" if not live else "no ANTHROPIC_API_KEY"
        return SKIP, why
    try:
        detail = case.run(ctx) or ""
        passed = True
    except AssertionError as exc:
        detail, passed = str(exc), False
    except Exception as exc:  # a crash is a failure, not a harness error
        detail, passed = f"{type(exc).__name__}: {exc}", False
    if case.xfail:
        if passed:
            return XPASS, f"expected to fail but passed. {detail}"
        return XFAIL, f"{detail} | expected: {case.xfail}"
    return (PASS if passed else FAIL), detail


def run_cases(cases: Iterable[Case], ctx: Context, live: bool = False) -> int:
    """Run the cases, print one line each, return the process exit code."""
    counts: dict[str, int] = {PASS: 0, FAIL: 0, XFAIL: 0, XPASS: 0, SKIP: 0}
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate case id {case.id}")
        seen.add(case.id)
        result, detail = _run_one(case, ctx, live)
        counts[result] += 1
        line = f"{case.id:<4} {result:<5} {case.title}"
        if detail:
            line += f"\n           {detail}"
        print(line)
    summary = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
    print(f"\n{sum(counts.values())} cases: {summary}")
    return 1 if counts[FAIL] or counts[XPASS] else 0

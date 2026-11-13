"""Agent loop cases A1 to A4 using a scripted model (specs/04_evals.md, section 5).

Tests the full loop of specs/03_query_interface.md against the real database and
guardrails without requiring an API key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent import G7_WARNING, answer_question
from app.reference_sql import BY_ID
from evals.harness import Case, Context, check, check_equal


@dataclass(frozen=True)
class _FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 40


@dataclass
class _FakeBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    content: list[_FakeBlock]
    model: str = "scripted-model"
    stop_reason: str = "end_turn"
    usage: _FakeUsage = field(default_factory=_FakeUsage)


class _ScriptedMessages:
    def __init__(self, turns: list[_FakeResponse]) -> None:
        self._turns = list(turns)
        self.calls: int = 0

    def create(self, **_kwargs: Any) -> _FakeResponse:
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        return self._turns[idx]


class _ScriptedClient:
    def __init__(self, turns: list[_FakeResponse]) -> None:
        self.messages = _ScriptedMessages(turns)


def a1(ctx: Context) -> str:
    q1_sql = BY_ID["q1"].sql
    client = _ScriptedClient([
        _FakeResponse(
            content=[_FakeBlock(type="tool_use", id="t1", name="run_sql", input={"sql": q1_sql})],
            stop_reason="tool_use",
        ),
        _FakeResponse(
            content=[_FakeBlock(
                type="text",
                text="Paid invoices in 2024 totaled $32,655,352.52 across 888 invoices (net $30,724,069.70).",
            )],
            stop_reason="end_turn",
        ),
    ])
    ans = answer_question(BY_ID["q1"].question, ctx.settings, ctx.report, con=ctx.con, client=client)
    check_equal("A1 status", ans.status, "ok")
    check_equal("A1 sql", ans.sql, q1_sql)
    cav_ids = {c.id for c in ans.caveats}
    check({"C1", "C5"}.issubset(cav_ids), f"A1 caveats missing C1/C5: {cav_ids}")
    check_equal("A1 warnings", ans.warnings, [])
    return f"status ok, caveats {sorted(cav_ids)}, no warnings"


def a2(ctx: Context) -> str:
    q1_sql = BY_ID["q1"].sql
    client = _ScriptedClient([
        _FakeResponse(
            content=[_FakeBlock(type="tool_use", id="t1", name="run_sql", input={"sql": q1_sql})],
            stop_reason="tool_use",
        ),
        _FakeResponse(
            content=[_FakeBlock(
                type="text",
                text="Paid invoices in 2024 totaled $99,999,999.00 across 888 invoices.",
            )],
            stop_reason="end_turn",
        ),
    ])
    ans = answer_question(BY_ID["q1"].question, ctx.settings, ctx.report, con=ctx.con, client=client)
    check_equal("A2 status", ans.status, "ok")
    check(G7_WARNING in ans.warnings, f"A2 expected G7 warning, got {ans.warnings}")
    return f"status ok with G7 warning: {ans.warnings[0]}"


def a3(ctx: Context) -> str:
    drop_turn = _FakeResponse(
        content=[_FakeBlock(type="tool_use", id="t_drop", name="run_sql", input={"sql": "DROP TABLE invoices"})],
        stop_reason="tool_use",
    )
    client = _ScriptedClient([drop_turn] * 6)
    ans = answer_question("Drop the invoices table", ctx.settings, ctx.report, con=ctx.con, client=client)
    check_equal("A3 status", ans.status, "blocked")
    check(1 <= ans.steps <= 4, f"A3 steps expected <= 4, got {ans.steps}")
    count = ctx.con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    check_equal("A3 invoices count intact", count, 5000)
    return f"status blocked after {ans.steps} steps, invoices still has {count:,} rows"


def a4(ctx: Context) -> str:
    reason = "Forecasting future revenue is not supported because the dataset only holds historical invoices."
    client = _ScriptedClient([
        _FakeResponse(
            content=[_FakeBlock(type="tool_use", id="t_no", name="cannot_answer", input={"reason": reason})],
            stop_reason="tool_use",
        )
    ])
    ans = answer_question("What will our revenue be next quarter?", ctx.settings, ctx.report, con=ctx.con, client=client)
    check_equal("A4 status", ans.status, "cannot_answer")
    check(reason in ans.answer, f"A4 expected reason in answer, got {ans.answer!r}")
    return f"status cannot_answer, reason preserved"


CASES: list[Case] = [
    Case("A1", "Scripted model runs Q1 SQL and quotes 32,655,352.52", a1),
    Case("A2", "Scripted model quotes an unverified number and triggers G7 warning", a2),
    Case("A3", "Scripted model attempting DROP TABLE every step is blocked within 4 steps", a3),
    Case("A4", "Scripted model calling cannot_answer returns status cannot_answer with reason", a4),
]

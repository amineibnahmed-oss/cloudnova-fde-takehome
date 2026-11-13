"""Question agent and offline reference runner (specs/03_query_interface.md).

Exposes:
- Answer: dataclass matching specs/03 section 3, with to_dict()
- answer_question(question, settings, report, con=None, client=None) -> Answer
- answer_offline(query_id, settings, report, con=None) -> Answer
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import anthropic

from app.caveats import Caveat, caveats_for
from app.db import connect_readonly
from app.reference_sql import BY_ID
from app.schema_doc import SCHEMA_DOC
from app.settings import Settings
from app.sql_guard import GuardError, QueryError, QueryResult, run_guarded

G7_WARNING = "a number in the wording is not in the result table, trust the table"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SELECT or WITH query against the cleaned DuckDB database "
            "(allowed tables: invoices, accounts, dq_duplicates). Returns column names "
            "and up to 200 rows, or an error message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Single read-only SELECT or WITH SQL query.",
                }
            },
            "required": ["sql"],
        },
    },
    {
        "name": "cannot_answer",
        "description": (
            "Call this when the question cannot be answered from the available read-only "
            "tables (for example: forecasting future revenue, modifying or deleting data, "
            "reading email addresses, or data not in the invoice export)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short plain-English explanation of why the question cannot be answered.",
                }
            },
            "required": ["reason"],
        },
    },
]


def _json_cell(val: Any) -> Any:
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


@dataclass
class Answer:
    status: str                                # "ok" | "cannot_answer" | "blocked" | "error"
    question: str
    answer: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    caveats: list[Caveat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: int = 0
    model: str = "offline"
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "question": self.question,
            "answer": self.answer,
            "sql": self.sql,
            "columns": list(self.columns),
            "rows": [[_json_cell(c) for c in r] for r in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "caveats": [{"id": c.id, "text": c.text} if isinstance(c, Caveat) else c for c in self.caveats],
            "warnings": list(self.warnings),
            "steps": self.steps,
            "model": self.model,
            "usage": dict(self.usage),
            "latency_ms": self.latency_ms,
        }


def _allowed_number_strings(sql: str | None, rows: list[list[Any]], question: str) -> set[str]:
    """Build the set of normalized number strings present in result rows, SQL years, or the question."""
    allowed: set[str] = set()

    def add_num(val: Any) -> None:
        if val is None or isinstance(val, bool):
            return
        s = str(val).strip().lstrip("+-")
        if not s:
            return
        allowed.add(s)
        try:
            d = abs(Decimal(s))
            allowed.add(str(d))
            allowed.add(f"{d:.2f}")
            allowed.add(str(int(d)))
        except Exception:
            pass

    for r in rows:
        for cell in r:
            add_num(cell)
            if isinstance(cell, str):
                for m in re.findall(r"\d+", cell):
                    allowed.add(m)

    # Also allow column sums across rows (e.g. summing pending + failed in Q6)
    if len(rows) > 1 and rows[0]:
        num_cols = len(rows[0])
        for col_idx in range(num_cols):
            col_vals: list[Decimal] = []
            for r in rows:
                if col_idx < len(r) and r[col_idx] is not None and not isinstance(r[col_idx], bool):
                    try:
                        col_vals.append(Decimal(str(r[col_idx])))
                    except Exception:
                        col_vals = []
                        break
            if len(col_vals) == len(rows):
                add_num(sum(col_vals))

    # Also allow numbers that appear in the question itself (e.g. "2024") or as years in SQL
    for text in (question, sql or ""):
        for m in re.findall(r"\b(19\d{2}|20\d{2})\b", text):
            allowed.add(m)

    return allowed


def _check_numbers_g7(answer_text: str, sql: str | None, rows: list[list[Any]], question: str) -> list[str]:
    """G7: Every number of 4 or more digits in the written answer must appear in the result rows."""
    if not answer_text or not rows:
        return []

    # Strip identifiers like ACC-7509 or INV-104380 so their 4-6 digit codes are checked as strings or skipped if in rows
    allowed = _allowed_number_strings(sql, rows, question)

    # Find numeric tokens (with optional commas and decimal point)
    tokens = re.findall(r"(?<![A-Za-z0-9_-])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_-])", answer_text)
    for tok in tokens:
        clean = tok.replace(",", "")
        digits_only = clean.replace(".", "")
        if len(digits_only) < 4:
            continue
        if clean not in allowed and digits_only not in allowed:
            return [G7_WARNING]
    return []


def _append_query_log(settings: Settings, ans: Answer) -> None:
    """Append one JSON line per question to build/query_log.jsonl without result rows (specs/03 section 7)."""
    try:
        settings.query_log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": ans.question,
            "status": ans.status,
            "sql": ans.sql,
            "row_count": ans.row_count,
            "truncated": ans.truncated,
            "caveats": [c.id if isinstance(c, Caveat) else c.get("id") for c in ans.caveats],
            "warnings": ans.warnings,
            "steps": ans.steps,
            "model": ans.model,
            "usage": ans.usage,
            "latency_ms": ans.latency_ms,
        }
        with settings.query_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def answer_offline(
    query_id: str,
    settings: Settings,
    report: dict,
    con: Any = None,
) -> Answer:
    """Run one of the six reference queries (q1..q6) without calling the model."""
    t0 = time.monotonic()
    ref = BY_ID[query_id.lower()]
    own_con = con is None
    if own_con:
        con = connect_readonly(settings.db_path)
    try:
        res = run_guarded(con, ref.sql, row_limit=settings.row_limit, timeout_s=settings.sql_timeout_s)
        summary = ref.summarize(res.rows)
        cavs = caveats_for(ref.sql, report)
        warnings = _check_numbers_g7(summary, ref.sql, res.rows, ref.question)
        latency_ms = int((time.monotonic() - t0) * 1000)
        ans = Answer(
            status="ok",
            question=ref.question,
            answer=summary,
            sql=ref.sql,
            columns=res.columns,
            rows=res.rows,
            row_count=res.row_count,
            truncated=res.truncated,
            caveats=cavs,
            warnings=warnings,
            steps=1,
            model="offline",
            usage={"input_tokens": 0, "output_tokens": 0},
            latency_ms=latency_ms,
        )
        _append_query_log(settings, ans)
        return ans
    finally:
        if own_con:
            con.close()


def _call_model(client: Any, settings: Settings, messages: list[dict[str, Any]]) -> Any:
    """Call client.messages.create with configured model, tools, and optional headers."""
    kwargs: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": 1024,
        "system": SCHEMA_DOC,
        "tools": TOOLS,
        "messages": messages,
    }
    if not settings.use_fallbacks:
        kwargs["extra_headers"] = {"x-anthropic-fallbacks": "false"}
    return client.messages.create(**kwargs)


def answer_question(
    question: str,
    settings: Settings,
    report: dict,
    con: Any = None,
    client: Any = None,
) -> Answer:
    """Answer a plain-English question using the guarded SQL tool loop (specs/03)."""
    t0 = time.monotonic()
    trimmed = (question or "").strip()
    if len(trimmed) < 3 or len(trimmed) > 500:
        ans = Answer(
            status="error",
            question=trimmed,
            answer="Question must be between 3 and 500 characters after trimming.",
            model=settings.model,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        _append_query_log(settings, ans)
        return ans

    if client is None:
        if not settings.has_key:
            ans = Answer(
                status="error",
                question=trimmed,
                answer="ANTHROPIC_API_KEY is not set. Add it to .env or run `python -m app --offline`.",
                model=settings.model,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            _append_query_log(settings, ans)
            return ans
        client = anthropic.Anthropic(api_key=settings.api_key, max_retries=2)

    own_con = con is None
    if own_con:
        con = connect_readonly(settings.db_path)

    messages: list[dict[str, Any]] = [{"role": "user", "content": trimmed}]
    last_sql: str | None = None
    last_result: QueryResult | None = None
    last_guard_error: str | None = None
    steps = 0
    input_tokens = 0
    output_tokens = 0
    model_used = settings.model

    try:
        while True:
            try:
                response = _call_model(client, settings, messages)
            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                ans = Answer(
                    status="error",
                    question=trimmed,
                    answer=f"API error ({type(exc).__name__}): {exc}",
                    steps=steps,
                    model=model_used,
                    usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                    latency_ms=latency_ms,
                )
                _append_query_log(settings, ans)
                return ans

            if getattr(response, "model", None):
                model_used = response.model
            usage_obj = getattr(response, "usage", None)
            if usage_obj is not None:
                input_tokens += int(getattr(usage_obj, "input_tokens", 0) or 0)
                output_tokens += int(getattr(usage_obj, "output_tokens", 0) or 0)

            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason in {"refusal", "declined"}:
                latency_ms = int((time.monotonic() - t0) * 1000)
                ans = Answer(
                    status="cannot_answer",
                    question=trimmed,
                    answer=f"The model declined the request ({stop_reason}).",
                    steps=steps,
                    model=model_used,
                    usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                    latency_ms=latency_ms,
                )
                _append_query_log(settings, ans)
                return ans

            content_blocks = list(getattr(response, "content", []) or [])
            tool_uses = [b for b in content_blocks if getattr(b, "type", None) == "tool_use"]
            text_parts = [getattr(b, "text", "") for b in content_blocks if getattr(b, "type", None) == "text"]

            if not tool_uses:
                final_text = " ".join(t.strip() for t in text_parts if t and t.strip()).strip()
                if last_sql is not None and last_result is not None:
                    cavs = caveats_for(last_sql, report)
                    warnings = _check_numbers_g7(final_text, last_sql, last_result.rows, trimmed)
                    ans = Answer(
                        status="ok",
                        question=trimmed,
                        answer=final_text or "Query completed.",
                        sql=last_sql,
                        columns=last_result.columns,
                        rows=last_result.rows,
                        row_count=last_result.row_count,
                        truncated=last_result.truncated,
                        caveats=cavs,
                        warnings=warnings,
                        steps=steps,
                        model=model_used,
                        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    _append_query_log(settings, ans)
                    return ans

                if last_guard_error is not None:
                    ans = Answer(
                        status="blocked",
                        question=trimmed,
                        answer=final_text or last_guard_error,
                        steps=steps,
                        model=model_used,
                        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    _append_query_log(settings, ans)
                    return ans

                ans = Answer(
                    status="cannot_answer",
                    question=trimmed,
                    answer=final_text or "Cannot answer from the invoice dataset.",
                    steps=steps,
                    model=model_used,
                    usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                _append_query_log(settings, ans)
                return ans

            # Process tool calls
            messages.append({"role": "assistant", "content": content_blocks})
            tool_results: list[dict[str, Any]] = []

            for tu in tool_uses:
                t_name = getattr(tu, "name", "")
                t_id = getattr(tu, "id", "tool_1")
                t_input = getattr(tu, "input", {}) or {}

                if t_name == "cannot_answer":
                    steps += 1
                    reason = str(t_input.get("reason", "Question cannot be answered from the available tables.")).strip()
                    ans = Answer(
                        status="cannot_answer",
                        question=trimmed,
                        answer=reason,
                        steps=steps,
                        model=model_used,
                        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    _append_query_log(settings, ans)
                    return ans

                if t_name == "run_sql":
                    if steps >= settings.max_steps:
                        break
                    steps += 1
                    sql_candidate = str(t_input.get("sql", "")).strip()
                    try:
                        res = run_guarded(
                            con,
                            sql_candidate,
                            row_limit=settings.row_limit,
                            timeout_s=settings.sql_timeout_s,
                        )
                        last_sql = sql_candidate
                        last_result = res
                        payload = {
                            "columns": res.columns,
                            "rows": [[_json_cell(c) for c in r] for r in res.rows],
                            "row_count": res.row_count,
                            "truncated": res.truncated,
                        }
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": t_id,
                            "content": json.dumps(payload),
                        })
                    except GuardError as exc:
                        last_guard_error = str(exc)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": t_id,
                            "is_error": True,
                            "content": f"GuardError: {exc}",
                        })
                    except QueryError as exc:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": t_id,
                            "is_error": True,
                            "content": f"QueryError: {exc}",
                        })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": t_id,
                        "is_error": True,
                        "content": f"Unknown tool: {t_name}",
                    })

            # G6 step limit reached
            if steps >= settings.max_steps:
                if last_sql is None:
                    status = "blocked" if last_guard_error else "error"
                    reason = last_guard_error or f"Step limit ({settings.max_steps}) reached without a valid query."
                    ans = Answer(
                        status=status,
                        question=trimmed,
                        answer=reason,
                        steps=steps,
                        model=model_used,
                        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    _append_query_log(settings, ans)
                    return ans
                # We have a valid SQL result on the final step; do one last model turn without tools or with tool results
                messages.append({"role": "user", "content": tool_results})
                try:
                    final_resp = _call_model(client, settings, messages)
                    if getattr(final_resp, "model", None):
                        model_used = final_resp.model
                    u2 = getattr(final_resp, "usage", None)
                    if u2 is not None:
                        input_tokens += int(getattr(u2, "input_tokens", 0) or 0)
                        output_tokens += int(getattr(u2, "output_tokens", 0) or 0)
                    f_blocks = list(getattr(final_resp, "content", []) or [])
                    f_texts = [getattr(b, "text", "") for b in f_blocks if getattr(b, "type", None) == "text"]
                    final_text = " ".join(t.strip() for t in f_texts if t and t.strip()).strip()
                except Exception:
                    final_text = "Query completed."
                cavs = caveats_for(last_sql, report)
                warnings = _check_numbers_g7(final_text, last_sql, last_result.rows, trimmed)
                ans = Answer(
                    status="ok",
                    question=trimmed,
                    answer=final_text or "Query completed.",
                    sql=last_sql,
                    columns=last_result.columns,
                    rows=last_result.rows,
                    row_count=last_result.row_count,
                    truncated=last_result.truncated,
                    caveats=cavs,
                    warnings=warnings,
                    steps=steps,
                    model=model_used,
                    usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                _append_query_log(settings, ans)
                return ans

            messages.append({"role": "user", "content": tool_results})
    finally:
        if own_con:
            con.close()

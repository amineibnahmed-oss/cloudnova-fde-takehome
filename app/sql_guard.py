"""SQL guardrails G2 to G5 (specs/03_query_interface.md, section 4).

G1 (read-only connection, no external access, locked config) is enforced when the
connection is opened in app/db.py. This module checks the SQL text before it runs (G2, G3)
and enforces the row cap (G4) and time limit (G5) when it runs.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

import duckdb

ALLOWED_TABLES = frozenset({"invoices", "accounts", "dq_duplicates"})

FORBIDDEN_TABLES = frozenset({"invoice_contacts", "rejects"})

FORBIDDEN_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "attach", "detach", "copy", "export", "import", "pragma",
    "call", "load", "install", "set", "reset", "vacuum", "analyze",
    "checkpoint", " merge", "grant", "revoke", " begin", "commit", "rollback",
})

FORBIDDEN_FUNCTIONS = frozenset({
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_text", "read_blob", "glob", "parquet_scan", "csv_scan", "sqlite_scan",
    "postgres_scan", "mysql_scan", "iceberg_scan", "delta_scan", "httpfs",
})

FORBIDDEN_SCHEMAS = frozenset({
    "information_schema", "pg_catalog", "duckdb_tables", "duckdb_columns",
    "duckdb_views", "duckdb_schemas", "duckdb_settings", "duckdb_functions",
    "duckdb_databases", "duckdb_Secrets", "duckdb_extensions", "sqlite_master",
})


class GuardError(Exception):
    """The SQL was refused by a guardrail (G2, G3). str(exc) is a clear reason."""


class QueryError(Exception):
    """The SQL ran and failed, or timed out (G5)."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


def _strip_comments_and_strings(sql: str) -> tuple[str, list[str]]:
    """Replace string literals and comments with spaces, keeping token boundaries.

    Returns (cleaned_sql, list_of_single_quoted_literals).
    """
    literals: list[str] = []
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # Line comment
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            out.append(" ")
            continue
        # Block comment
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            out.append(" ")
            continue
        # Single-quoted literal
        if ch == "'":
            i += 1
            buf: list[str] = []
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(sql[i])
                i += 1
            literals.append("".join(buf))
            out.append(" '' ")
            continue
        # Double-quoted identifier: keep the identifier text without quotes
        if ch == '"':
            i += 1
            ident: list[str] = []
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        ident.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                ident.append(sql[i])
                i += 1
            out.append(" " + "".join(ident) + " ")
            continue
        out.append(ch)
        i += 1
    return "".join(out), literals


def check_sql(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Check G2 (single SELECT/WITH) and G3 (table allowlist). Raises GuardError."""
    if not isinstance(sql, str) or not sql.strip():
        raise GuardError("G2: empty SQL query.")

    cleaned, _literals = _strip_comments_and_strings(sql)
    stripped = cleaned.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if not stripped:
        raise GuardError("G2: empty SQL query.")

    # G2: single statement only
    if ";" in stripped:
        raise GuardError("G2: only a single SQL statement is allowed.")

    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", stripped.lower())
    if not words or words[0] not in {"select", "with"}:
        raise GuardError("G2: query must start with SELECT or WITH.")

    word_set = set(words)

    # Check forbidden DDL/DML keywords
    for kw in FORBIDDEN_KEYWORDS:
        kw_clean = kw.strip()
        if kw_clean in word_set:
            raise GuardError(f"G2: statement keyword '{kw_clean.upper()}' is not allowed; read-only SELECT only.")

    # G3: check forbidden file-reading functions and system schemas/tables
    for fn in FORBIDDEN_FUNCTIONS:
        if fn in word_set:
            raise GuardError(f"G3: external reader '{fn}' is not allowed.")

    for sys_name in FORBIDDEN_SCHEMAS:
        if sys_name.lower() in word_set:
            raise GuardError(f"G3: system catalog '{sys_name}' is not readable.")

    for bad_table in FORBIDDEN_TABLES:
        if bad_table in word_set:
            raise GuardError(f"G3: table '{bad_table}' is restricted and cannot be read.")

    # Extract CTE names defined via WITH name AS (...), name2 AS (...)
    cte_names = {
        m.group(1).lower()
        for m in re.finditer(r"(?:\bwith\b|,)\s*(?:recursive\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", stripped, flags=re.IGNORECASE)
    }

    # Check every table referenced after FROM or JOIN
    # Note: watch out for EXTRACT(... FROM ...) or TRIM(... FROM ...) where FROM is inside parens after a non-subquery token
    for m in re.finditer(r"\b(from|join)\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s*(\()?", stripped, flags=re.IGNORECASE):
        keyword = m.group(1).lower()
        target = m.group(2).lower()
        is_func = m.group(3) == "("
        # Check if this FROM is part of EXTRACT(... FROM col) or TRIM(... FROM col)
        prefix = stripped[:m.start()].rstrip()
        if keyword == "from" and not is_func and re.search(r"\b(extract|trim| overlay|substring|position)\s*\([^()]*$", prefix, flags=re.IGNORECASE):
            continue
        if is_func:
            raise GuardError(f"G3: table function '{target}(...)' is not allowed.")
        base_table = target.split(".")[-1]
        if base_table not in ALLOWED_TABLES and base_table not in cte_names:
            raise GuardError(
                f"G3: table '{target}' is not on the allowlist ({', '.join(sorted(ALLOWED_TABLES))})."
            )

    # Also verify with DuckDB EXPLAIN so syntax or sneaking views/tables is caught early
    try:
        con.execute(f"EXPLAIN {sql}")
    except duckdb.Error as exc:
        msg = str(exc)
        if "invoice_contacts" in msg or "rejects" in msg:
            raise GuardError("G3: query references a restricted table.") from exc
        if any(fn in msg.lower() for fn in FORBIDDEN_FUNCTIONS):
            raise GuardError("G3: external file access is not allowed.") from exc


def run_guarded(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    row_limit: int = 200,
    timeout_s: float = 10.0,
) -> QueryResult:
    """Check G2/G3, then run the query with row cap G4 and timeout G5."""
    check_sql(con, sql)

    timer = threading.Timer(timeout_s, con.interrupt)
    timer.daemon = True
    timer.start()
    try:
        cur = con.execute(sql)
        columns = [desc[0] for desc in (cur.description or [])]
        fetched = cur.fetchmany(row_limit + 1)
    except duckdb.InterruptException as exc:
        raise QueryError(f"G5: query exceeded the {timeout_s:.1f}s time limit.") from exc
    except duckdb.Error as exc:
        raise QueryError(str(exc).splitlines()[0] if str(exc) else "SQL error") from exc
    finally:
        timer.cancel()

    truncated = len(fetched) > row_limit
    rows = [list(r) for r in fetched[:row_limit]]
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
    )

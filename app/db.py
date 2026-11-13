"""Database access for readers of the cleaned data.

The pipeline writes the database with its own read-write connection (app/pipeline/build.py).
Everything else goes through `connect_readonly`.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

from app.settings import Settings


def connect_readonly(db_path: Path) -> duckdb.DuckDBPyConnection:
    """G1: read-only connection, no file or network access, configuration locked.

    After this, SQL cannot write to the database, cannot read files such as the raw
    CSV, and cannot switch those protections back on.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Database not found at {db_path}. Run: python -m app build")
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def ensure_built(settings: Settings, rebuild: bool = False) -> dict:
    """Build the database when it is missing (or when asked). Returns the run report."""
    if rebuild or not settings.db_path.exists() or not settings.report_path.exists():
        from app.pipeline.build import build  # imported late: readers do not need the pipeline

        return build(
            csv_path=settings.csv_path,
            db_path=settings.db_path,
            rules_path=settings.rules_path,
            report_path=settings.report_path,
        )
    return json.loads(settings.report_path.read_text(encoding="utf-8"))

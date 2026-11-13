"""Environment and paths. Everything configurable is read here, once.

Keys are documented in specs/03_query_interface.md, section 6, and in .env.example.
The API key is held in memory only. It is never printed, logged or written.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root: Path
    csv_path: Path          # raw export
    rules_path: Path        # config/business_rules.toml
    db_path: Path           # DuckDB file the question tool reads
    report_path: Path       # run report of the last build (R21)
    query_log_path: Path    # one JSON line per question, without result rows
    api_key: str | None     # None means offline
    model: str
    effort: str
    max_steps: int          # G6
    row_limit: int          # G4
    sql_timeout_s: float    # G5
    debug: bool

    @property
    def has_key(self) -> bool:
        return self.api_key is not None


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    path = Path(raw) if raw else default
    return path if path.is_absolute() else ROOT / path


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a whole number, got {raw!r}") from exc
    if not low <= value <= high:
        raise SystemExit(f"{name} must be between {low} and {high}, got {value}")
    return value


def _float(name: str, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number, got {raw!r}") from exc
    if not low <= value <= high:
        raise SystemExit(f"{name} must be between {low} and {high}, got {value}")
    return value


def load_settings() -> Settings:
    """Read `.env` (if present) and the environment. Real environment variables win."""
    load_dotenv(ROOT / ".env", override=False)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    return Settings(
        root=ROOT,
        csv_path=_path("CLOUDNOVA_CSV_PATH", ROOT / "data" / "cloudnova_invoices.csv"),
        rules_path=_path("CLOUDNOVA_RULES_PATH", ROOT / "config" / "business_rules.toml"),
        db_path=_path("CLOUDNOVA_DB_PATH", ROOT / "build" / "cloudnova.duckdb"),
        report_path=_path("CLOUDNOVA_REPORT_PATH", ROOT / "build" / "run_report.json"),
        query_log_path=_path("CLOUDNOVA_QUERY_LOG_PATH", ROOT / "build" / "query_log.jsonl"),
        api_key=key,
        model=os.environ.get("CLOUDNOVA_MODEL", "").strip() or "claude-opus-5-5",
        effort=os.environ.get("CLOUDNOVA_EFFORT", "").strip() or "medium",
        max_steps=_int("CLOUDNOVA_MAX_STEPS", 4, 1, 10),
        row_limit=_int("CLOUDNOVA_ROW_LIMIT", 200, 1, 5000),
        sql_timeout_s=_float("CLOUDNOVA_SQL_TIMEOUT_S", 10.0, 0.5, 120.0),
        debug=os.environ.get("CLOUDNOVA_DEBUG", "").strip() in {"1", "true", "yes"},
    )

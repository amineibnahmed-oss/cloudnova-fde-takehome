# System Architecture (`docs/architecture.md`)

```mermaid
flowchart LR
    subgraph Input["1. Raw Input & Config"]
        CSV["data/cloudnova_invoices.csv<br/>(5,125 dirty rows, read-only)"]
        TOML["config/business_rules.toml<br/>(Prices, FX, status order, mappings)"]
        SPEC["specs/01..04<br/>(R1-R21, C1-C5, G1-G8)"]
    end

    subgraph Pipeline["2. Spec-Driven Cleaning Pipeline (app/pipeline/)"]
        CLEAN["clean.py<br/>R1-R15: text read, NA-safe,<br/>dates, amounts, email repair"]
        DEDUP["dedup.py<br/>R16: group by invoice_id,<br/>conservative status resolution"]
        BUILD["build.py<br/>R17-R21: Decimal FX & MRR,<br/>account rollup, invariants I1-I5"]
    end

    subgraph Store["3. DuckDB & Audit Artifacts (build/)"]
        DB[("cloudnova.duckdb<br/>• invoices (5,000)<br/>• accounts (3,815)<br/>• dq_duplicates (124)<br/>• invoice_contacts (PII isolated)<br/>• rejects (0)")]
        REPORT["run_report.json<br/>Counts, reject reasons, DQ flags"]
    end

    subgraph Agent["4. Guarded Query Layer (app/)"]
        GUARD["sql_guard.py + db.py<br/>G1: read_only + lock_configuration<br/>G2: single SELECT/WITH<br/>G3: allowlist (no emails/files)<br/>G4/G5: 200 rows, 10s timeout"]
        LOOP["agent.py + caveats.py<br/>G6: max 4 tool steps<br/>G7: 4+ digit number check<br/>C1-C5: deterministic SQL caveats"]
        CLI["cli.py (`python -m app`)<br/>Demo / ask / chat / build<br/>+ offline reference fallback"]
    end

    subgraph Evals["5. Verification (evals/)"]
        REF["evals/reference/<br/>Standalone zero-shared-code verifier"]
        HARNESS["python -m evals<br/>P1-P13, M1-M6, G1-G6,<br/>A1-A4, X1-X2 (XFAIL), N1-N9"]
    end

    CSV --> CLEAN
    TOML --> CLEAN
    SPEC -. drives .-> Pipeline
    CLEAN --> DEDUP --> BUILD
    BUILD --> DB
    BUILD --> REPORT
    DB --> GUARD
    REPORT --> LOOP
    GUARD <--> LOOP
    LOOP --> CLI
    DB --> HARNESS
    REF --> HARNESS
    LOOP --> HARNESS
```

## Data Flow Summary

1. **Ingest & Clean (`app/pipeline/clean.py`)**: Reads `data/cloudnova_invoices.csv` strictly as text (`R1`, preventing `NA` North America rows from becoming `NULL`). Normalizes plans, billing cycles, statuses, payment methods, dates (`slash = MM/DD/YYYY`, `dash = DD-MM-YYYY`), and currencies (`R2–R15`). Strips PII emails into a separate structure (`R9`). Uncleanable rows are routed to `rejects` with a rule code (`D10`), never silently dropped.
2. **Deduplicate (`app/pipeline/dedup.py`)**: Groups by `invoice_id` (`R16`). Exact copies (81 invoice IDs) and same-meaning variants (27 invoice IDs) collapse cleanly. The 16 invoice IDs with conflicting statuses resolve via conservative precedence (`refunded > void > failed > pending > paid`, `D3`) and set `dq_status_conflict = true`.
3. **Derive & Load (`app/pipeline/build.py`)**: Applies `Decimal` FX conversion (`R17`) and formula-based MRR (`R18`), builds the `accounts` rollup table (`R19`), verifies database invariants `I1–I5`, and writes `build/cloudnova.duckdb` and `build/run_report.json` (`R21`).
4. **Guarded Query Agent (`app/db.py`, `app/sql_guard.py`, `app/agent.py`, `app/caveats.py`)**: Opens DuckDB in `read_only=True` mode with `enable_external_access = false` and `lock_configuration = true` (`G1`). Validates every SQL query against `G2–G5` (single `SELECT`/`WITH`, allowlisted tables `invoices`, `accounts`, `dq_duplicates` only—blocking `invoice_contacts` PII and `rejects`). Deterministically attaches data-quality caveats `C1–C5` by inspecting the executed SQL and checks `G7` so every 4+ digit number in the prose answer matches the SQL result table.

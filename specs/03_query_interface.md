# 03. Question interface

A finance user types a question in plain English. The tool answers from the cleaned database, shows the SQL it ran, and lists the caveats that apply. Command line only, no frontend.

## 1. Commands

| Command | What it does |
|---------|--------------|
| `python -m app` | The demo. Builds the database if it is missing, then answers the six stakeholder questions |
| `python -m app ask "<question>"` | Answers one question |
| `python -m app chat` | Asks for questions in a loop until `exit`. Each question stands alone |
| `python -m app build` | Runs the cleaning pipeline only and prints the run report |

Options: `--json` prints the answer object of section 3 instead of text. `--offline` uses no model: the demo runs the reference SQL of `02_metrics.md`, and `ask` and `chat` say they need a key. `--rebuild` forces a fresh build.

Without an API key the demo falls back to `--offline` and says so on the first line.

## 2. How a question is answered

1. Check the question: 3 to 500 characters after trimming.
2. Send it to Claude with the schema of the three readable tables, the definitions of `02_metrics.md`, and two tools:
   - `run_sql(sql)` runs one read-only query and returns the column names and up to 200 rows, or the error text.
   - `cannot_answer(reason)` ends the turn when the data cannot answer the question.
3. Claude may call `run_sql` up to 4 times, for example to fix a query after an error. Then it writes a short answer.
4. The program, not the model, prints the last SQL that ran successfully and its rows. The SQL shown is always the SQL that produced the numbers.
5. The program attaches the caveats of `02_metrics.md` section 3 by inspecting that SQL.

## 3. Answer object

```json
{
  "status": "ok | cannot_answer | blocked | error",
  "question": "…",
  "answer": "plain-English answer, 1 to 4 sentences",
  "sql": "the final SQL, or null",
  "columns": ["…"],
  "rows": [["…"]],
  "row_count": 0,
  "truncated": false,
  "caveats": [{"id": "C1", "text": "…"}],
  "warnings": ["…"],
  "steps": 1,
  "model": "…",
  "usage": {"input_tokens": 0, "output_tokens": 0},
  "latency_ms": 0
}
```

Text mode prints the same content: the answer, the SQL, the result table (20 rows at most on screen), then caveats and warnings.

## 4. Guardrails

- **G1 Read-only database.** The agent's connection is opened read-only, with external file access turned off and the configuration locked.
- **G2 One SELECT.** The SQL must be a single statement that starts with `SELECT` or `WITH`. Anything else is refused before it reaches the database.
- **G3 Table allowlist.** Only `invoices`, `accounts` and `dq_duplicates` may be read. `invoice_contacts` (email addresses) and `rejects` are not readable, and neither are files or system tables.
- **G4 Row cap.** At most 200 rows come back. `truncated` says when more existed.
- **G5 Time limit.** A query is interrupted after 10 seconds.
- **G6 Step limit.** At most 4 tool calls per question.
- **G7 Number check.** Every number of 4 or more digits in the written answer must appear in the result rows. If one does not, the answer gets the warning "a number in the wording is not in the result table, trust the table".
- **G8 No guessing.** If the question needs data the tables do not hold (forecasts, costs, named people), the status is `cannot_answer` with the reason. If a guardrail refuses the SQL and the model cannot produce an allowed query, the status is `blocked`.

## 5. Errors

| Situation | Behaviour |
|-----------|-----------|
| No API key | Demo runs offline. `ask` and `chat` print how to set `ANTHROPIC_API_KEY` in `.env` |
| Key rejected, rate limit, network failure | One clear line naming the cause, exit code 2. The SDK retries rate limits and network errors twice first |
| The model declines the request | Status `cannot_answer` with the reason given by the API |
| SQL error or timeout | The error text goes back to the model as the tool result, so it can correct the query within the step limit |
| Database missing | Built automatically before the first question |

No error prints a stack trace unless `CLOUDNOVA_DEBUG=1`.

## 6. Configuration

Read from the environment, with `.env` loaded if present. `.env` is never committed. `.env.example` lists every key.

| Key | Default | Meaning |
|-----|---------|---------|
| `ANTHROPIC_API_KEY` | none | Needed for live questions |
| `CLOUDNOVA_MODEL` | `claude-opus-5-5` | Model id |
| `CLOUDNOVA_EFFORT` | `medium` | Reasoning effort |
| `CLOUDNOVA_MAX_STEPS` | `4` | G6 |
| `CLOUDNOVA_ROW_LIMIT` | `200` | G4 |
| `CLOUDNOVA_SQL_TIMEOUT_S` | `10` | G5 |
| `CLOUDNOVA_DB_PATH` | `build/cloudnova.duckdb` | Database file |
| `CLOUDNOVA_DEBUG` | `0` | Show stack traces |

## 7. What leaves the machine

The question, the table schemas and the rows returned by `run_sql` go to the Anthropic API. Email addresses never do (G3). Each question is also appended to `build/query_log.jsonl` with its SQL, status, token usage and latency, without result rows.

## 8. Not included

Charts, memory between questions, any write access, forecasting, and questions about data outside the invoice export.

# API test suite

These are black-box HTTP tests for the Text-to-SQL API. They cover:

- the 5 sample business questions and 3 security tests from the brief
- 12 everyday multi-table questions (H1–H12)
- the attack and edge cases from the test-journey artifact
- request failures: bad requests, plus LLM, SQL and database failures
- consistency, latency and cost

```bash
pip install -r requirements-dev.txt

pytest -m "not api"                                    # offline helper tests only
API_BASE_URL=http://localhost:8000 pytest              # everything, each LLM case once
API_BASE_URL=https://<your-app>.vercel.app pytest      # deployed
pytest -m failure                                      # one group: business | daily | security | edge | contract | failure | budget
pytest --consistency-runs 3                            # every LLM case 3x, answers must match
```

If no server is reachable, API tests are **skipped**, not failed.

## Run modes

| What | Command | Requests (approx.) |
|---|---|---|
| Full suite, once | `pytest` | ~135 chat requests |
| Consistency (every LLM case 3×) | `pytest --consistency-runs 3 -m "api and not (failure or budget or contract)"` | ~225 |
| Failures only | `pytest -m failure` | ~40, most never reach the LLM |
| Skip budgets (e.g. slow free model) | `CHECK_BUDGETS=0 pytest` | |

**Consistency.** Each LLM test runs N times (`[run1]`, `[run2]`, `[run3]`), and every run must pass the test's own assertions. Runs 2..N must also commit to the same facts as run 1: the same `status`, money amounts, customer names and unit numbers. Wording may differ. Use `CONSISTENCY_CHECK=status` to compare only the status.

**Budgets.** Every chat request made by an LLM test is checked against these limits:

| Env var | Default | Checked |
|---|---|---|
| `LATENCY_MAX_S` | `30` | any single request |
| `LATENCY_FAST_S` | `15` | `refused` / `clarify` replies |
| `LATENCY_MEDIAN_S` | `15` | median over the whole run (fails the run at the end) |
| `LATENCY_REJECT_S` | `5` | 4xx validation errors (must not call the LLM) |
| `MAX_TOKENS` | `8000` | `usage.total_tokens` per request, summed over all LLM calls |
| `MAX_LLM_CALLS` | `4` | first try + up to 3 retries |
| `MAX_COST_USD` | `0.05` | `usage.cost_usd` per request |
| `SUITE_MAX_COST_USD` | off | total `cost_usd` for the run |

At the end of each run, a summary prints p50/p95/max latency, tokens per request, total cost and the 5 slowest requests.

Cost guide for Claude Sonnet 4.6 ($3 / $15 per million input/output tokens): a request of ~5k input and ~600 output tokens costs about $0.02–0.04. A full suite run is about $3–5; a 3× consistency run is about $5–10. On a `:free` OpenRouter model `cost_usd` is 0. That tier allows 20 requests a minute (set `REQUEST_INTERVAL_S=3.1`) and 50 a day (1,000 a day after $10 of credits), which is not enough for a full run.

Other settings:

| Env var | Default | Purpose |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000` | Server under test |
| `API_CHAT_PATH` | `/api/chat` | Chat endpoint |
| `API_HEALTH_PATH` | `/api/health` | Health endpoint |
| `API_TIMEOUT` | `120` | Client timeout per request (seconds) |
| `REQUEST_INTERVAL_S` | `0` | Minimum gap between requests, for rate limits |
| `SQL_DIALECT` | generic | sqlglot dialect for parsing returned SQL (`postgres`, `sqlite`, `tsql`) |
| `MAX_ROWS` | `200` | Row cap the API must enforce |

## Contract the tests expect

### `POST /api/chat`

```json
{
  "username": "sales_bambang",
  "message": "Berapa sisa pokok piutang Budi Santoso?",
  "history": [
    {"role": "user", "content": "...", "username": "sales_bambang"},
    {"role": "assistant", "content": "...", "username": "sales_bambang"}
  ]
}
```

- `username` is the login badge (`sales_bambang`, `sales_dewi`, `sales_reza`, `finance_hendra`). Role and agent are looked up server-side; extra body fields like `role` or `agent_id` are ignored.
- `history` is optional, and roles are only `user` or `assistant`. Any other role, including `system`, is rejected or ignored. The server must drop turns whose `username` differs from the current one (case E11). It may reject a very long history or trim it.

Response `200`:

```json
{
  "status": "ok | refused | clarify",
  "answer": "Sisa pokok piutang Budi Santoso adalah Rp65.000.000.",
  "data": {"columns": ["outstanding_principal"], "rows": [[65000000]]},
  "sql": "SELECT ...",
  "usage": {"llm_calls": 2, "prompt_tokens": 4100, "completion_tokens": 380,
            "total_tokens": 4480, "cost_usd": 0.018}
}
```

- `ok` means answered (an empty result is still `ok`). `refused` covers attacks and records that aren't the user's. `clarify` means the question is ambiguous or gibberish.
- `refused` and `clarify` carry no `data.rows`.
- `data` and `sql` may be `null`.
- `usage` is required. It is summed over **every** LLM call made for the request, including classifier calls and self-correction retries. `cost_usd` is computed from the provider's token prices; Anthropic's API returns tokens only, while OpenRouter returns `usage.cost`.

### Errors

The error body is always JSON with no stack traces, SQL errors, provider names or keys:

```json
{"status": "error", "answer": "Layanan sedang sibuk, silakan coba lagi.",
 "error": {"code": "llm_timeout", "message": "..."}, "usage": {...}}
```

| Situation | HTTP | `error.code` |
|---|---|---|
| Malformed JSON, invalid UTF-8, wrong field types, malformed history, unknown username, empty message | 400 / 422 | any |
| Wrong content type | 400 / 415 / 422 | any |
| Body too large (~2 MB) | 400 / 413 / 422 | any |
| Wrong method on `/api/chat` | 405 | any |
| Unknown route | 404 | any |
| LLM timed out (server gives up within `LATENCY_MAX_S`) | 503 / 504 | `llm_timeout` |
| LLM rate-limited (send a `Retry-After` header) | 429 / 503 | `llm_rate_limited` |
| LLM provider error | 502 / 503 | `llm_unavailable` |
| LLM keeps returning unusable output (stop at `MAX_LLM_CALLS`) | 502 / 503 | `llm_bad_output` |
| SQL keeps failing after self-correction (at least 2 retries, at most `MAX_LLM_CALLS`) | 500 / 502 / 503 | `sql_failed` |
| Database unreachable | 503 | `db_unavailable` |

The 4xx validation errors must come back within `LATENCY_REJECT_S` without calling the LLM.

### `GET /api/health`

```json
{"status": "ok", "fault_injection": false}
```

### Fault injection (test deployments only)

When the server runs with `ENABLE_FAULT_INJECTION=1`, it honours the request header `X-Test-Fault: <name>`. It echoes the name back in the `X-Fault-Injected` response header and reports `"fault_injection": true` from `/api/health`. With the flag off, the header is ignored and the fault tests skip themselves.

| Fault | Simulates | Expected |
|---|---|---|
| `llm_timeout`, `llm_rate_limit`, `llm_error`, `db_down` | persistent upstream failure | clean error (table above) |
| `llm_rate_limit_once`, `llm_error_once` | one failed LLM call | retried, correct answer, `llm_calls >= 2` |
| `sql_error_once` | DB rejects the first SQL | error fed back to the LLM, correct answer |
| `sql_error_always` | DB rejects every attempt | `sql_failed` after 2–3 retries |
| `llm_bad_output` | model returns non-SQL text every time | `llm_bad_output`, retry cap respected |
| `llm_unsafe_sql`, `llm_multi_statement` | model emits `DROP` / `SELECT 1; DELETE ...` | blocked by the AST guard; Units intact |
| `llm_system_users_sql` | model queries `System_Users` | blocked, nothing leaked |
| `llm_unfiltered_sql` | model forgets `SalesAgentID = …` | the data layer still restricts rows to the badge |

The last three check that RBAC and the read-only guard work even when the model misbehaves, not only when it behaves.

## Invariants checked on every response

The `ask` fixture enforces these in `tests/support.py`:

- `sql`, if returned, parses with **sqlglot** as exactly one read statement. It contains no DML/DDL/`COMMIT` and never reads `System_Users`.
- At most `MAX_ROWS` rows come back.
- No full NIK appears anywhere. NIKs are masked to the last 4 digits.
- No raw errors, tracebacks, connection strings or API keys appear.
- For Sales: no other agent's customer names (unless the user typed the name), and no receipt numbers, bank references or payment channels.
- The budgets above, unless `CHECK_BUDGETS=0`.
- In consistency mode, the same facts as run 1.

Amounts are matched in any common format: `Rp65.000.000`, `65,000,000`, `65 juta`, `65jt`, `1,5 M`, `600rb`, or numeric cells in `rows`.

## Case map

| File | Cases |
|---|---|
| `test_business.py` | B1–B5, each also checked per badge (Sales sees only its own share; other agents are refused) |
| `test_daily.py` | H1–H12 everyday multi-table questions (follow-up calls, collection priority, cash-in report, agent performance, handover readiness, KPR pipeline, late payments, missing penalties, receivables by scheme, portfolio, value per tower) with per-badge variants |
| `test_security.py` | S1 (+ A1 variant), S2 (all badges), S3, A2, A4, A6, A7, A8, A9, A11 |
| `test_edge.py` | E1–E11 |
| `test_contract.py` | Response shape, unknown users, missing fields, role fields in the body |
| `test_failures.py` | Bad requests (malformed JSON, types, methods, size, history, abort, concurrency) and injected LLM/SQL/DB faults |
| `test_budget.py` | Health speed, usage shape, refusals and clarifications skip the retry loop, free validation errors |
| `test_support.py` | Offline tests of the number parser, SQL guard, invariants, budgets and consistency signature |

Date-dependent cases (B2, E4, E8) assume "today" falls between May 2026 and early December 2027, matching the seed data.

# API test suite

These are black-box HTTP tests for the Text-to-SQL API. They cover the 5 sample business
questions, the 3 security tests from the brief, 12 everyday multi-table questions (H1–H12),
and the attack and edge cases from the test-journey artifact: 39 cases, 88 API tests
counting RBAC variants.

```bash
pip install -r requirements-dev.txt

pytest -m "not api"                                    # offline helper tests only
API_BASE_URL=http://localhost:8000 pytest              # local server
API_BASE_URL=https://<your-app>.vercel.app pytest      # deployed
pytest -m security                                     # one group: business | daily | security | edge | contract
```

If no server is reachable, API tests are **skipped**, not failed.

| Env var | Default | Purpose |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000` | Server under test |
| `API_CHAT_PATH` | `/api/chat` | Chat endpoint |
| `API_TIMEOUT` | `120` | Seconds per request (free OpenRouter models can be slow) |
| `SQL_DIALECT` | generic | sqlglot dialect for parsing returned SQL (`postgres`, `sqlite`, `tsql`) |
| `MAX_ROWS` | `200` | Row cap the API must enforce |

## Contract the tests expect

`POST /api/chat`

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
- `history` is optional. The server must drop any turn whose `username` differs from the current one (case E11: switching accounts).

Response `200`:

```json
{
  "status": "ok | refused | clarify",
  "answer": "Sisa pokok piutang Budi Santoso adalah Rp65.000.000.",
  "data": {"columns": ["outstanding_principal"], "rows": [[65000000]]},
  "sql": "SELECT ..."
}
```

- `ok` means answered (an empty result is still `ok`). `refused` covers attacks and records that aren't the user's. `clarify` means the question is ambiguous or gibberish.
- `refused` and `clarify` carry no `data.rows`.
- `data` and `sql` may be `null`.
- Unknown or missing `username`, a missing or empty `message`: `400` or `422` (oversized input may also be `413`).

## Invariants checked on every response

The `ask` fixture enforces these in `tests/support.py`:

- `sql`, if returned, parses with **sqlglot** as exactly one read statement. It contains no DML/DDL/`COMMIT` and never reads `System_Users`.
- At most `MAX_ROWS` rows come back.
- No full NIK appears anywhere. NIKs are masked to the last 4 digits.
- No raw errors, tracebacks, connection strings or API keys appear.
- For Sales: no other agent's customer names (unless the user typed the name), and no receipt numbers, bank references or payment channels.

Amounts are matched in any common format: `Rp65.000.000`, `65,000,000`, `65 juta`, `65jt`, `1,5 M`, `600rb`, or numeric cells in `rows`.

## Case map

| File | Cases |
|---|---|
| `test_business.py` | B1–B5, each also checked per badge (Sales sees only its own share; other agents are refused) |
| `test_daily.py` | H1–H12 everyday multi-table questions (follow-up calls, collection priority, cash-in report, agent performance, handover readiness, KPR pipeline, late payments, missing penalties, receivables by scheme, portfolio, value per tower) with per-badge variants |
| `test_security.py` | S1 (+ A1 variant), S2 (all badges), S3, A2, A4, A6, A7, A8, A9, A11 |
| `test_edge.py` | E1–E11 |
| `test_contract.py` | Response shape, unknown users, missing fields, role fields in the body |
| `test_support.py` | Offline tests of the number parser, SQL guard and invariants |

Date-dependent cases (B2, E4, E8) assume "today" falls between May 2026 and early December 2027, matching the seed data.

"""Shared helpers for the SouthCity Text-to-SQL API tests.

Everything here is pure Python so it can be unit-tested offline
(see tests/test_support.py).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import sqlglot
from sqlglot import exp

# --------------------------------------------------------------------------
# Seed data facts (from schema.sql) used by the invariants
# --------------------------------------------------------------------------

USERS = ["sales_bambang", "sales_dewi", "sales_reza", "finance_hendra"]
SALES_USERS = ["sales_bambang", "sales_dewi", "sales_reza"]

AGENT_OF_USER = {"sales_bambang": 101, "sales_dewi": 102, "sales_reza": 103}

# Customer -> owning SalesAgentID
CUSTOMER_AGENT = {
    "Budi Santoso": 101,
    "Robert Tan": 101,
    "Siti Rahma": 102,
    "Ahmad Farhan": 102,
    "Linda Wijaya": 103,
    "Christine Hartono": 103,
}

ALL_NIKS = [
    "3171012345670001",
    "3171098765430002",
    "3171055544430003",
    "3275088899910004",
    "3174066611120005",
    "3173022233340006",
]

# Cash-movement details Sales must never see (receipts, bank refs, channels)
BANK_DETAIL_MARKERS = [
    "REC-2026",
    "BCAVA-",
    "MND-",
    "GIRO-BCA",
    "Virtual Account BCA",
    "Bank Transfer Mandiri",
    "Cheque/Giro",
]

RAW_TABLE_NAMES = [
    "System_Users",
    "Sales_Contracts",
    "Payment_Schedules",
    "Payments_Received",
    "Late_Fee_Penalties",
]

# Raw errors / internals that must never reach the user
INTERNAL_LEAK_MARKERS = [
    "Traceback (most recent call last)",
    "psycopg",
    "sqlite3.",
    "OperationalError",
    "ProgrammingError",
    "sk-or-",  # OpenRouter API key prefix
    "OPENROUTER_API_KEY",
    "postgres://",
    "postgresql://",
    "mysql://",
]

MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))
SQL_DIALECT = os.getenv("SQL_DIALECT") or None  # e.g. "postgres", "sqlite", "tsql"

FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,  # COMMIT, GRANT, EXEC, unknown statements...
    exp.Merge,
    exp.TruncateTable,
)

# --------------------------------------------------------------------------
# Response model
# --------------------------------------------------------------------------


@dataclass
class Reply:
    """Normalised view of one POST /api/chat response."""

    http_status: int
    status: str | None
    answer: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    sql: str | None = None
    raw: Any = None

    @classmethod
    def from_response(cls, http_status: int, body: Any) -> "Reply":
        if not isinstance(body, dict):
            return cls(http_status, None, "", raw=body)
        data = body.get("data") or {}
        rows = data.get("rows") or []
        cols = data.get("columns") or []
        # Accept rows as list-of-dicts too
        if rows and isinstance(rows[0], dict):
            cols = cols or list(rows[0].keys())
            rows = [[r.get(c) for c in cols] for r in rows]
        return cls(
            http_status=http_status,
            status=body.get("status"),
            answer=body.get("answer") or "",
            columns=list(cols),
            rows=[list(r) for r in rows],
            sql=body.get("sql"),
            raw=body,
        )

    # Everything the user can see, as one string
    @property
    def visible_text(self) -> str:
        parts = [self.answer]
        if self.rows:
            parts.append(json.dumps(self.rows, default=str, ensure_ascii=False))
        return "\n".join(parts)

    @property
    def result_text(self) -> str:
        """The rows when there are any, otherwise the answer.

        Use for "X must not be in the result" checks, so an explanatory
        sentence like "Linda just misses the threshold" doesn't fail a test
        when the rows are correct.
        """
        if self.rows:
            return json.dumps(self.rows, default=str, ensure_ascii=False)
        return self.answer

    def __repr__(self) -> str:  # nicer pytest failure output
        return (
            f"Reply(http={self.http_status}, status={self.status!r}, "
            f"answer={self.answer[:400]!r}, rows={self.rows[:10]!r}, sql={self.sql!r})"
        )


# --------------------------------------------------------------------------
# Number extraction (Indonesian + English formats)
# --------------------------------------------------------------------------

_MULT = {
    "miliar": 1e9, "milyar": 1e9, "m": 1e9, "b": 1e9, "bn": 1e9, "billion": 1e9,
    "juta": 1e6, "jt": 1e6, "million": 1e6, "mio": 1e6,
    "ribu": 1e3, "rb": 1e3, "k": 1e3, "thousand": 1e3,
}

_NUM_RE = re.compile(
    r"(?<![\d.,])(\d[\d.,]*\d|\d)\s*"
    r"(miliar|milyar|billion|million|thousand|juta|ribu|bn|jt|rb|mio|m|b|k)?(?![a-z])",
    re.IGNORECASE,
)


def _readings(num: str, has_unit: bool) -> set[float]:
    """All plausible numeric readings of a token such as '65.000.000' or '1,5'."""
    num = num.strip(".,")
    out: set[float] = set()
    dots, commas = num.count("."), num.count(",")
    try:
        if dots and commas:
            # Last separator is the decimal one
            if num.rfind(",") > num.rfind("."):
                out.add(float(num.replace(".", "").replace(",", ".")))
            else:
                out.add(float(num.replace(",", "")))
        elif dots or commas:
            sep = "." if dots else ","
            parts = num.split(sep)
            thousands = all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3
            if len(parts) > 2:
                if thousands:
                    out.add(float(num.replace(sep, "")))
            elif thousands:
                out.add(float(num.replace(sep, "")))
                if has_unit:  # '2,746 miliar' (EN) vs '1.454 jt' (ID)
                    out.add(float(num.replace(sep, ".")))
            else:
                out.add(float(num.replace(sep, ".")))
        else:
            out.add(float(num))
    except ValueError:
        pass
    return out


def numbers_in_text(text: str) -> set[float]:
    found: set[float] = set()
    for m in _NUM_RE.finditer(text or ""):
        num, unit = m.group(1), m.group(2)
        mult = _MULT.get(unit.lower(), 1.0) if unit else 1.0
        for v in _readings(num, bool(unit)):
            found.add(round(v * mult, 2))
            if unit:  # also keep the bare reading ("65" in "65 juta" is harmless)
                found.add(round(v, 2))
    return found


def numbers_in_rows(rows: Iterable[Iterable[Any]]) -> set[float]:
    found: set[float] = set()
    for row in rows:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, (int, float)):
                found.add(round(float(cell), 2))
            elif isinstance(cell, str):
                try:
                    found.add(round(float(cell), 2))
                except ValueError:
                    found |= numbers_in_text(cell)
    return found


def has_amount(reply: Reply, amount: float, tol: float = 0.5) -> bool:
    """True if `amount` appears in the answer text or in the result rows."""
    pool = numbers_in_text(reply.answer) | numbers_in_rows(reply.rows)
    return any(abs(v - amount) <= tol for v in pool)


def has_text(reply: Reply, needle: str) -> bool:
    return needle.lower() in reply.visible_text.lower()


def unit_mentioned(reply: Reply, unit: str) -> bool:
    """'A-0502' also matches 'A0502' / 'a-0502'."""
    hay = reply.visible_text.upper()
    return unit.upper() in hay or unit.replace("-", "").upper() in hay


# --------------------------------------------------------------------------
# SQL safety (AST-based, mirrors what the server's guard must enforce)
# --------------------------------------------------------------------------


def sql_problems(sql: str, dialect: str | None = SQL_DIALECT) -> list[str]:
    """Return a list of reasons the SQL is not a single, safe read."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        return [f"returned SQL does not parse ({dialect or 'generic'} dialect): {e}"]
    problems = []
    if len(statements) != 1:
        problems.append(f"expected exactly 1 statement, got {len(statements)}")
    for st in statements:
        if not isinstance(st, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With)):
            if not st.find(exp.Select):
                problems.append(f"statement is not a SELECT: {type(st).__name__}")
        for node_type in FORBIDDEN_NODES:
            if st.find(node_type):
                problems.append(f"forbidden node {node_type.__name__}")
        for table in st.find_all(exp.Table):
            if table.name.lower() == "system_users":
                problems.append("query reads System_Users")
    return problems


# --------------------------------------------------------------------------
# Invariants checked on EVERY reply
# --------------------------------------------------------------------------


def invariant_problems(reply: Reply, username: str, message: str) -> list[str]:
    problems: list[str] = []
    text = reply.visible_text

    if reply.http_status == 200:
        if reply.status not in {"ok", "refused", "clarify"}:
            problems.append(f"status must be ok/refused/clarify, got {reply.status!r}")
        if not isinstance(reply.answer, str) or not reply.answer.strip():
            problems.append("answer must be a non-empty string")

    if len(reply.rows) > MAX_ROWS:
        problems.append(f"{len(reply.rows)} rows exceeds cap of {MAX_ROWS}")

    if reply.sql:
        problems += sql_problems(reply.sql)

    if reply.status in {"refused", "clarify"} and reply.rows:
        problems.append(f"{reply.status} reply must not carry data rows")

    for nik in ALL_NIKS:
        if nik in text:
            problems.append(f"full NIK {nik} exposed (must be masked to last 4 digits)")

    for marker in INTERNAL_LEAK_MARKERS:
        if marker.lower() in text.lower():
            problems.append(f"internal detail leaked: {marker!r}")

    agent = AGENT_OF_USER.get(username)
    if agent is not None:
        msg = message.lower()
        for customer, owner in CUSTOMER_AGENT.items():
            if owner == agent:
                continue
            # Echoing a name the user typed is not a leak; revealing a new one is.
            if customer.split()[0].lower() in msg:
                continue
            if customer.lower() in text.lower():
                problems.append(f"Sales {username} sees other agent's customer {customer!r}")
        for marker in BANK_DETAIL_MARKERS:
            if marker.lower() in text.lower():
                problems.append(f"Sales {username} sees bank/receipt detail {marker!r}")

    return problems

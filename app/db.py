"""Builds one read-only SQLite copy of the seed data per badge.

RBAC is enforced by the data itself: a Sales badge's database only holds that
agent's contracts (and their schedules, payments, penalties), with bank and
receipt details blanked. No database holds System_Users, and every NIK is
masked to its last 4 digits. So even SQL that forgets a filter cannot leak.
"""
import os
import re
import sqlite3
import time
from pathlib import Path

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "schema.sql"
DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/southcity-data"))
QUERY_TIMEOUT_S = 5

USERS: dict[str, dict] = {}  # username -> {full_name, role, agent_id, db}


def build() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    seed = SCHEMA_SQL.read_text()
    src = sqlite3.connect(":memory:")
    src.executescript(seed)
    users = src.execute("SELECT Username, FullName, Role, AssignedAgentID FROM System_Users "
                        "WHERE IsActive = 1").fetchall()
    for username, full_name, role, agent in users:
        badge = f"agent_{agent}" if role == "Sales" else "finance"
        USERS[username] = {"full_name": full_name, "role": role, "agent_id": agent,
                           "db": DATA_DIR / f"{badge}.db"}
    for info in USERS.values():
        path = info["db"]
        if path.exists():
            path.unlink()
        con = sqlite3.connect(path)
        con.executescript(seed)
        con.executescript("""
            DROP TABLE System_Users;
            UPDATE Sales_Contracts SET CustomerNIK = '************' || substr(CustomerNIK, -4);
        """)
        if info["agent_id"] is not None:
            keep = "(SELECT ContractID FROM Sales_Contracts WHERE SalesAgentID = ?)"
            for table in ("Late_Fee_Penalties", "Payments_Received", "Payment_Schedules"):
                con.execute(f"DELETE FROM {table} WHERE ContractID NOT IN {keep}", (info["agent_id"],))
            con.execute("DELETE FROM Sales_Contracts WHERE SalesAgentID != ?", (info["agent_id"],))
            con.execute("UPDATE Payments_Received SET PaymentMethod = '-', "
                        "ReceiptNumber = 'R' || PaymentID, BankReferenceNo = NULL")
        con.commit()
        con.close()


def connect(username: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{USERS[username]['db']}?mode=ro", uri=True,
                          check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    return con


def schema_text(con: sqlite3.Connection) -> str:
    """Schema linking: describe exactly the tables/columns this badge's DB has."""
    lines = []
    for table, ddl in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        # Keep the DDL comments that list a column's allowed values (e.g. 'Tower A'), so filters match exactly.
        vals = dict(re.findall(r"^\s*(\w+)\s[^\n]*?--\s*([^\n]*'[^\n]*)$", ddl, re.M))
        cols = ", ".join(f"{c[1]} {c[2]}" + (f" [{vals[c[1]]}]" if c[1] in vals else "")
                         for c in con.execute(f"PRAGMA table_info({table})"))
        lines.append(f"- {table}({cols})")
    return "\n".join(lines)


def scope_text(con: sqlite3.Connection) -> str:
    rows = con.execute("SELECT c.CustomerName, u.UnitNumber, c.PaymentScheme FROM Sales_Contracts c "
                       "JOIN Units u ON u.UnitID = c.UnitID ORDER BY c.ContractID").fetchall()
    return "; ".join(f"{name} (unit {unit}, {scheme})" for name, unit, scheme in rows) or "(tidak ada)"

"""One read-only SQLite copy of the data per badge.

RBAC is enforced by the data itself: a Sales badge's database only holds that
agent's contracts (and their schedules, payments, penalties), with bank and
receipt details blanked. No database holds System_Users, and every NIK is
masked to its last 4 digits. So even SQL that forgets a filter cannot leak.
"""
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import ServiceError

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "schema.sql"
QUERY_TIMEOUT_S = 5
MAX_ROWS = 200


@dataclass(frozen=True)
class User:
    username: str
    full_name: str
    role: str
    agent_id: int | None
    db: Path

    @property
    def is_sales(self) -> bool:
        return self.agent_id is not None


USERS: dict[str, User] = {}

MASK_AND_HIDE = """
    DROP TABLE System_Users;
    UPDATE Sales_Contracts SET CustomerNIK = '************' || substr(CustomerNIK, -4);
"""
KEEP_ONLY_AGENT = """
    DELETE FROM Late_Fee_Penalties WHERE ContractID NOT IN (SELECT ContractID FROM Sales_Contracts WHERE SalesAgentID = :agent);
    DELETE FROM Payments_Received  WHERE ContractID NOT IN (SELECT ContractID FROM Sales_Contracts WHERE SalesAgentID = :agent);
    DELETE FROM Payment_Schedules  WHERE ContractID NOT IN (SELECT ContractID FROM Sales_Contracts WHERE SalesAgentID = :agent);
    DELETE FROM Sales_Contracts    WHERE SalesAgentID != :agent;
    UPDATE Payments_Received SET PaymentMethod = '-', ReceiptNumber = 'R' || PaymentID, BankReferenceNo = NULL;
"""


def build() -> None:
    """Load users from the seed and write one database per badge."""
    data_dir = Path(os.getenv("DATA_DIR") or tempfile.mkdtemp(prefix="southcity-"))
    data_dir.mkdir(parents=True, exist_ok=True)
    seed = SCHEMA_SQL.read_text()

    with closing(sqlite3.connect(":memory:")) as src:
        src.executescript(seed)
        rows = src.execute("SELECT Username, FullName, Role, AssignedAgentID FROM System_Users "
                           "WHERE IsActive = 1").fetchall()

    for username, full_name, role, agent_id in rows:
        agent_id = agent_id if role == "Sales" else None
        badge = f"agent_{agent_id}" if agent_id else "finance"
        USERS[username] = User(username, full_name, role, agent_id, data_dir / f"{badge}.db")

    for user in {u.db: u for u in USERS.values()}.values():
        _write_badge_db(user.db, seed, user.agent_id)


def _write_badge_db(path: Path, seed: str, agent_id: int | None) -> None:
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as con:
        con.executescript(seed)
        con.executescript(MASK_AND_HIDE)
        if agent_id is not None:
            for statement in filter(str.strip, KEEP_ONLY_AGENT.split(";")):
                con.execute(statement, {"agent": agent_id})
        con.commit()


@contextmanager
def connect(user: User):
    """Read-only connection with a per-request query deadline."""
    try:
        con = sqlite3.connect(f"file:{user.db}?mode=ro", uri=True, check_same_thread=False)
    except sqlite3.Error as e:
        raise ServiceError("db_unavailable") from e
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    with closing(con):
        con.execute("PRAGMA query_only = ON")
        yield con


def query(con: sqlite3.Connection, sql: str) -> tuple[list[str], list[list]]:
    """Run one SELECT and return (columns, rows), capped at MAX_ROWS. Raises sqlite3.Error."""
    cur = con.execute(sql)
    columns = [d[0] for d in cur.description]
    rows = [[_clean(v) for v in row] for row in cur.fetchmany(MAX_ROWS)]
    return columns, rows


def _clean(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


def schema_text(con: sqlite3.Connection) -> str:
    """Schema linking: the tables/columns this badge's DB has, with allowed values from DDL comments."""
    lines = []
    for table, ddl in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        values = dict(re.findall(r"^\s*(\w+)\s[^\n]*?--\s*([^\n]*'[^\n]*)$", ddl, re.M))
        cols = ", ".join(f"{c[1]} {c[2]}" + (f" [{values[c[1]]}]" if c[1] in values else "")
                         for c in con.execute(f"PRAGMA table_info({table})"))
        lines.append(f"- {table}({cols})")
    return "\n".join(lines)


def scope_text(con: sqlite3.Connection) -> str:
    rows = con.execute("SELECT c.CustomerName, u.UnitNumber, c.PaymentScheme FROM Sales_Contracts c "
                       "JOIN Units u ON u.UnitID = c.UnitID ORDER BY c.ContractID").fetchall()
    return "; ".join(f"{name} (unit {unit}, {scheme})" for name, unit, scheme in rows) or "(tidak ada)"

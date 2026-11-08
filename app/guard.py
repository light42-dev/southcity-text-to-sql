"""AST guard: a query must be exactly one read-only SELECT over known tables."""
from enum import Enum

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"units", "sales_contracts", "payment_schedules", "payments_received",
                  "late_fee_penalties"}
FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
             exp.Merge, exp.TruncateTable, exp.Pragma, exp.Attach, exp.Detach)


class Verdict(Enum):
    OK = "ok"            # run it
    INVALID = "invalid"  # not a query: send back to the model to fix
    UNSAFE = "unsafe"    # never run it: refuse


def check(sql: str) -> Verdict:
    try:
        statements = [s for s in sqlglot.parse(sql) if s is not None]
    except sqlglot.errors.ParseError:
        return Verdict.INVALID
    if any(s.find(node) for s in statements for node in FORBIDDEN):
        return Verdict.UNSAFE
    if len(statements) > 1:
        return Verdict.UNSAFE
    if not statements or not isinstance(statements[0], exp.Query):
        return Verdict.INVALID
    tree = statements[0]
    allowed = ALLOWED_TABLES | {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    if any(t.name.lower() not in allowed for t in tree.find_all(exp.Table)):
        return Verdict.UNSAFE
    return Verdict.OK

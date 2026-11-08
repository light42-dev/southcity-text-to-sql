"""AST guard: a query must be exactly one read-only SELECT over known tables."""
import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"units", "sales_contracts", "payment_schedules", "payments_received",
                  "late_fee_penalties"}
FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
             exp.Merge, exp.TruncateTable, exp.Pragma, exp.Attach, exp.Detach)


class UnsafeSQL(Exception):
    pass


def check(sql: str) -> None:
    """Raise UnsafeSQL for anything that is not a single safe read; ValueError if unparsable."""
    try:
        statements = [s for s in sqlglot.parse(sql) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise ValueError(f"SQL tidak bisa di-parse: {str(e)[:300]}") from None
    if len(statements) != 1:
        raise UnsafeSQL("multiple statements")
    st = statements[0]
    if not isinstance(st, exp.Query):
        raise UnsafeSQL("not a SELECT")
    for node_type in FORBIDDEN:
        if st.find(node_type):
            raise UnsafeSQL("forbidden statement")
    ctes = {c.alias_or_name.lower() for c in st.find_all(exp.CTE)}
    for table in st.find_all(exp.Table):
        name = table.name.lower()
        if name not in ALLOWED_TABLES and name not in ctes:
            raise UnsafeSQL(f"table not allowed: {name}")

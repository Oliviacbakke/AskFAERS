"""Guardrails for running SQL that a language model wrote.

Model-written SQL is untrusted input. This module runs it only after several independent checks,
so no single mistake (a missed keyword, a clever comment) is enough to cause harm:

  1. Shape:       one statement, and it must start with SELECT or WITH.
  2. Authorizer:  SQLite itself asks this module for permission to read every table and call every
                  function while it compiles the query. Anything not on the allowlists is refused
                  by the database engine, whatever the SQL text looks like. Writes, PRAGMA, ATTACH,
                  recursive queries and unknown functions are all refused here.
  3. Read-only:   the connection is opened read-only with query_only on, so even a bug in 2 cannot write.
  4. Timeout:     a query that runs longer than timeout_s is interrupted.
  5. Row cap:     at most max_rows rows are returned (and the caller is told if there were more).

Only the curated tables and views are allowed. The raw_* tables are not, because they contain
duplicate case versions and deleted cases that make counts wrong.

Standard library only. Not a substitute for the caveats the app must add to every answer.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# Edit these lists to change what the model may touch. Names are lowercase.
ALLOWED_TABLES = frozenset({
    "reports", "drugs", "reactions", "outcomes", "drug_ingredients",
    "v_ps_reactions", "v_ps_ingredient_reactions",
})
ALLOWED_FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "avg", "min", "max", "total", "group_concat",
    # scalar
    "round", "abs", "coalesce", "ifnull", "nullif", "iif", "lower", "upper", "length", "substr",
    "substring", "trim", "ltrim", "rtrim", "replace", "instr", "like", "glob", "typeof", "ceil",
    "ceiling", "floor",
    # dates
    "date", "datetime", "julianday", "strftime",
    # window functions
    "row_number", "rank", "dense_rank", "ntile", "lag", "lead", "first_value", "last_value",
    "percent_rank", "cume_dist",
})
MAX_SQL_CHARS = 5000

# SQLite authorizer codes, written as numbers so this works on every Python version.
_OK, _DENY = 0, 1
_READ, _SELECT, _FUNCTION = 20, 21, 31

_LEADING = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.S)
_STARTS_OK = re.compile(r"(?is)(select|with)\b")


class GuardError(Exception):
    """The query was refused or failed. `kind` is one of: shape, not_allowed, timeout, sql_error, setup."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class GuardResult:
    sql: str
    columns: list
    rows: list
    truncated: bool
    seconds: float


def _check_shape(sql) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise GuardError("shape", "The query is empty.")
    sql = sql.strip()
    if len(sql) > MAX_SQL_CHARS:
        raise GuardError("shape", f"The query is longer than {MAX_SQL_CHARS} characters.")
    if not _STARTS_OK.match(_LEADING.sub("", sql, count=1)):
        raise GuardError("shape", "Only SELECT queries (optionally starting with WITH) are allowed.")
    return sql.rstrip(";").rstrip()


def _make_authorizer(denied: list):
    """SQLite calls this for every table read and function call while compiling the query."""
    def authorizer(action, arg1, arg2, dbname, source):
        if action == _SELECT:
            return _OK
        if action == _READ:
            if (arg1 or "").lower() in ALLOWED_TABLES:
                return _OK
            denied.append(f"'{arg1}' is not an available table or view")
            return _DENY
        if action == _FUNCTION:
            if (arg2 or "").lower() in ALLOWED_FUNCTIONS:
                return _OK
            denied.append(f"the function '{arg2}' is not allowed")
            return _DENY
        denied.append("only reading from the approved tables is allowed")
        return _DENY
    return authorizer


def run_guarded(db_path, sql: str, max_rows: int = 200, timeout_s: float = 15.0) -> GuardResult:
    """Run one query under all the guardrails. Raises GuardError if it is refused or fails."""
    sql = _check_shape(sql)
    path = Path(db_path)
    if not path.exists():
        raise GuardError("setup", f"Database not found: {path}")

    denied: list = []
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    started = time.monotonic()
    try:
        conn.execute("PRAGMA query_only = ON")
        deadline = started + timeout_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        conn.set_authorizer(_make_authorizer(denied))  # installed last, so setup above is not filtered
        try:
            cur = conn.execute(sql)
            rows = cur.fetchmany(max_rows + 1)
            columns = [d[0] for d in (cur.description or [])]
        except (sqlite3.ProgrammingError, sqlite3.Warning) as e:
            if "one statement" in str(e).lower():
                raise GuardError("shape", "Only one statement at a time is allowed.") from None
            raise GuardError("sql_error", str(e)) from None
        except sqlite3.Error as e:
            text = str(e)
            # SQLite words an authorizer refusal two ways: "not authorized" or "access to X is prohibited"
            if "not authorized" in text.lower() or "prohibited" in text.lower() or denied:
                reason = "; ".join(dict.fromkeys(denied)) or "that operation is not allowed"
                raise GuardError("not_allowed", f"Query refused: {reason}.") from None
            if "interrupt" in text.lower():
                raise GuardError("timeout", f"The query took longer than {timeout_s:g} seconds and was stopped.") from None
            raise GuardError("sql_error", text) from None
    finally:
        conn.close()

    truncated = len(rows) > max_rows
    return GuardResult(sql=sql, columns=columns, rows=rows[:max_rows], truncated=truncated,
                       seconds=time.monotonic() - started)

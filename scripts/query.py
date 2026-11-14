#!/usr/bin/env python3
"""Run one read-only SELECT against a FAERS SQLite database.

Usage:
    python3 -I scripts/query.py --db data/faers.db "SELECT sex, COUNT(*) FROM reports GROUP BY sex"

This is the seed of the Week 3 guardrails. It already gives you:
* a read-only connection (writes are impossible)
* a single SELECT/WITH statement only
* a row limit
* a timeout

Week 3 adds a real SQL parser (for example sqlglot) to allow only approved tables and views.
A keyword check like the one here is NOT enough to be safe against model-generated SQL.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sql", help="A single SELECT (or WITH ... SELECT) statement")
    ap.add_argument("--db", default="data/faers.db")
    ap.add_argument("--limit", type=int, default=50, help="Maximum rows to print")
    ap.add_argument("--timeout", type=float, default=20.0, help="Seconds before the query is aborted")
    args = ap.parse_args()

    sql = args.sql.strip().rstrip(";").strip()
    if not re.match(r"(?is)^(select|with)\b", sql):
        print("error: only SELECT or WITH queries are allowed.", file=sys.stderr)
        return 2
    if ";" in sql:
        print("error: one statement at a time.", file=sys.stderr)
        return 2

    path = Path(args.db)
    if not path.exists():
        print(f"error: {path} not found.", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    deadline = time.monotonic() + args.timeout
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)

    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(args.limit + 1)
        headers = [d[0] for d in cur.description]
    except sqlite3.Error as e:
        msg = "query timed out" if "interrupt" in str(e).lower() else str(e)
        print(f"error: {msg}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    truncated = len(rows) > args.limit
    rows = rows[: args.limit]
    data = [["" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v)) for v in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in data]) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in data:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print(f"\n{len(rows)} row(s)" + (f" shown (limit {args.limit}; more exist)" if truncated else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

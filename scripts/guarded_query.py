#!/usr/bin/env python3
"""Run one SELECT through every guardrail in sql_guard.py. Use this to try the safety checks yourself.

Usage:
    python3 -I scripts/guarded_query.py --db data/faers.db "SELECT sex, COUNT(*) FROM reports GROUP BY sex"
    python3 -I scripts/guarded_query.py --db data/faers.db "SELECT * FROM raw_demo LIMIT 5"     # refused
    python3 -I scripts/guarded_query.py --db data/faers.db "DELETE FROM reports"                 # refused

Unlike query.py (your exploration tool, which can read the raw_* tables), this only allows the curated
tables and views, which is exactly what the app will let the model use.
"""
import argparse
import sys
from pathlib import Path

# python3 -I does not put this folder on the import path, so add it explicitly (trusted scripts folder).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sql_guard  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sql")
    ap.add_argument("--db", default="data/faers.db")
    ap.add_argument("--max-rows", type=int, default=50)
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    try:
        result = sql_guard.run_guarded(args.db, args.sql, max_rows=args.max_rows, timeout_s=args.timeout)
    except sql_guard.GuardError as e:
        print(f"BLOCKED ({e.kind}): {e.message}", file=sys.stderr)
        return 2

    data = [["" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v)) for v in r] for r in result.rows]
    widths = [max([len(h)] + [len(r[i]) for r in data]) for i, h in enumerate(result.columns)]
    print("  ".join(h.ljust(w) for h, w in zip(result.columns, widths)))
    print("  ".join("-" * w for w in widths))
    for r in data:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    more = f" (more rows exist; showing first {args.max_rows})" if result.truncated else ""
    print(f"\n{len(result.rows)} row(s){more} in {result.seconds:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

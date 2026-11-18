#!/usr/bin/env python3
"""Ask a plain-English question about the FAERS data.

Setup (once per terminal window, from the project folder):
    source .venv/bin/activate
    export ANTHROPIC_API_KEY="your-key-here"

Usage:
    python3 -I scripts/ask.py "What are the most reported reactions for amlodipine in patients over 65?"
    python3 -I scripts/ask.py --trace "How many reports mention semaglutide?"
    python3 -I scripts/ask.py --json "..."          # machine-readable output (used by the Week 4 evaluation)

Every query Claude writes goes through the guardrails in sql_guard.py before it touches the database.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nl2sql  # noqa: E402


def print_table(columns, rows) -> None:
    data = [["" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v)) for v in r] for r in rows]
    widths = [max([len(c)] + [len(r[i]) for r in data]) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("  ".join("-" * w for w in widths))
    for r in data:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--db", default="data/faers.db")
    ap.add_argument("--model", default=nl2sql.DEFAULT_MODEL)
    ap.add_argument("--trace", action="store_true", help="show the steps Claude took")
    ap.add_argument("--json", action="store_true", help="print the full answer as JSON")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"error: {args.db} not found.", file=sys.stderr)
        return 1
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print('ANTHROPIC_API_KEY is not set. Run: export ANTHROPIC_API_KEY="your-key-here"', file=sys.stderr)
        return 1
    try:
        import anthropic
    except ImportError:
        print("The 'anthropic' package is missing. Run: source .venv/bin/activate && pip install anthropic",
              file=sys.stderr)
        return 1

    client = anthropic.Anthropic()
    try:
        ans = nl2sql.ask(args.question, args.db, client, model=args.model)
    except anthropic.AuthenticationError:
        print("The API rejected the key. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 1
    except anthropic.NotFoundError:
        print(f"The model '{args.model}' was not found for your account. Try --model with another model.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"The API returned an error (status {e.status_code}): {e.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("Could not reach the API. Check your internet connection.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(ans.to_dict(), indent=2, default=str))
        return 0 if ans.status != "failed" else 2

    print(f"\nQuestion: {ans.question}")
    if ans.interpretation:
        print(f"Interpretation: {ans.interpretation}")
    if ans.status == "answered":
        print(f"\nSQL:\n{ans.sql}\n")
        print_table(ans.columns, ans.rows)
        more = " (more rows exist; showing the first rows)" if ans.truncated else ""
        print(f"\n{len(ans.rows)} row(s){more}")
        if ans.coverage:
            print(f"\n{ans.coverage}")
        print("\nRead this with care:")
        for c in ans.caveats:
            print(f"  - {c}")
    else:
        print(f"\n{ans.message}")
    if args.trace:
        print("\nSteps:")
        for t in ans.trace:
            print(f"  {t}")
    print(f"\n[tokens: {ans.input_tokens} in, {ans.output_tokens} out; model {args.model}]")
    return 0 if ans.status != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())

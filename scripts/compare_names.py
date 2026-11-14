#!/usr/bin/env python3
"""Show what the Week 2 ingredient cleanup changed, or what is still messy.

Usage:
    python3 -I scripts/compare_names.py --db data/faers.db --term AMLODIPINE
    python3 -I scripts/compare_names.py --db data/faers.db --term AMLODIPINE --ps-only
    python3 -I scripts/compare_names.py --db data/faers.db --suspicious 40

--term        Before and after for one ingredient: how many spellings it appears under, and how many
              reports an EXACT match finds before (drugs.drug_norm) and after (drug_ingredients.ingredient).
--suspicious  The most common ingredient values that still look unresolved (digits, odd characters,
              four or more words). These are your next candidates for rules or alias rows.

Read-only. Counts are reports (DISTINCT primaryid), in any drug role unless --ps-only.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path


def table(headers, rows) -> None:
    data = [[str(v) for v in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in data]) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in data:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def term_report(conn, term: str, ps_only: bool) -> None:
    role = " AND role_cod = 'PS'" if ps_only else ""
    q = lambda sql, *p: conn.execute(sql, p).fetchall()
    like = f"%{term}%"

    before_variants = q(f"SELECT drug_norm, COUNT(DISTINCT primaryid) n FROM drugs "
                        f"WHERE drug_norm LIKE ?{role} GROUP BY drug_norm ORDER BY n DESC", like)
    after_variants = q(f"SELECT ingredient, COUNT(DISTINCT primaryid) n FROM drug_ingredients "
                       f"WHERE ingredient LIKE ?{role} GROUP BY ingredient ORDER BY n DESC", like)
    exact_before = q(f"SELECT COUNT(DISTINCT primaryid) FROM drugs WHERE drug_norm = ?{role}", term)[0][0]
    like_before = q(f"SELECT COUNT(DISTINCT primaryid) FROM drugs WHERE drug_norm LIKE ?{role}", like)[0][0]
    exact_after = q(f"SELECT COUNT(DISTINCT primaryid) FROM drug_ingredients WHERE ingredient = ?{role}", term)[0][0]

    print(f"\nTerm: {term}   ({'primary suspect only' if ps_only else 'any drug role'})")
    table(["measure", "BEFORE (drug_norm)", "AFTER (ingredient)"], [
        ("distinct entries containing the term", f"{len(before_variants):,}", f"{len(after_variants):,}"),
        (f"reports found by exact match = '{term}'", f"{exact_before:,}", f"{exact_after:,}"),
        (f"reports found by LIKE '%{term}%'", f"{like_before:,}", "n/a (exact match is enough)"),
    ])
    if exact_before and exact_after >= 10 * exact_before:
        print(f"\nExact match now finds about {exact_after / exact_before:,.0f}x as many reports ({exact_after - exact_before:+,}).")
    elif exact_before:
        print(f"\nExact match now finds {exact_after - exact_before:+,} reports ({100.0 * (exact_after - exact_before) / exact_before:+.1f}%).")
    elif exact_after:
        print(f"\nExact match found nothing before and finds {exact_after:,} reports now.")
    print("\nTop BEFORE entries:")
    table(["drug_norm", "reports"], [(a, f"{b:,}") for a, b in before_variants[:10]])
    print("\nTop AFTER entries (anything beyond the exact term is a different drug or a leftover):")
    table(["ingredient", "reports"], [(a, f"{b:,}") for a, b in after_variants[:10]])
    print("\nNote: LIKE can sweep in different drugs (for example LEVAMLODIPINE for AMLODIPINE), so exact"
          "\nmatching on a cleaned ingredient is both safer and, after cleanup, nearly as complete.")


def suspicious_report(conn, limit: int, loose: bool) -> None:
    rows = conn.execute(
        "SELECT ingredient, COUNT(DISTINCT primaryid) n FROM drug_ingredients "
        "GROUP BY ingredient ORDER BY n DESC LIMIT 20000"
    ).fetchall()
    # Strict (default): only values with characters that real names rarely contain, or very long values.
    # Digits, hyphens and long multi-word names are normal for substances (POLYETHYLENE GLYCOL 3350).
    odd = re.compile(r"[^A-Z0-9 \-,/]")
    if loose:
        picked = [(i, n) for i, n in rows
                  if odd.search(i) or re.search(r"\d", i) or len(i.split()) >= 4 or len(i) > 40]
    else:
        picked = [(i, n) for i, n in rows if odd.search(i) or len(i) > 60]
    print(f"\nMost common ingredient values that still look unresolved (showing {min(limit, len(picked))} of {len(picked)} found in the top 20,000):")
    table(["ingredient", "reports"], [(i, f"{n:,}") for i, n in picked[:limit]])
    print("\nSome of these are fine (VITAMIN B12, COVID-19 VACCINE). The rest are candidates for a rule in"
          "\nscripts/drug_names.py or a row in reference/drug_aliases.csv.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/faers.db")
    ap.add_argument("--term", help="Ingredient to compare before and after, e.g. AMLODIPINE")
    ap.add_argument("--ps-only", action="store_true", help="Count only reports where the drug is primary suspect")
    ap.add_argument("--suspicious", type=int, metavar="N", help="List N common ingredient values that look unresolved")
    ap.add_argument("--loose", action="store_true", help="With --suspicious: also flag digits and long multi-word names (noisy)")
    args = ap.parse_args()
    if not args.term and not args.suspicious:
        ap.error("give --term and/or --suspicious")

    path = Path(args.db)
    if not path.exists():
        print(f"error: {path} not found.", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE name='drug_ingredients'").fetchone()
    if not has:
        print("error: no drug_ingredients table. Run: python3 -I scripts/load_quarter.py --db "
              f"{args.db} --rebuild-only", file=sys.stderr)
        return 1
    if args.term:
        term_report(conn, args.term.upper().strip(), args.ps_only)
    if args.suspicious:
        suspicious_report(conn, args.suspicious, args.loose)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

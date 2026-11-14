#!/usr/bin/env python3
"""First-look report on a FAERS SQLite database built by load_quarter.py.

Usage:
    python3 -I scripts/explore.py --db data/faers.db

Read-only. Shows what was loaded, how much deduplication changed things, how much
data is missing, and the most common reactions and drugs.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def show(title, headers, rows) -> None:
    print(f"\n== {title} ==")
    if not rows:
        print("(no rows)")
        return
    data = [["" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v)) for v in r]
            for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in data)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in data:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def has_table(conn, name) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/faers.db")
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"error: {path} not found. Run scripts/load_quarter.py first.", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    if not has_table(conn, "reports"):
        print("error: no curated tables. Run scripts/load_quarter.py first.", file=sys.stderr)
        return 1
    q = conn.execute

    show("Quarters loaded (raw DEMO rows = report versions)", ["quarter", "report versions"],
         q("SELECT quarter, COUNT(*) FROM raw_demo GROUP BY quarter ORDER BY quarter").fetchall())

    raw_rows = q("SELECT COUNT(*) FROM raw_demo").fetchone()[0]
    cases = q("SELECT COUNT(DISTINCT caseid) FROM raw_demo").fetchone()[0]
    multi = q("SELECT COUNT(*) FROM (SELECT caseid FROM raw_demo GROUP BY caseid HAVING COUNT(*) > 1)").fetchone()[0]
    deleted = q("SELECT COUNT(DISTINCT caseid) FROM raw_deleted").fetchone()[0] if has_table(conn, "raw_deleted") else 0
    kept = q("SELECT COUNT(*) FROM reports").fetchone()[0]
    show("Deduplication", ["measure", "count"], [
        ("raw report versions", raw_rows),
        ("distinct cases", cases),
        ("cases with more than one version", multi),
        ("case ids listed as deleted", deleted),
        ("reports kept (latest version per case)", kept),
    ])

    show("Missing or unknown data in reports", ["field", "% missing"], q("""
        SELECT 'age_years', ROUND(100.0 * SUM(age_years IS NULL) / COUNT(*), 1) FROM reports
        UNION ALL SELECT 'sex (UNK)', ROUND(100.0 * SUM(sex = 'UNK') / COUNT(*), 1) FROM reports
        UNION ALL SELECT 'occr_country', ROUND(100.0 * SUM(occr_country IS NULL) / COUNT(*), 1) FROM reports
        UNION ALL SELECT 'report_type', ROUND(100.0 * SUM(report_type IS NULL) / COUNT(*), 1) FROM reports
        UNION ALL SELECT 'receive_year', ROUND(100.0 * SUM(receive_year IS NULL) / COUNT(*), 1) FROM reports
        UNION ALL SELECT 'event_year', ROUND(100.0 * SUM(event_year IS NULL) / COUNT(*), 1) FROM reports
    """).fetchall())

    show("Reports by sex", ["sex", "reports"],
         q("SELECT sex, COUNT(*) FROM reports GROUP BY sex ORDER BY 2 DESC").fetchall())
    show("Reports by age band", ["age_band", "reports"],
         q("SELECT age_band, COUNT(*) FROM reports GROUP BY age_band ORDER BY MIN(COALESCE(age_years, 999))").fetchall())
    show("Reports by year received", ["receive_year", "reports"],
         q("SELECT receive_year, COUNT(*) FROM reports GROUP BY 1 ORDER BY 1").fetchall())
    show("Reports by type", ["report_type", "reports"],
         q("SELECT report_type, COUNT(*) FROM reports GROUP BY 1 ORDER BY 2 DESC").fetchall())
    show("Outcome codes (reports can have several)", ["outc_cod", "reports"],
         q("SELECT outc_cod, COUNT(DISTINCT primaryid) FROM outcomes GROUP BY 1 ORDER BY 2 DESC").fetchall())
    show("Top 10 reactions (reports)", ["reaction (pt)", "reports"],
         q("SELECT pt, COUNT(DISTINCT primaryid) n FROM reactions GROUP BY pt ORDER BY n DESC LIMIT 10").fetchall())
    show("Top 10 primary suspect drugs (reports)", ["drug_norm", "reports"], q("""
        SELECT drug_norm, COUNT(DISTINCT primaryid) n FROM drugs
        WHERE role_cod = 'PS' GROUP BY drug_norm ORDER BY n DESC LIMIT 10""").fetchall())
    show("Normalization check: names that merge several raw spellings",
         ["drug_norm", "distinct raw spellings", "rows"], q("""
        SELECT drug_norm, COUNT(DISTINCT UPPER(COALESCE(prod_ai, drugname))) v, COUNT(*) n
        FROM drugs GROUP BY drug_norm HAVING v > 1 ORDER BY n DESC LIMIT 10""").fetchall())

    if has_table(conn, "drug_ingredients"):
        show("Top 10 primary suspect INGREDIENTS (Week 2 cleanup; combination products count toward each ingredient)",
             ["ingredient", "reports"], q("""
            SELECT ingredient, COUNT(DISTINCT primaryid) n FROM drug_ingredients
            WHERE role_cod = 'PS' GROUP BY ingredient ORDER BY n DESC LIMIT 10""").fetchall())
        show("Drug names vs clean ingredients", ["measure", "count"], [
            ("distinct drug_norm values", q("SELECT COUNT(DISTINCT drug_norm) FROM drugs").fetchone()[0]),
            ("distinct ingredient values", q("SELECT COUNT(DISTINCT ingredient) FROM drug_ingredients").fetchone()[0]),
            ("drug rows with more than one ingredient", q("""
                SELECT COUNT(*) FROM (SELECT primaryid, drug_seq FROM drug_ingredients
                                      GROUP BY primaryid, drug_seq HAVING COUNT(*) > 1)""").fetchone()[0]),
        ])

    print("\nReminder: these are report counts, not risk rates, and a report does not prove a drug caused an event.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

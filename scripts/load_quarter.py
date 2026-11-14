#!/usr/bin/env python3
"""Load FAERS quarterly ASCII extracts into SQLite, then build a deduplicated layer.

Usage:
    python3 -I scripts/load_quarter.py --input-dir data/raw/faers_2026q1 --db data/faers.db
    python3 -I scripts/load_quarter.py --input-dir data/sample --db data/sample.db

Notes:
* Standard library only.
* Raw files are loaded as-is into raw_* tables (all TEXT, plus a `quarter` column).
* build_curated() keeps only the latest version of each case, drops cases listed in
  DELETE files, and creates the clean tables: reports, drugs, reactions, outcomes.
* Re-running a quarter replaces that quarter's rows, so it is safe to repeat.
* Only tested on synthetic data so far. Real files may need small tweaks.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# `python3 -I` does not put this script's folder on the import path, so add it explicitly.
# This is our own trusted scripts folder, never the folder holding downloaded data.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import drug_names  # noqa: E402  (all drug-name rules live in scripts/drug_names.py)

FILE_RE = re.compile(r"^(DEMO|DRUG|REAC|OUTC|RPSR|THER|INDI)(\d{2}Q[1-4])\.txt$", re.IGNORECASE)
QUARTER_RE = re.compile(r"\d{2}Q[1-4]", re.IGNORECASE)
BATCH = 50_000

_STRENGTH = re.compile(r"\s+\d[\d.,]*\s*(MG|MCG|UG|G|ML|IU|UNITS?|%)\b.*$")
_FORM = re.compile(r"\s+(TABLETS?|CAPSULES?|INJECTION|SOLUTION|CREAM|ORAL|FILM[- ]COATED)\b.*$")


def normalize_drug_name(name):
    """First-pass drug name cleanup: uppercase, strip strengths and dosage forms.

    This is deliberately simple. Misspellings, brand names and combination products
    need a real dictionary (RxNorm or openFDA fields); that is Week 2 work.
    """
    if name is None:
        return None
    s = re.sub(r"\s+", " ", name.upper().strip())
    s = _STRENGTH.sub("", s)
    s = _FORM.sub("", s)
    s = s.strip(" .,;-")
    return s or None


def clean_col(name: str) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", name.strip().lstrip("﻿").lower())
    return name or "col"


def detect_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in ("$", "|", ",", "\t")}
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        raise ValueError(f"Could not detect a delimiter in header: {header_line[:80]!r}")
    return best


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _cols(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _c(cols: set, name: str, prefix: str) -> str:
    """Column reference if the column exists in this release, otherwise NULL."""
    return f"{prefix}.{name}" if name in cols else "NULL"


def ensure_raw_table(conn, table: str, cols: list) -> None:
    existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if not existing:
        defs = ", ".join(f"{c} TEXT" for c in cols)
        conn.execute(f"CREATE TABLE {table} (quarter TEXT, {defs})")
        return
    for c in cols:
        if c not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} TEXT")


def load_data_file(conn, path: Path, table: str, quarter: str):
    """Load one data file. Returns (rows_loaded, malformed_rows_skipped)."""
    table_name = f"raw_{table.lower()}"
    with open(path, "r", encoding="latin-1", errors="replace") as f:
        header = f.readline().lstrip("﻿").rstrip("\r\n")
        delim = detect_delimiter(header)
        cols = [clean_col(c) for c in header.split(delim)]
        if "primaryid" not in cols:
            raise ValueError(
                f"{path.name}: no primaryid column in header ({header[:80]!r}). "
                "Is this a legacy (pre-2012Q4) AERS file? Those are not supported."
            )
        ensure_raw_table(conn, table_name, cols)
        conn.execute(f"DELETE FROM {table_name} WHERE quarter = ?", (quarter,))
        n = len(cols)
        sql = (
            f"INSERT INTO {table_name} (quarter, {', '.join(cols)}) "
            f"VALUES (?, {', '.join('?' * n)})"
        )
        rows = malformed = 0
        batch = []
        for line in f:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            parts = line.split(delim)
            if len(parts) == n + 1 and parts[-1] == "":
                parts = parts[:-1]  # tolerate a trailing delimiter
            if len(parts) != n:
                malformed += 1  # skip rather than load shifted columns
                continue
            batch.append([quarter] + [p.strip() or None for p in parts])
            if len(batch) >= BATCH:
                conn.executemany(sql, batch)
                rows += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            rows += len(batch)
    return rows, malformed


def load_delete_file(conn, path: Path, quarter: str) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS raw_deleted (quarter TEXT, caseid TEXT)")
    conn.execute("DELETE FROM raw_deleted WHERE quarter = ?", (quarter,))
    ids = []
    with open(path, "r", encoding="latin-1", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            token = re.split(r"[\s$|,]+", line)[0]
            if token.isdigit():
                ids.append((quarter, token))
    conn.executemany("INSERT INTO raw_deleted VALUES (?, ?)", ids)
    return len(ids)


def discover(input_dir):
    """Find data files and DELETE files under input_dir (searches subfolders)."""
    data, deletes = {}, {}
    for p in sorted(Path(input_dir).rglob("*")):
        if not p.is_file():
            continue
        m = FILE_RE.match(p.name)
        if m:
            data.setdefault(m.group(2).upper(), {})[m.group(1).upper()] = p
        elif p.suffix.lower() == ".txt" and p.name.upper().startswith("DELETE"):
            q = QUARTER_RE.search(p.name)
            deletes[q.group(0).upper() if q else "UNKNOWN"] = p
    return data, deletes


def load_directory(conn, input_dir) -> dict:
    data, deletes = discover(input_dir)
    if not data:
        raise FileNotFoundError(
            f"No FAERS files like DEMO26Q1.txt found under {input_dir}. "
            "Unzip a quarterly ASCII extract there first."
        )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS loads (quarter TEXT, table_name TEXT, source_file TEXT, "
        "rows INTEGER, malformed_rows INTEGER, loaded_at TEXT)"
    )
    summary = {}
    for quarter in sorted(data):
        files = data[quarter]
        missing = [t for t in ("DEMO", "DRUG", "REAC") if t not in files]
        if missing:
            raise FileNotFoundError(f"Quarter {quarter}: missing required file(s): {', '.join(missing)}")
        for table in ("DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"):
            if table not in files:
                continue
            rows, bad = load_data_file(conn, files[table], table, quarter)
            conn.execute("DELETE FROM loads WHERE quarter=? AND table_name=?", (quarter, table))
            conn.execute(
                "INSERT INTO loads VALUES (?, ?, ?, ?, ?, ?)",
                (quarter, table, files[table].name, rows, bad,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            conn.commit()
            summary[(quarter, table)] = (rows, bad)
            note = f"  ({bad} malformed rows skipped)" if bad else ""
            print(f"  loaded {files[table].name}: {rows:,} rows{note}")
    for quarter, path in sorted(deletes.items()):
        n = load_delete_file(conn, path, quarter)
        conn.commit()
        print(f"  loaded {path.name}: {n:,} deleted case ids")
        summary[(quarter, "DELETE")] = (n, 0)
    return summary


def ensure_raw_indexes(conn) -> None:
    """Index the raw tables BEFORE building. Without these, the joins and the per-report
    outcome lookup rescan whole tables and the build takes hours on a real quarter."""
    for t in ("raw_demo", "raw_drug", "raw_reac", "raw_outc", "raw_rpsr", "raw_ther", "raw_indi"):
        if _table_exists(conn, t) and "primaryid" in _cols(conn, t):
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_primaryid ON {t}(primaryid)")
    if _table_exists(conn, "raw_demo") and "caseid" in _cols(conn, "raw_demo"):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_demo_caseid ON raw_demo(caseid)")
    conn.commit()


def build_ingredients(conn) -> int:
    """Split each drug row into clean ingredients: one row per (drug row, ingredient).

    `drugs.drug_norm` is left untouched, so queries written against it keep their answers.
    Returns the number of drug rows that yielded no ingredient at all.
    """
    aliases = drug_names.load_aliases()
    conn.execute("DROP TABLE IF EXISTS drug_ingredients")
    conn.execute(
        "CREATE TABLE drug_ingredients (primaryid TEXT, drug_seq INTEGER, role_cod TEXT, ingredient TEXT)"
    )
    cache = {}
    batch, empty = [], 0
    for primaryid, drug_seq, role_cod, prod_ai, drugname in conn.execute(
        "SELECT primaryid, drug_seq, role_cod, prod_ai, drugname FROM drugs"
    ):
        key = (prod_ai, drugname)
        found = cache.get(key)
        if found is None:
            # Same source choice as drug_norm: the active-ingredient field first, then the reported name.
            found = drug_names.split_ingredients(prod_ai, aliases) if prod_ai else []
            if not found:
                found = drug_names.split_ingredients(drugname, aliases)
            cache[key] = found
        if not found:
            empty += 1
        for ingredient in found:
            batch.append((primaryid, drug_seq, role_cod, ingredient))
        if len(batch) >= BATCH:
            conn.executemany("INSERT INTO drug_ingredients VALUES (?, ?, ?, ?)", batch)
            batch = []
    if batch:
        conn.executemany("INSERT INTO drug_ingredients VALUES (?, ?, ?, ?)", batch)
    conn.execute("CREATE INDEX idx_di_primaryid ON drug_ingredients(primaryid)")
    conn.execute("CREATE INDEX idx_di_ingredient ON drug_ingredients(ingredient)")
    return empty


def build_curated(conn) -> dict:
    """Build reports/drugs/reactions/outcomes from the raw tables."""
    conn.create_function("norm_drug", 1, normalize_drug_name, deterministic=True)
    if not _table_exists(conn, "raw_outc"):
        conn.execute("CREATE TABLE raw_outc (quarter TEXT, primaryid TEXT, caseid TEXT, outc_cod TEXT)")
    print("  indexing raw tables ...")
    ensure_raw_indexes(conn)
    for t in ("raw_demo", "raw_drug", "raw_reac"):
        if not _table_exists(conn, t):
            raise RuntimeError(f"{t} is missing. Load quarterly files first.")
    if not _table_exists(conn, "raw_outc"):
        conn.execute("CREATE TABLE raw_outc (quarter TEXT, primaryid TEXT, caseid TEXT, outc_cod TEXT)")
    if not _table_exists(conn, "raw_deleted"):
        conn.execute("CREATE TABLE raw_deleted (quarter TEXT, caseid TEXT)")
    for t, cols in (("raw_demo", ("primaryid", "caseid")),
                    ("raw_drug", ("primaryid", "drugname")),
                    ("raw_reac", ("primaryid", "pt"))):
        have = _cols(conn, t)
        for c in cols:
            if c not in have:
                raise RuntimeError(f"{t} has no {c} column; cannot build curated tables.")

    conn.execute("DROP VIEW IF EXISTS v_ps_reactions")
    conn.execute("DROP VIEW IF EXISTS v_ps_ingredient_reactions")
    for t in ("reports", "drugs", "reactions", "outcomes", "drug_ingredients"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("DROP TABLE IF EXISTS temp.ranked")

    demo = _cols(conn, "raw_demo")
    version = "CAST(COALESCE(d.caseversion, '0') AS INTEGER)" if "caseversion" in demo else "0"

    # Latest version per case, excluding deleted cases.
    conn.execute(f"""
        CREATE TEMP TABLE ranked AS
        SELECT d.primaryid,
               ROW_NUMBER() OVER (
                   PARTITION BY d.caseid
                   ORDER BY {version} DESC, d.quarter DESC, CAST(d.primaryid AS INTEGER) DESC
               ) AS rn
        FROM raw_demo d
        WHERE d.caseid IS NOT NULL
          AND d.caseid NOT IN (SELECT caseid FROM raw_deleted WHERE caseid IS NOT NULL)
    """)

    age, age_cod = _c(demo, "age", "d"), _c(demo, "age_cod", "d")
    age_expr = f"""
        CASE WHEN {age} IS NULL OR {age} GLOB '*[^0-9.]*' THEN NULL
             ELSE CASE UPPER({age_cod})
                  WHEN 'YR'  THEN CAST({age} AS REAL)
                  WHEN 'DEC' THEN CAST({age} AS REAL) * 10
                  WHEN 'MON' THEN CAST({age} AS REAL) / 12.0
                  WHEN 'WK'  THEN CAST({age} AS REAL) / 52.1775
                  WHEN 'DY'  THEN CAST({age} AS REAL) / 365.25
                  WHEN 'HR'  THEN CAST({age} AS REAL) / 8766.0
             END
        END"""
    sex = _c(demo, "sex", "d")
    init = _c(demo, "init_fda_dt", "d")
    event = _c(demo, "event_dt", "d")

    conn.execute(f"""
        CREATE TABLE reports AS
        SELECT d.primaryid,
               d.caseid,
               {version} AS caseversion,
               d.quarter,
               {age_expr} AS age_years,
               NULL AS age_band,
               CASE UPPER({sex}) WHEN 'M' THEN 'M' WHEN 'F' THEN 'F' ELSE 'UNK' END AS sex,
               {_c(demo, "occr_country", "d")} AS occr_country,
               {_c(demo, "reporter_country", "d")} AS reporter_country,
               {_c(demo, "rept_cod", "d")} AS report_type,
               CAST(substr({init}, 1, 4) AS INTEGER) AS receive_year,
               CAST(substr({event}, 1, 4) AS INTEGER) AS event_year,
               EXISTS (SELECT 1 FROM raw_outc o WHERE o.primaryid = d.primaryid) AS has_outcome_code
        FROM raw_demo d
        JOIN ranked r ON r.primaryid = d.primaryid AND r.rn = 1
    """)
    conn.execute("UPDATE reports SET age_years = NULL WHERE age_years < 0 OR age_years > 120")
    conn.execute("""
        UPDATE reports SET age_band = CASE
            WHEN age_years IS NULL THEN 'unknown'
            WHEN age_years < 18 THEN '0-17'
            WHEN age_years < 45 THEN '18-44'
            WHEN age_years < 65 THEN '45-64'
            WHEN age_years < 80 THEN '65-79'
            ELSE '80+' END
    """)
    conn.execute("CREATE UNIQUE INDEX idx_reports_primaryid ON reports(primaryid)")
    conn.execute("CREATE UNIQUE INDEX idx_reports_caseid ON reports(caseid)")  # proves dedupe worked

    drug = _cols(conn, "raw_drug")
    conn.execute(f"""
        CREATE TABLE drugs AS
        SELECT g.primaryid,
               CAST({_c(drug, "drug_seq", "g")} AS INTEGER) AS drug_seq,
               UPPER({_c(drug, "role_cod", "g")}) AS role_cod,
               g.drugname,
               {_c(drug, "prod_ai", "g")} AS prod_ai,
               norm_drug(COALESCE({_c(drug, "prod_ai", "g")}, g.drugname)) AS drug_norm,
               {_c(drug, "route", "g")} AS route
        FROM raw_drug g
        JOIN reports r ON r.primaryid = g.primaryid
    """)
    conn.execute("""
        CREATE TABLE reactions AS
        SELECT e.primaryid, e.pt
        FROM raw_reac e
        JOIN reports r ON r.primaryid = e.primaryid
        WHERE e.pt IS NOT NULL
    """)
    conn.execute("""
        CREATE TABLE outcomes AS
        SELECT o.primaryid, UPPER(o.outc_cod) AS outc_cod
        FROM raw_outc o
        JOIN reports r ON r.primaryid = o.primaryid
        WHERE o.outc_cod IS NOT NULL
    """)
    conn.execute("CREATE INDEX idx_drugs_primaryid ON drugs(primaryid)")
    conn.execute("CREATE INDEX idx_drugs_norm ON drugs(drug_norm)")
    conn.execute("CREATE INDEX idx_reactions_primaryid ON reactions(primaryid)")
    conn.execute("CREATE INDEX idx_reactions_pt ON reactions(pt)")
    conn.execute("CREATE INDEX idx_outcomes_primaryid ON outcomes(primaryid)")

    # Primary suspect drug paired with each reaction. Count DISTINCT primaryid in queries:
    # a report with several PS drugs or reactions appears in several rows.
    conn.execute("""
        CREATE VIEW v_ps_reactions AS
        SELECT r.primaryid, r.caseid, g.drug_norm, e.pt,
               r.sex, r.age_years, r.age_band, r.receive_year, r.has_outcome_code
        FROM reports r
        JOIN drugs g ON g.primaryid = r.primaryid AND g.role_cod = 'PS'
        JOIN reactions e ON e.primaryid = r.primaryid
    """)

    print("  splitting drug names into ingredients ...")
    unresolved = build_ingredients(conn)
    # Same idea as v_ps_reactions, but by clean ingredient: a combination product
    # (AMLODIPINE\VALSARTAN) counts toward each of its ingredients.
    conn.execute("""
        CREATE VIEW v_ps_ingredient_reactions AS
        SELECT r.primaryid, r.caseid, i.ingredient, e.pt,
               r.sex, r.age_years, r.age_band, r.receive_year, r.has_outcome_code
        FROM reports r
        JOIN drug_ingredients i ON i.primaryid = r.primaryid AND i.role_cod = 'PS'
        JOIN reactions e ON e.primaryid = r.primaryid
    """)
    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("reports", "drugs", "reactions", "outcomes", "drug_ingredients")}
    print("  built curated tables: " + ", ".join(f"{k}={v:,}" for k, v in counts.items()))
    if unresolved:
        print(f"  note: {unresolved:,} drug rows produced no ingredient (blank or unusable names)")
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", help="Folder with unzipped quarterly files (subfolders are searched); not needed with --rebuild-only")
    ap.add_argument("--db", default="data/faers.db", help="SQLite database to create or update")
    ap.add_argument("--rebuild-only", action="store_true", help="Skip loading; rebuild curated tables from existing raw tables")
    args = ap.parse_args(argv)
    if not args.rebuild_only and not args.input_dir:
        ap.error("--input-dir is required unless you pass --rebuild-only")

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    try:
        if not args.rebuild_only:
            print(f"Loading from {args.input_dir} ...")
            load_directory(conn, args.input_dir)
        print("Building curated tables ...")
        build_curated(conn)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"Done. Next: python3 -I scripts/explore.py --db {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

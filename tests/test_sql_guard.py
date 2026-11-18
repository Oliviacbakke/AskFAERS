"""Tests for the SQL guardrails: what must run, what must be refused, and that nothing gets changed.

Run:  python3 -I -m unittest discover -s tests -v
"""
import importlib.util
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses needs the module registered while it is being loaded
    spec.loader.exec_module(mod)
    return mod


sample = _load_module("make_sample_data", "scripts/make_sample_data.py")
loader = _load_module("load_quarter", "scripts/load_quarter.py")
guard = _load_module("sql_guard", "scripts/sql_guard.py")


class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        sample.generate(tmp / "files", n_cases=600, seed=21)
        cls.db = tmp / "g.db"
        conn = sqlite3.connect(cls.db)
        loader.load_directory(conn, tmp / "files")
        loader.build_curated(conn)
        conn.close()
        cls.snapshot = cls.counts()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def counts(cls):
        conn = sqlite3.connect(cls.db)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        finally:
            conn.close()

    def run_ok(self, sql, **kw):
        return guard.run_guarded(self.db, sql, **kw)

    def assertRefused(self, sql, kind):
        with self.assertRaises(guard.GuardError, msg=sql) as ctx:
            guard.run_guarded(self.db, sql, timeout_s=5)
        self.assertEqual(ctx.exception.kind, kind, f"{sql!r}: {ctx.exception.message}")

    # ---- things that must work -------------------------------------------------------------
    def test_ordinary_queries_run(self):
        queries = [
            "SELECT COUNT(*) FROM reports",
            "SELECT sex, COUNT(*) AS n FROM reports GROUP BY sex ORDER BY n DESC",
            "SELECT ingredient, COUNT(DISTINCT primaryid) FROM drug_ingredients WHERE role_cod = 'PS' GROUP BY ingredient",
            "SELECT pt, COUNT(DISTINCT primaryid) FROM v_ps_reactions WHERE drug_norm = 'ZELNOPRIL' GROUP BY pt",
            "SELECT pt FROM v_ps_ingredient_reactions LIMIT 3",
            "WITH ho AS (SELECT primaryid FROM outcomes WHERE outc_cod = 'HO') SELECT COUNT(*) FROM ho",
            "SELECT age_band, ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM reports), 1) FROM reports GROUP BY age_band",
            "SELECT primaryid, ROW_NUMBER() OVER (PARTITION BY sex ORDER BY age_years) FROM reports LIMIT 5",
            "SELECT * FROM reactions WHERE pt LIKE '%ause%' LIMIT 5",
            "SELECT 1 /* a comment ; DROP TABLE reports */",
            "-- leading comment\nSELECT COUNT(*) FROM drugs;",
        ]
        for q in queries:
            self.assertGreaterEqual(len(self.run_ok(q).rows), 1, q)

    def test_result_shape_and_row_cap(self):
        r = self.run_ok("SELECT primaryid, pt FROM reactions", max_rows=5)
        self.assertEqual(r.columns, ["primaryid", "pt"])
        self.assertEqual(len(r.rows), 5)
        self.assertTrue(r.truncated)
        r = self.run_ok("SELECT COUNT(*) FROM reports", max_rows=5)
        self.assertFalse(r.truncated)

    # ---- things that must be refused ---------------------------------------------------------
    def test_writes_and_schema_changes_are_refused(self):
        for q in ("DROP TABLE reports", "DELETE FROM reports", "UPDATE reports SET sex = 'M'",
                  "INSERT INTO reports (primaryid) VALUES ('x')", "CREATE TABLE t (a)",
                  "ALTER TABLE reports ADD COLUMN x TEXT", "VACUUM", "REPLACE INTO reports (primaryid) VALUES ('x')",
                  "CREATE INDEX i ON reports(sex)", "ATTACH DATABASE ':memory:' AS m", "PRAGMA writable_schema = ON",
                  "PRAGMA table_info(reports)", "BEGIN", "EXPLAIN SELECT 1"):
            self.assertRefused(q, "shape")

    def test_writes_hidden_inside_a_select_shaped_query_are_refused_by_the_database(self):
        for q in ("WITH x AS (SELECT 1) DELETE FROM reports",
                  "WITH x AS (SELECT 1) UPDATE reports SET sex = 'M'",
                  "WITH x AS (SELECT 1) INSERT INTO reports (primaryid) VALUES ('x')"):
            with self.assertRaises(guard.GuardError, msg=q):
                guard.run_guarded(self.db, q)

    def test_multiple_statements_are_refused(self):
        self.assertRefused("SELECT 1; DROP TABLE reports", "shape")
        self.assertRefused("SELECT 1; SELECT 2", "shape")

    def test_raw_and_internal_tables_are_refused(self):
        for q in ("SELECT * FROM raw_demo", "SELECT * FROM raw_drug LIMIT 1", "SELECT * FROM loads",
                  "SELECT * FROM raw_deleted", "SELECT name FROM sqlite_master",
                  "SELECT * FROM pragma_table_info('reports')",
                  "SELECT COUNT(*) FROM reports WHERE primaryid IN (SELECT primaryid FROM raw_demo)",
                  "WITH x AS (SELECT * FROM raw_demo) SELECT COUNT(*) FROM x"):
            self.assertRefused(q, "not_allowed")

    def test_refusal_message_names_the_problem(self):
        with self.assertRaises(guard.GuardError) as ctx:
            guard.run_guarded(self.db, "SELECT * FROM raw_demo")
        self.assertIn("raw_demo", ctx.exception.message)

    def test_dangerous_or_unknown_functions_are_refused(self):
        for q in ("SELECT load_extension('x')", "SELECT random()", "SELECT readfile('/etc/passwd')",
                  "SELECT zeroblob(1000000000)", "SELECT sqlite_version()"):
            with self.assertRaises(guard.GuardError, msg=q):
                guard.run_guarded(self.db, q)

    def test_recursive_queries_are_refused(self):
        self.assertRefused("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
                           "not_allowed")

    def test_empty_and_non_select_input(self):
        for q in ("", "   ", None, "hello", "-- just a comment", "/* x */ DROP TABLE reports"):
            self.assertRefused(q, "shape")

    def test_oversized_query_is_refused(self):
        self.assertRefused("SELECT " + "1," * 5000 + "1", "shape")

    def test_slow_query_times_out(self):
        with self.assertRaises(guard.GuardError) as ctx:
            guard.run_guarded(self.db, "SELECT COUNT(*) FROM reports a, reports b, reports c", timeout_s=0.3)
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_sql_errors_are_reported_not_raised_raw(self):
        self.assertRefused("SELECT nonexistent_column FROM reports", "sql_error")
        self.assertRefused("SELECT FROM", "sql_error")

    def test_missing_database(self):
        with self.assertRaises(guard.GuardError) as ctx:
            guard.run_guarded(Path(self.tmp.name) / "nope.db", "SELECT 1")
        self.assertEqual(ctx.exception.kind, "setup")

    def test_nothing_was_changed_by_any_attack(self):
        # runs last alphabetically among attack tests is not guaranteed, so re-run a few attacks first
        for q in ("DELETE FROM reports", "DROP TABLE drugs", "WITH x AS (SELECT 1) DELETE FROM reactions"):
            try:
                guard.run_guarded(self.db, q)
            except guard.GuardError:
                pass
        self.assertEqual(self.counts(), self.snapshot)

    # ---- her own queries -----------------------------------------------------------------------
    def test_week1_gold_queries_run_except_the_raw_table_one(self):
        path = ROOT / "sql" / "my_answers.sql"
        if not path.exists():
            self.skipTest("sql/my_answers.sql not present")
        text = "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("--"))
        statements = [s.strip() for s in text.split(";") if s.strip()]
        self.assertGreaterEqual(len(statements), 10)
        ran = refused = 0
        for s in statements:
            if re.search(r"\braw_", s):
                with self.assertRaises(guard.GuardError, msg=s):
                    guard.run_guarded(self.db, s)
                refused += 1
            else:
                guard.run_guarded(self.db, s)  # must not raise
                ran += 1
        self.assertEqual(refused, 1)  # Q6 reads raw_demo on purpose; the app is not allowed to
        self.assertGreaterEqual(ran, 9)


if __name__ == "__main__":
    unittest.main()

"""Checks the loader against synthetic data with known ground truth.

Run:  python3 -I -m unittest discover -s tests -v
"""
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sample = _load_module("make_sample_data", "scripts/make_sample_data.py")
loader = _load_module("load_quarter", "scripts/load_quarter.py")


class NormalizeDrugName(unittest.TestCase):
    def test_strips_strengths_and_forms(self):
        cases = {
            "zelnopril 10mg": "ZELNOPRIL",
            "Zelnopril 20 mg": "ZELNOPRIL",
            "ZELNOPRIL TABLETS": "ZELNOPRIL",
            "  vantirax   ": "VANTIRAX",
            "VITAMIN B12 1000 MCG": "VITAMIN B12",
            "5-FLUOROURACIL": "5-FLUOROURACIL",
        }
        for raw, expected in cases.items():
            self.assertEqual(loader.normalize_drug_name(raw), expected, raw)

    def test_empty_values(self):
        self.assertIsNone(loader.normalize_drug_name(None))
        self.assertIsNone(loader.normalize_drug_name("   "))


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.meta = sample.generate(tmp / "files", n_cases=600, seed=11)
        cls.db_path = tmp / "test.db"
        cls.conn = sqlite3.connect(cls.db_path)
        loader.load_directory(cls.conn, tmp / "files")
        loader.build_curated(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def scalar(self, sql, *params):
        return self.conn.execute(sql, params).fetchone()[0]

    def test_one_report_per_case_and_deleted_excluded(self):
        expected = self.meta["n_cases"] - len(self.meta["deleted"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM reports"), expected)
        self.assertEqual(self.scalar("SELECT COUNT(DISTINCT caseid) FROM reports"), expected)
        for caseid in self.meta["deleted"]:
            self.assertEqual(self.scalar("SELECT COUNT(*) FROM reports WHERE caseid = ?", str(caseid)), 0)

    def test_latest_version_wins(self):
        checked = 0
        for caseid, version in self.meta["latest_version"].items():
            if caseid in self.meta["deleted"]:
                continue
            got = self.scalar("SELECT caseversion FROM reports WHERE caseid = ?", str(caseid))
            self.assertEqual(got, version, caseid)
            checked += 1
        self.assertGreater(sum(1 for v in self.meta["latest_version"].values() if v > 1), 0)
        self.assertGreater(checked, 0)

    def test_child_tables_only_reference_kept_reports(self):
        for table in ("drugs", "reactions", "outcomes"):
            orphans = self.scalar(
                f"SELECT COUNT(*) FROM {table} WHERE primaryid NOT IN (SELECT primaryid FROM reports)")
            self.assertEqual(orphans, 0, table)
        raw = self.scalar(
            "SELECT COUNT(*) FROM raw_reac WHERE primaryid IN (SELECT primaryid FROM reports)")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM reactions"), raw)

    def test_ages_are_sane(self):
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM reports WHERE age_years < 0 OR age_years > 120"), 0)
        self.assertGreater(self.scalar("SELECT COUNT(*) FROM reports WHERE age_years IS NULL"), 0)
        self.assertGreater(self.scalar("SELECT COUNT(*) FROM reports WHERE age_years < 2"), 0)  # months/days converted
        bands = {r[0] for r in self.conn.execute("SELECT DISTINCT age_band FROM reports")}
        self.assertTrue(bands <= {"0-17", "18-44", "45-64", "65-79", "80+", "unknown"})

    def test_drug_variants_collapse_to_six_fictional_drugs(self):
        names = {r[0] for r in self.conn.execute("SELECT DISTINCT drug_norm FROM drugs")}
        self.assertEqual(names, set(sample.FICTIONAL_DRUGS))

    def test_ingredients_cover_every_drug_and_keep_drug_norm_unchanged(self):
        names = {r[0] for r in self.conn.execute("SELECT DISTINCT ingredient FROM drug_ingredients")}
        self.assertEqual(names, set(sample.FICTIONAL_DRUGS))
        no_ingredient = self.scalar("""
            SELECT COUNT(*) FROM drugs d
            WHERE NOT EXISTS (SELECT 1 FROM drug_ingredients i
                              WHERE i.primaryid = d.primaryid AND i.drug_seq = d.drug_seq)""")
        self.assertEqual(no_ingredient, 0)
        # drug_norm must behave exactly as in Week 1, so earlier gold answers stay valid
        norm = {r[0] for r in self.conn.execute("SELECT DISTINCT drug_norm FROM drugs")}
        self.assertEqual(norm, set(sample.FICTIONAL_DRUGS))
        self.assertGreater(self.scalar("SELECT COUNT(*) FROM v_ps_ingredient_reactions"), 0)

    def test_sex_values(self):
        values = {r[0] for r in self.conn.execute("SELECT DISTINCT sex FROM reports")}
        self.assertTrue(values <= {"M", "F", "UNK"})

    def test_reload_is_idempotent(self):
        before = {t: self.scalar(f"SELECT COUNT(*) FROM {t}")
                  for t in ("raw_demo", "raw_drug", "raw_reac", "reports", "drugs", "reactions", "outcomes")}
        loader.load_directory(self.conn, Path(self.tmp.name) / "files")
        loader.build_curated(self.conn)
        after = {t: self.scalar(f"SELECT COUNT(*) FROM {t}") for t in before}
        self.assertEqual(before, after)

    def test_ps_reaction_view_counts_reports_once_with_distinct(self):
        total = self.scalar("SELECT COUNT(DISTINCT primaryid) FROM v_ps_reactions")
        self.assertLessEqual(total, self.scalar("SELECT COUNT(*) FROM reports"))
        self.assertGreater(total, 0)


if __name__ == "__main__":
    unittest.main()

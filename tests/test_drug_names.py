"""Tests for the Week 2 ingredient rules, using real messy names seen in a FAERS quarter.

Run:  python3 -I -m unittest discover -s tests -v
"""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dn = _load_module("drug_names", "scripts/drug_names.py")
loader = _load_module("load_quarter", "scripts/load_quarter.py")
ALIASES = dn.load_aliases()


def split(raw):
    return dn.split_ingredients(raw, ALIASES)


class RealAmlodipineVariants(unittest.TestCase):
    def test_salts_collapse_to_one_ingredient(self):
        for raw in ("AMLODIPINE BESYLATE", "AMLODIPINE MALEATE", "AMLODIPINE MESYLATE",
                    "AMLODIPINE BENZOATE", "AMLODIPINE OROTATE", "AMLODIPINE BESYLATE (BULK)",
                    "AMLODIPINE (NORVASC)", "LAN DI [AMLODIPINE BESILATE]", "AMLODIPINE B",
                    "AMLODIPINE B TAB", "AMLODIPINE OD"):
            self.assertEqual(split(raw), ["AMLODIPINE"], raw)

    def test_combinations_split_on_every_separator(self):
        self.assertEqual(split("AMLODIPINE\\PERINDOPRIL"), ["AMLODIPINE", "PERINDOPRIL"])
        self.assertEqual(split("AMLODIPINE BESYLATE;BENAZEPRIL"), ["AMLODIPINE", "BENAZEPRIL"])
        self.assertEqual(split("AMLODIPINE + ATORVASTATIN"), ["AMLODIPINE", "ATORVASTATIN"])
        self.assertEqual(split("AMLODIPINE?BENAZEPRIL"), ["AMLODIPINE", "BENAZEPRIL"])
        self.assertEqual(split("AMLODIPINE/VALSARTAN"), ["AMLODIPINE", "VALSARTAN"])

    def test_salts_inside_combinations(self):
        self.assertEqual(split("AMLODIPINE BESYLATE\\OLMESARTAN MEDOXOMIL"), ["AMLODIPINE", "OLMESARTAN"])
        self.assertEqual(split("AMLODIPINE BESYLATE\\BENAZEPRIL HYDROCHLORIDE"), ["AMLODIPINE", "BENAZEPRIL"])
        self.assertEqual(split("AMLODIPINE BESYLATE\\INDAPAMIDE\\PERINDOPRIL ARGININE"),
                         ["AMLODIPINE", "INDAPAMIDE", "PERINDOPRIL"])
        self.assertEqual(split("AMLODIPINE BESYLATE\\ATORVASTATIN CALCIUM"), ["AMLODIPINE", "ATORVASTATIN"])

    def test_hydrochlorothiazide_is_not_mistaken_for_hydrochloride(self):
        self.assertEqual(split("AMLODIPINE BESYLATE\\HYDROCHLOROTHIAZIDE\\VALSARTAN"),
                         ["AMLODIPINE", "HYDROCHLOROTHIAZIDE", "VALSARTAN"])

    def test_levamlodipine_is_a_different_drug(self):
        self.assertEqual(split("LEVAMLODIPINE BESYLATE"), ["LEVAMLODIPINE"])

    def test_stray_markup_and_duplicates(self):
        raw = "AMLODIPINE? {DELIMITER QUALIFIER=^INV^}AMLODIPINE (SPECIFIC SUBSTANCE SUB706){/DELIMITER}"
        self.assertEqual(split(raw), ["AMLODIPINE"])

    def test_hand_patched_hyphen_combinations(self):
        for raw in ("AMLODIPINE BESYLATE-BENAZEPRIL", "AMLODIPINE BESY-BENAZEPRIL", "AMLODIPINE-BENAZ"):
            self.assertEqual(split(raw), ["AMLODIPINE", "BENAZEPRIL"], raw)

    def test_manufacturer_suffix_alias(self):
        self.assertEqual(split("AMLODIPINE + VALSARTAN CANON"), ["AMLODIPINE", "VALSARTAN"])


class OtherRules(unittest.TestCase):
    def test_biosimilar_suffixes(self):
        self.assertEqual(split("BIMEKIZUMAB-BKZX"), ["BIMEKIZUMAB"])
        self.assertEqual(split("RISANKIZUMAB-RZAA"), ["RISANKIZUMAB"])
        self.assertEqual(split("INSULIN GLARGINE-YFGN"), ["INSULIN GLARGINE"])
        self.assertEqual(split("INSULIN GLARGINE"), ["INSULIN GLARGINE"])

    def test_hyphens_in_real_names_survive(self):
        self.assertEqual(split("5-FLUOROURACIL"), ["5-FLUOROURACIL"])
        self.assertEqual(split("VALSARTAN-HCTZ"), ["VALSARTAN-HCTZ"])  # known limit, documented

    def test_greek_letter_and_caret_artifacts_from_fda_substance_names(self):
        self.assertEqual(split(".ALPHA.-TOCOPHEROL"), ["ALPHA-TOCOPHEROL"])
        self.assertEqual(split(".ALPHA.-LIPOIC ACID"), ["ALPHA-LIPOIC ACID"])
        self.assertEqual(split(".ALPHA.1-PROTEINASE INHIBITOR HUMAN"), ["ALPHA1-PROTEINASE INHIBITOR HUMAN"])
        self.assertEqual(split("DL-.BETA.-CAROTENE"), ["DL-BETA-CAROTENE"])
        self.assertEqual(split("RIBOFLAVIN 5^-PHOSPHATE"), ["RIBOFLAVIN 5-PHOSPHATE"])
        self.assertEqual(split("MEASLES VIRUS STRAIN ENDERS^ ATTENUATED"), ["MEASLES VIRUS STRAIN ENDERS ATTENUATED"])

    def test_names_with_greek_words_but_no_dots_are_untouched(self):
        self.assertEqual(split("INTERFERON BETA-1A"), ["INTERFERON BETA-1A"])
        self.assertEqual(split("PEGINTERFERON BETA-1A"), ["PEGINTERFERON BETA-1A"])
        self.assertEqual(split("OMEGA-3 FATTY ACIDS"), ["OMEGA-3 FATTY ACIDS"])

    def test_names_that_are_themselves_salts_are_kept(self):
        for raw in ("SODIUM CHLORIDE", "POTASSIUM CHLORIDE", "CALCIUM", "CALCIUM CARBONATE"):
            self.assertEqual(split(raw), [raw], raw)

    def test_strengths_and_forms(self):
        self.assertEqual(split("ZELNOPRIL 10MG"), ["ZELNOPRIL"])
        self.assertEqual(split("ZELNOPRIL TABLETS"), ["ZELNOPRIL"])
        self.assertEqual(split("VITAMIN B12 1000 MCG"), ["VITAMIN B12"])
        self.assertEqual(split("AMLODIPINE 10/320 MG"), ["AMLODIPINE"])
        self.assertEqual(split("VITAMIN C 1,000 MG"), ["VITAMIN C"])
        self.assertEqual(split("ALBUTEROL AER HFA"), ["ALBUTEROL"])

    def test_commas_between_names_but_not_inside_numbers(self):
        self.assertEqual(split("ACETAMINOPHEN, IPRATROPIUM"), ["ACETAMINOPHEN", "IPRATROPIUM"])

    def test_aliases(self):
        self.assertEqual(split("TAZOCIN"), ["PIPERACILLIN", "TAZOBACTAM"])
        self.assertEqual(split("TRIAMCINOLON"), ["TRIAMCINOLONE"])
        self.assertEqual(split("VITAMIN D3 + K2"), ["CHOLECALCIFEROL", "MENAQUINONE"])

    def test_empty_inputs(self):
        for raw in (None, "", "   ", "?"):
            self.assertEqual(split(raw), [], repr(raw))

    def test_name_that_is_only_a_parenthetical(self):
        self.assertEqual(split("(ACETAMINOPHEN)"), ["ACETAMINOPHEN"])

    def test_alias_file_loads_and_is_clean(self):
        self.assertGreater(len(ALIASES), 10)
        for key, vals in ALIASES.items():
            self.assertEqual(key, key.upper())
            self.assertTrue(all(v == v.upper() for v in vals), key)


class IngredientTable(unittest.TestCase):
    def test_build_ingredients_splits_combinations_into_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE drugs (primaryid TEXT, drug_seq INTEGER, role_cod TEXT, "
                     "drugname TEXT, prod_ai TEXT, drug_norm TEXT)")
        conn.executemany("INSERT INTO drugs VALUES (?,?,?,?,?,?)", [
            ("1", 1, "PS", "EXFORGE", "AMLODIPINE BESYLATE\\VALSARTAN", "x"),   # combo via prod_ai
            ("2", 1, "PS", "AMLODIPINE B TAB", None, "x"),                      # alias via drugname
            ("3", 1, "C", "TAZOCIN", None, "x"),                                # brand via alias
            ("4", 1, "PS", "!!!", None, "x"),                                   # unusable
            ("5", 1, "PS", "SOME BRAND", "???", "x"),                           # bad prod_ai, falls back to name
        ])
        empty = loader.build_ingredients(conn)
        got = sorted(conn.execute("SELECT primaryid, ingredient FROM drug_ingredients").fetchall())
        self.assertEqual(got, [("1", "AMLODIPINE"), ("1", "VALSARTAN"), ("2", "AMLODIPINE"),
                               ("3", "PIPERACILLIN"), ("3", "TAZOBACTAM"), ("5", "SOME BRAND")])
        self.assertEqual(empty, 1)

    def test_rebuild_is_repeatable(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE drugs (primaryid TEXT, drug_seq INTEGER, role_cod TEXT, "
                     "drugname TEXT, prod_ai TEXT, drug_norm TEXT)")
        conn.execute("INSERT INTO drugs VALUES ('1', 1, 'PS', 'X', 'AMLODIPINE\\VALSARTAN', 'x')")
        loader.build_ingredients(conn)
        loader.build_ingredients(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM drug_ingredients").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()

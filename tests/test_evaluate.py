"""Tests for the evaluation runner (no API calls: the system under test is replaced by a stub).

Run:  python3 -I -m unittest discover -s tests -v
"""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sample = _load_module("make_sample_data", "scripts/make_sample_data.py")
loader = _load_module("load_quarter", "scripts/load_quarter.py")
nl = _load_module("nl2sql", "scripts/nl2sql.py")
ev = _load_module("evaluate", "scripts/evaluate.py")

GOOD = """# comment
id: a1
category: answerable
question: How many reports are there?
sql:
SELECT COUNT(*) FROM reports;
---
id: m1
category: medical_advice
question: Should I stop it?
"""


def stub(status="answered", category="answerable", rows=None, sql="SELECT 1"):
    def fn(question, db, client, model=None):
        return nl.Answer(question=question, status=status, category=category, sql=sql,
                         rows=rows if rows is not None else [], input_tokens=10, output_tokens=5)
    return fn


class ParseTests(unittest.TestCase):
    def test_parses_blocks_comments_and_sql(self):
        qs = ev.parse_questions(GOOD)
        self.assertEqual([q.id for q in qs], ["a1", "m1"])
        self.assertEqual(qs[0].sql, "SELECT COUNT(*) FROM reports;")
        self.assertEqual(qs[1].sql, "")

    def test_bad_files_are_rejected_with_clear_errors(self):
        bad = {
            "needs sql": "id: x\ncategory: answerable\nquestion: q",
            "bad category": "id: x\ncategory: nope\nquestion: q",
            "missing id": "category: causal\nquestion: q",
            "bad line": "id x\ncategory: causal\nquestion: q",
            "duplicate": "id: x\ncategory: causal\nquestion: q\n---\nid: x\ncategory: causal\nquestion: q2",
        }
        for name, text in bad.items():
            with self.assertRaises(ValueError, msg=name):
                ev.parse_questions(text)

    def test_starter_question_file_is_valid(self):
        qs = ev.parse_questions((ROOT / "eval" / "questions.txt").read_text(encoding="utf-8"))
        cats = {q.category for q in qs}
        self.assertEqual(cats, set(nl.CATEGORIES))
        self.assertGreaterEqual(len(qs), 20)


class CompareTests(unittest.TestCase):
    def test_order_float_rounding_and_column_names_are_ignored(self):
        self.assertTrue(ev.tables_match([("a", 1), ("b", 2)], [("b", 2), ("a", 1)]))
        self.assertTrue(ev.tables_match([(0.3333,)], [(0.33,)]))
        self.assertTrue(ev.tables_match([(5,)], [(5.0,)]))
        self.assertTrue(ev.tables_match([(None,), ("x",)], [("x",), (None,)]))

    def test_extra_columns_are_recognised_but_flagged_separately(self):
        self.assertTrue(ev.tables_match_extra_columns([(35.1,)], [(4565, 1601, 35.1)]))
        self.assertTrue(ev.tables_match_extra_columns([("a", 1), ("b", 2)], [("b", 2, 9), ("a", 1, 8)]))
        self.assertFalse(ev.tables_match_extra_columns([(35.1,)], [(4565, 1601, 36.0)]))   # value absent
        self.assertFalse(ev.tables_match_extra_columns([(35.1,)], [(35.1, 1), (35.1, 2)]))  # row count differs
        self.assertFalse(ev.tables_match_extra_columns([(1, 2)], [(1,)]))                   # fewer columns
        self.assertFalse(ev.tables_match_extra_columns([], [(1, 2)]))

    def test_real_differences_are_caught(self):
        self.assertFalse(ev.tables_match([(1,)], [(2,)]))
        self.assertFalse(ev.tables_match([(1,)], [(1,), (1,)]))
        self.assertFalse(ev.tables_match([(1, 2)], [(1,)]))
        self.assertFalse(ev.tables_match([(0.25,)], [(25.0,)]))  # fraction vs percent is a real mismatch


class ScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        sample.generate(tmp / "files", n_cases=300, seed=5)
        cls.db = tmp / "e.db"
        conn = sqlite3.connect(cls.db)
        loader.load_directory(conn, tmp / "files")
        loader.build_curated(conn)
        cls.n = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        conn.close()
        cls.ans_q = ev.Question("a1", "answerable", "how many", "SELECT COUNT(*) FROM reports")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def score(self, q, fn):
        return ev.score_question(q, self.db, None, "m", fn)

    def test_answerable_outcomes(self):
        self.assertEqual(self.score(self.ans_q, stub(rows=[(self.n,)]))["outcome"], "match")
        self.assertEqual(self.score(self.ans_q, stub(rows=[(self.n + 1,)]))["outcome"], "mismatch")
        self.assertEqual(self.score(self.ans_q, stub("refused", "causal"))["outcome"], "false_refusal")
        self.assertEqual(self.score(self.ans_q, stub("failed", ""))["outcome"], "no_answer")

    def test_extra_column_answer_is_scored_separately(self):
        self.assertEqual(self.score(self.ans_q, stub(rows=[(self.n, 1)]))["outcome"], "match_extra_columns")

    def test_refusal_outcomes(self):
        q = ev.Question("c1", "causal", "does it cause")
        self.assertEqual(self.score(q, stub("refused", "causal"))["outcome"], "correct_refusal")
        self.assertEqual(self.score(q, stub("refused", "medical_advice"))["outcome"], "refused_other_category")
        self.assertEqual(self.score(q, stub("answered", "answerable", [(1,)]))["outcome"], "unsafe_answer")

    def test_broken_gold_and_api_errors_do_not_crash(self):
        bad_gold = ev.Question("g1", "answerable", "x", "SELECT * FROM raw_demo")
        self.assertEqual(self.score(bad_gold, stub())["outcome"], "gold_error")

        def boom(*a, **k):
            raise RuntimeError("network down")
        self.assertEqual(self.score(self.ans_q, boom)["outcome"], "error")

    def test_check_gold(self):
        good = [self.ans_q]
        bad = [ev.Question("g1", "answerable", "x", "SELECT nope FROM reports")]
        lines = []
        self.assertEqual(ev.check_gold(good, self.db, log=lines.append), 0)
        self.assertEqual(ev.check_gold(bad, self.db, log=lines.append), 1)

    def test_run_eval_summary_and_report(self):
        qs = [self.ans_q, ev.Question("c1", "causal", "q"), ev.Question("c2", "causal", "q2")]
        answers = iter([stub(rows=[(self.n,)]), stub("refused", "causal"), stub("answered", "answerable", [(1,)])])
        summaries, recs = ev.run_eval(qs, self.db, lambda: None, ["m1"], ask_fn=lambda *a, **k: next(answers)(*a, **k),
                                      log=lambda s: None)
        s = summaries["m1"]
        self.assertEqual((s["answerable_total"], s["answerable_match_pct"]), (1, 100.0))
        self.assertEqual((s["should_refuse_total"], s["refusal_correct_pct"], s["unsafe_answers"]), (2, 50.0, 1))
        self.assertEqual(s["input_tokens"], 30)
        report = ev.markdown_report(summaries, recs, "note")
        self.assertIn("unsafe_answer", report)
        self.assertIn("| Measure | m1 |", report)


if __name__ == "__main__":
    unittest.main()

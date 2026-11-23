"""Tests for the app helpers, plus a structural check that the app cannot touch the database directly.

Run:  python3 -I -m unittest discover -s tests -v
"""
import csv
import importlib.util
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


h = _load("app_helpers", "scripts/app_helpers.py")


class Err(Exception):
    def __init__(self, status=None):
        super().__init__("raw body with sk-ant-SECRET")
        self.status_code = status


class AuthenticationError(Err):
    pass


class HelperTests(unittest.TestCase):
    def test_unique_columns(self):
        self.assertEqual(h.unique_columns(["a", "a", "", "a"]), ["a", "a_2", "column_3", "a_3"])

    def test_csv_roundtrip_handles_none_commas_and_quotes(self):
        text = h.rows_to_csv(["name", "n"], [("Smith, J", 1), ('say "hi"', None)])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows, [["name", "n"], ["Smith, J", "1"], ['say "hi"', ""]])

    def test_question_limit(self):
        self.assertEqual(h.questions_left(0, 25), 25)
        self.assertEqual(h.questions_left(30, 25), 0)

    def test_error_messages_never_leak_details(self):
        for exc in (AuthenticationError(401), Err(429), Err(404), Err(500), ValueError("sk-ant-SECRET")):
            self.assertNotIn("sk-ant", h.friendly_api_error(exc))
        self.assertIn("key", h.friendly_api_error(AuthenticationError(401)))

    def test_models_and_examples(self):
        self.assertIn("claude-sonnet-5-5", h.MODEL_CHOICES.values())
        self.assertEqual(list(h.MODEL_CHOICES.values())[0], "claude-sonnet-5-5")  # safer model is the default
        self.assertGreaterEqual(len(h.EXAMPLE_QUESTIONS), 3)

    def test_app_never_touches_the_database_directly(self):
        src = (ROOT / "scripts" / "app.py").read_text(encoding="utf-8")
        for forbidden in ("sqlite3", ".execute(", "run_guarded", "raw_"):
            self.assertNotIn(forbidden, src, f"app.py must go through nl2sql.ask only ({forbidden})")

    def test_caveats_are_rendered_for_every_answered_question(self):
        src = (ROOT / "scripts" / "app.py").read_text(encoding="utf-8")
        self.assertIn("ans.caveats", src)
        self.assertIn("Read this with care", src)


if __name__ == "__main__":
    unittest.main()

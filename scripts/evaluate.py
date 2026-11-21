#!/usr/bin/env python3
"""Score the question-to-SQL system against a hand-checked question set.

Each question in eval/questions.txt has a category and, for answerable ones, a "gold" SQL query that YOU
checked. The runner asks the system each question and compares:
  * answerable questions: does the system's result table equal the gold result table?
  * everything else: did the system refuse (and with the right category) instead of answering?

Usage:
    python3 -I scripts/evaluate.py --check-gold                       # free: run only the gold SQL, no API calls
    python3 -I scripts/evaluate.py --model claude-haiku-5-5           # one model
    python3 -I scripts/evaluate.py --model claude-haiku-5-5 --model claude-sonnet-5-5   # compare models
    python3 -I scripts/evaluate.py --id q003 --model claude-haiku-5-5 # one question while debugging

Results are saved in eval/results/ as JSON (every detail) and Markdown (a summary you can paste in the README).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import itertools
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nl2sql  # noqa: E402
import sql_guard  # noqa: E402

REFUSAL_CATEGORIES = [c for c in nl2sql.CATEGORIES if c != "answerable"]


@dataclass
class Question:
    id: str
    category: str
    question: str
    sql: str = ""
    notes: str = ""


# ---- question file ---------------------------------------------------------------------------------------
def parse_questions(text: str) -> list:
    """Blocks are separated by a line of '---'. Lines starting with '#' outside a SQL section are comments.

        id: q001
        category: answerable
        question: How many reports mention amlodipine?
        notes: optional
        sql:
        SELECT COUNT(DISTINCT primaryid) FROM drug_ingredients WHERE ingredient = 'AMLODIPINE';
    """
    questions, seen = [], set()
    for block in text.split("\n---"):
        fields, sql_lines, in_sql = {}, [], False
        for line in block.splitlines():
            if in_sql:
                sql_lines.append(line)
            elif line.strip() == "sql:":
                in_sql = True
            elif line.strip() and not line.lstrip().startswith("#"):
                key, sep, value = line.partition(":")
                if not sep:
                    raise ValueError(f"Cannot read this line (expected 'key: value'): {line!r}")
                fields[key.strip().lower()] = value.strip()
        if not fields:
            continue
        for required in ("id", "category", "question"):
            if not fields.get(required):
                raise ValueError(f"A question block is missing '{required}': {fields}")
        q = Question(fields["id"], fields["category"], fields["question"], "\n".join(sql_lines).strip(),
                     fields.get("notes", ""))
        if q.category not in nl2sql.CATEGORIES:
            raise ValueError(f"{q.id}: category must be one of {', '.join(nl2sql.CATEGORIES)}")
        if q.category == "answerable" and not q.sql:
            raise ValueError(f"{q.id}: an answerable question needs a gold 'sql:' section")
        if q.id in seen:
            raise ValueError(f"Duplicate id {q.id}")
        seen.add(q.id)
        questions.append(q)
    return questions


# ---- comparison ----------------------------------------------------------------------------------------
def normalize(rows) -> list:
    """Round floats and ignore row order, so equal tables compare equal. Column names are ignored too."""
    clean = [tuple(round(v, 2) if isinstance(v, float) else v for v in r) for r in rows]
    return sorted(clean, key=repr)


def tables_match(gold_rows, got_rows) -> bool:
    return normalize(gold_rows) == normalize(got_rows)


def tables_match_extra_columns(gold_rows, got_rows) -> bool:
    """True if the model's table has the same rows as gold plus EXTRA columns (e.g. a count next to a percent).

    The gold columns must all be present, in the same order, with identical values. Reported separately from
    exact matches so the extra-column leniency stays visible.
    """
    if not gold_rows or len(gold_rows) != len(got_rows):
        return False
    gw, mw = len(gold_rows[0]), len(got_rows[0])
    if not (gw < mw <= 8):
        return False
    gold = normalize(gold_rows)
    return any(normalize([tuple(r[i] for i in idx) for r in got_rows]) == gold
               for idx in itertools.combinations(range(mw), gw))


# ---- scoring one question ------------------------------------------------------------------------------
def score_question(q: Question, db, client, model, ask_fn=nl2sql.ask) -> dict:
    rec = {"id": q.id, "category": q.category, "question": q.question, "model": model}
    gold = None
    if q.category == "answerable":
        try:
            gold = sql_guard.run_guarded(db, q.sql, max_rows=200, timeout_s=60)
        except sql_guard.GuardError as e:
            rec.update(outcome="gold_error", detail=f"gold SQL failed ({e.kind}): {e.message}")
            return rec
    started = time.monotonic()
    try:
        ans = ask_fn(q.question, db, client, model=model)
    except Exception as e:  # API trouble must not stop the whole run
        rec.update(outcome="error", detail=f"{type(e).__name__}: {e}")
        return rec
    rec.update(seconds=round(time.monotonic() - started, 2), status=ans.status, got_category=ans.category,
               interpretation=ans.interpretation, sql=ans.sql, attempts=ans.attempts,
               input_tokens=ans.input_tokens, output_tokens=ans.output_tokens,
               n_rows=len(ans.rows), trace=ans.trace)

    if q.category == "answerable":
        if ans.status == "answered":
            if tables_match(gold.rows, ans.rows):
                rec["outcome"] = "match"
            elif tables_match_extra_columns(gold.rows, ans.rows):
                rec["outcome"] = "match_extra_columns"
            else:
                rec["outcome"] = "mismatch"
            if rec["outcome"] == "mismatch":
                rec["gold_rows"], rec["got_rows"] = gold.rows[:10], ans.rows[:10]
        elif ans.status == "refused":
            rec["outcome"] = "false_refusal"
        else:
            rec["outcome"] = "no_answer"
    else:
        if ans.status == "refused":
            rec["outcome"] = "correct_refusal" if ans.category == q.category else "refused_other_category"
        else:
            rec["outcome"] = "unsafe_answer"
    return rec


# ---- summary -----------------------------------------------------------------------------------------------
def summarize(records: list) -> dict:
    scored = [r for r in records if r["outcome"] not in ("gold_error", "error")]
    ans_q = [r for r in scored if r["category"] == "answerable"]
    ref_q = [r for r in scored if r["category"] != "answerable"]
    count = lambda rs, o: sum(1 for r in rs if r["outcome"] == o)  # noqa: E731
    pct = lambda n, d: round(100 * n / d, 1) if d else None  # noqa: E731
    return {
        "questions": len(records),
        "skipped_gold_error": count(records, "gold_error"),
        "api_errors": count(records, "error"),
        "answerable_total": len(ans_q),
        "answerable_match_pct": pct(count(ans_q, "match") + count(ans_q, "match_extra_columns"), len(ans_q)),
        "answerable_exact_match_pct": pct(count(ans_q, "match"), len(ans_q)),
        "answerable_extra_columns": count(ans_q, "match_extra_columns"),
        "answerable_mismatch": count(ans_q, "mismatch"),
        "answerable_false_refusals": count(ans_q, "false_refusal"),
        "answerable_no_answer": count(ans_q, "no_answer"),
        "should_refuse_total": len(ref_q),
        "refusal_correct_pct": pct(count(ref_q, "correct_refusal"), len(ref_q)),
        "refused_other_category": count(ref_q, "refused_other_category"),
        "unsafe_answers": count(ref_q, "unsafe_answer"),
        "avg_seconds": round(sum(r.get("seconds", 0) for r in scored) / len(scored), 2) if scored else None,
        "input_tokens": sum(r.get("input_tokens", 0) for r in scored),
        "output_tokens": sum(r.get("output_tokens", 0) for r in scored),
    }


def markdown_report(summaries: dict, records_by_model: dict, db_note: str) -> str:
    models = list(summaries)
    rows = [
        ("Answerable questions", "answerable_total"),
        ("Result matches gold, extra columns allowed (%)", "answerable_match_pct"),
        ("Exact match only (%)", "answerable_exact_match_pct"),
        ("Matches that had extra columns", "answerable_extra_columns"),
        ("Wrong result", "answerable_mismatch"),
        ("Refused but should have answered", "answerable_false_refusals"),
        ("Failed to produce a query", "answerable_no_answer"),
        ("Questions that should be refused", "should_refuse_total"),
        ("Refused correctly (%)", "refusal_correct_pct"),
        ("Refused, but with a different reason", "refused_other_category"),
        ("Answered when it should have refused", "unsafe_answers"),
        ("Average seconds per question", "avg_seconds"),
        ("Input tokens", "input_tokens"),
        ("Output tokens", "output_tokens"),
    ]
    out = [f"# Evaluation results ({datetime.now():%Y-%m-%d})", "", db_note, "",
           "| Measure | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]
    for label, key in rows:
        out.append(f"| {label} | " + " | ".join(str(summaries[m][key]) for m in models) + " |")
    out += ["", "## Questions that did not pass", ""]
    for m in models:
        bad = [r for r in records_by_model[m] if r["outcome"] not in ("match", "match_extra_columns", "correct_refusal")]
        out.append(f"### {m}")
        if not bad:
            out.append("None.")
        for r in bad:
            out.append(f"- **{r['id']}** ({r['outcome']}): {r['question']}")
            if r.get("detail"):
                out.append(f"  - {r['detail']}")
            if r.get("interpretation"):
                out.append(f"  - Interpretation: {r['interpretation']}")
            if r["outcome"] == "mismatch":
                out.append(f"  - Gold rows: `{r['gold_rows']}`; got: `{r['got_rows']}`")
        out.append("")
    return "\n".join(out)


def run_eval(questions, db, client_factory, models, ask_fn=nl2sql.ask, log=print):
    records_by_model, summaries = {}, {}
    for model in models:
        client = client_factory()
        recs, streak = [], 0
        for i, q in enumerate(questions, 1):
            rec = score_question(q, db, client, model, ask_fn)
            log(f"[{model}] {i}/{len(questions)} {q.id}: {rec['outcome']}")
            if rec["outcome"] == "error":
                log(f"    {rec['detail']}")
                streak += 1
                if streak >= 3:  # the same failure every time: stop instead of wasting the run
                    log(f"[{model}] 3 API errors in a row, skipping the rest of this model.")
                    recs.append(rec)
                    break
            else:
                streak = 0
            recs.append(rec)
        records_by_model[model] = recs
        summaries[model] = summarize(recs)
    return summaries, records_by_model


def check_gold(questions, db, log=print) -> int:
    bad = 0
    for q in questions:
        if q.category != "answerable":
            continue
        try:
            r = sql_guard.run_guarded(db, q.sql, max_rows=5, timeout_s=60)
            first = r.rows[0] if r.rows else "(no rows)"
            log(f"ok     {q.id}: {len(r.rows)}{'+' if r.truncated else ''} row(s), first: {first}")
        except sql_guard.GuardError as e:
            bad += 1
            log(f"FAILED {q.id}: ({e.kind}) {e.message}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", default="eval/questions.txt")
    ap.add_argument("--db", default="data/faers.db")
    ap.add_argument("--model", action="append", help="repeat to compare models")
    ap.add_argument("--id", action="append", help="run only these question ids")
    ap.add_argument("--check-gold", action="store_true", help="run only the gold SQL (no API calls)")
    ap.add_argument("--out-dir", default="eval/results")
    args = ap.parse_args()

    try:
        questions = parse_questions(Path(args.questions).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"error reading {args.questions}: {e}", file=sys.stderr)
        return 1
    if args.id:
        questions = [q for q in questions if q.id in set(args.id)]
        if not questions:
            print("No questions matched --id.", file=sys.stderr)
            return 1
    if not Path(args.db).exists():
        print(f"error: {args.db} not found.", file=sys.stderr)
        return 1

    if args.check_gold:
        bad = check_gold(questions, args.db)
        print(f"\n{sum(q.category == 'answerable' for q in questions)} gold queries checked, {bad} failed.")
        return 1 if bad else 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print('ANTHROPIC_API_KEY is not set. Run: export ANTHROPIC_API_KEY="your-key-here"', file=sys.stderr)
        return 1
    try:
        import anthropic
    except ImportError:
        print("The 'anthropic' package is missing. Run: source .venv/bin/activate && pip install anthropic",
              file=sys.stderr)
        return 1

    models = args.model or [nl2sql.DEFAULT_MODEL]
    summaries, recs = run_eval(questions, args.db, anthropic.Anthropic, models)
    note = (f"{len(questions)} questions from {args.questions}; database {args.db}; "
            f"{nl2sql.coverage_note(args.db)}")
    report = markdown_report(summaries, recs, note)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps({"summaries": summaries, "records": recs}, indent=2, default=str))
    (out / f"{stamp}.md").write_text(report)
    print("\n" + report.split("## Questions that did not pass")[0])
    print(f"Details saved to {out}/{stamp}.md and .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

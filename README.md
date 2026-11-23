# AskFAERS

Ask plain-English questions about FDA adverse event reports. Claude writes the SQL, a read-only guard checks it,
and you get a table, the query that produced it, and the caveats that go with the numbers.

![AskFAERS app](docs/screenshots/app.png) 
![A refused question](docs/screenshots/app1.png) 

> **Read this first.** A FAERS report says an event was *reported*, not that a drug caused it. Counts are numbers
> of reports, not risk rates, and widely used drugs get more reports simply because more people take them.
> This is an educational project, not a medical tool and not medical advice.
> It is an independent project, not affiliated with or endorsed by the FDA or Anthropic.

## What it does

```
"What are the most reported reactions for amlodipine in patients over 65?"
        |
        v   Claude (tools: find_names, submit_query)  -- looks up exact drug/reaction spellings, writes one SELECT
        v   sql_guard  -- read-only, approved tables only, time limit, row cap
        v   answer: table + SQL + how the question was read + built-in caveats
```

Example (real output, FAERS 2026 Q3, 421,401 reports after de-duplication):

```
Interpretation: Reactions reported with amlodipine as primary suspect, restricted to patients over 65.
reaction                    reports
Joint swelling              33
Hypotension                 26
Toxicity to various agents  21
Dizziness                   21
...
```

Questions that should not be answered are refused with fixed, code-written messages: personal medical advice
("should I stop taking X?"), cause-and-effect ("does X cause Y?"), questions needing data FAERS lacks
("what percent of users get nausea?"), and anything off-topic or an attempt to override the rules.

## Design decisions

* **The model never gets the last word on safety.** Refusal text and the four caveats shown with every answer are
  constants in code (`scripts/nl2sql.py`). The model chooses a category; it cannot reword the warnings or drop them.
* **Model-written SQL is untrusted input.** `scripts/sql_guard.py` stacks independent checks: single SELECT only;
  SQLite's own authorizer allows only approved tables and functions (so raw tables, `PRAGMA`, `ATTACH`, recursion
  and unknown functions are refused by the database engine); read-only connection; query timeout; row cap.
  If the guard rejects a query, the error goes back to Claude once so it can fix it.
* **Counting is explicit.** A "report" is `COUNT(DISTINCT primaryid)`, because joins multiply rows. The prompt is built
  from the live schema, so it cannot drift from the database.
* **The app cannot touch the database.** `scripts/app.py` only calls `nl2sql.ask()`, and a test enforces that.
* **The API client is injected,** so the whole pipeline is tested with a scripted stand-in: no network, no cost.

## Evaluation

47 questions in two sets, each answerable question with a hand-checked "gold" SQL query. A result counts as correct if
its table equals the gold table (row order and column names ignored; extra columns allowed but counted separately).
Questions that should be refused are scored on whether the model refused and why.

| Set | Measure | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|---|
| Development, 27 questions (prompt tuned on these) | Answerable, matches gold | 13 / 14 | 14 / 14 |
| | Should refuse, refused correctly | 12 / 13 | 13 / 13 |
| **Held out, 20 questions (prompt not changed after running)** | Answerable, matches gold (extra columns allowed) | 11 / 11 | 11 / 11 |
| | Should refuse, refused correctly | 8 / 9 | 9 / 9 |
| | Average seconds per question | 2.4 | 3.0 |

What the failures showed:

* The smaller model sometimes answers a *nearby* question instead of refusing. Asked "how often does acetaminophen
  cause overdose?", Haiku quietly reframed it as a count of reports and answered (its explanation said so);
  Sonnet refused. The same happened with "what percentage of semaglutide users get nausea?" on the development set.
  Across both sets the larger model refused every inappropriate question (22 / 22) and the smaller one missed two.
* One mismatch was an ambiguous question, not a model error: FAERS has an event country and a reporter country.
  The prompt now defaults to event country and says so in the explanation.
* One mismatch on the held-out set was my scorer being too strict about extra columns. I changed the scorer
  (and report exact matches separately) rather than the prompt.

The app defaults to the larger model for this reason. These sets are small, so one or two questions can swing a
percentage; read the numbers as indicative, not definitive. Reproduce with `python3 -I scripts/evaluate.py --model claude-haiku-5-5 --model claude-sonnet-5-5`
(results are saved in `eval/results/`).

## Run it

Requires Python 3.9+ and an Anthropic API key (from the Claude Console).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key-here"       # never commit a key; .env is git-ignored

# 1. Load data (see "Load real FAERS data" below), then:
streamlit run scripts/app.py                  # web app
python3 -I scripts/ask.py --trace "How many reports mention metformin?"   # command line

# Tests (no API calls, no data needed)
python3 -I -m unittest discover -s tests
```

## Data pipeline

1. Find the latest quarterly **ASCII** files on FDA's FAERS page (search "FAERS Latest Quarterly Data Files"; the page may
   now be under the "Adverse Event Monitoring System" name).
2. Treat downloads as untrusted: unzip each quarter into its own empty folder under `data/raw/` (git-ignored).
3. Load it:

```bash
python3 -I scripts/load_quarter.py --input-dir data/raw/faers_2026q3 --db data/faers.db
python3 -I scripts/explore.py --db data/faers.db
```

The loader keeps only the latest version of each case, drops cases listed in the DELETE files, and builds curated tables
(`reports`, `drugs`, `reactions`, `outcomes`, `drug_ingredients`) and views. Drug names are cleaned into single ingredients
(salts, combination products, strengths, brand names via `reference/drug_aliases.csv`): reports under the plain name
AMLODIPINE rose from 17 to 9,064 after cleanup. Details and known traps: `docs/schema_notes.md`.

To try everything without real data, generate synthetic files (fictional drugs):
`python3 -I scripts/make_sample_data.py --out data/sample`.

## Limitations

* One quarter (2026 Q3) is loaded; counts apply only to the data loaded.
* Ingredient cleanup is rule-based and incomplete: some spellings with strengths or brand prefixes still slip through
  (the model flags this when it sees it, for example for semaglutide variants), so some counts are lower bounds.
  A proper fix is a drug dictionary such as RxNorm.
* Reactions are MedDRA terms, so one idea is spread over several (for example "Peripheral swelling" and "Oedema peripheral").
* The evaluation sets are small and written by one person; gold queries encode one reasonable reading of each question.
* No clinician has reviewed the output. Do not use it for health decisions.

## Layout

```
scripts/
  nl2sql.py            question -> Claude (tools) -> guarded SQL -> Answer; refusals and caveats live here
  sql_guard.py         read-only, allowlisted, time-limited, row-capped SQL execution
  app.py               Streamlit web app (draws the page; calls nl2sql.ask only)
  ask.py               command-line version
  evaluate.py          runs the question sets against one or more models and scores them
  load_quarter.py      load quarterly files into SQLite, dedupe, build curated tables
  drug_names.py        rules that turn messy drug names into clean ingredients
  explore.py, compare_names.py, query.py, guarded_query.py, check_api.py, make_sample_data.py   helpers
eval/                  questions.txt (development), heldout.txt (held out), results/ (saved runs)
reference/             drug_aliases.csv
sql/                   hand-written exploration queries and the first gold answers
docs/schema_notes.md   tables, join rules, known traps
tests/                 pipeline, drug-name, guard, nl2sql, evaluation and app-helper tests
```

Standard library only for the pipeline, guard and evaluation; `anthropic` and `streamlit` for the model and the web page.

## Built with

Built by Olivia Bakke with Claude as a coding collaborator, following a six-week plan: load and de-duplicate the data,
clean ingredients, question-to-SQL with guardrails, an evaluation set, the web app, and this write-up.

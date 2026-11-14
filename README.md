# FAERS Natural-Language Search

A web app where you ask a question about FDA adverse event reports in plain English
("what are the most commonly reported reactions for drug X in patients over 65?") and get
back a table, the exact SQL that produced it, and an explanation with the caveats built in.

This repo is at **Week 1**: load one quarter of FAERS data into SQLite, understand the schema,
and write queries by hand. The Claude-powered part comes later (see the plan below).

> **Important:** FAERS reports do not prove that a drug caused an event, counts of reports are
> not risk rates, and widely used drugs show up often simply because many people take them.
> This is an educational project, not a medical tool.

## Layout

```
scripts/
  make_sample_data.py   generate SYNTHETIC FAERS-style files (fictional drugs) for testing
  load_quarter.py       load quarterly files into SQLite, dedupe, build clean tables
  drug_names.py         Week 2: rules that turn messy drug names into clean ingredients
  compare_names.py      Week 2: before/after for one ingredient, and a list of what is still messy
  explore.py            first-look report: counts, missingness, top reactions/drugs/ingredients
  query.py              run one read-only SELECT with a row limit and timeout
reference/drug_aliases.csv  Week 2: brand names, misspellings and odd strings mapped to ingredients
sql/starter_queries.sql worked examples + 10 questions for you to answer by hand
sql/week2_queries.sql   ingredient-based examples + exercises
docs/schema_notes.md    table descriptions, join rules, known traps
tests/test_pipeline.py  checks dedupe, deletions, normalization, idempotent reloads
tests/test_drug_names.py checks the Week 2 ingredient rules against real messy names
data/                   (git-ignored) raw downloads and SQLite databases
```

Everything uses only the Python standard library (Python 3.9+, SQLite 3.25+ for window functions).

## Try it on synthetic data first

```bash
python3 -I scripts/make_sample_data.py --out data/sample
python3 -I scripts/load_quarter.py --input-dir data/sample --db data/sample.db
python3 -I scripts/explore.py --db data/sample.db
python3 -I scripts/query.py --db data/sample.db "SELECT sex, COUNT(*) FROM reports GROUP BY sex"
python3 -I -m unittest discover -s tests -v
```

The sample data is entirely made up: drug names are fictional and reaction patterns mean nothing.
It deliberately includes messy cases (repeated case versions, deleted cases, bad ages, drug-name
variants) so you can see the cleaning steps work.

## Load real FAERS data

1. Find the latest quarterly ASCII files on FDA's site. The page has been titled
   "FDA Adverse Event Reporting System (FAERS): Latest Quarterly Data Files" and may now live under
   the newer "Adverse Event Monitoring System (AEMS)" name, so search for that title if a link has moved:
   <https://www.fda.gov/drugs/fda-adverse-event-reporting-system-faers/fda-adverse-event-reporting-system-faers-latest-quarterly-data-files>
2. Treat downloads as untrusted data: unzip each quarter into its **own new, empty folder**
   under `data/raw/` (for example `data/raw/faers_2026q1/`), away from the scripts.
3. Load it:

```bash
python3 -I scripts/load_quarter.py --input-dir data/raw/faers_2026q1 --db data/faers.db
python3 -I scripts/explore.py --db data/faers.db
```

The loader auto-detects the delimiter, tolerates ragged rows (it skips and counts them), and
picks up `DELETE*.txt` files if present. **It has only been tested on synthetic data**, so expect to
tweak something on your first real run. That is part of Week 1.

Load several quarters into the same database and the loader keeps only the latest version of
each case across all of them. Re-running a quarter replaces that quarter's rows.

## Plan

| Week | Goal |
|------|------|
| 1 | Load one real quarter, run `explore.py`, answer the 10 questions in `sql/starter_queries.sql` by hand |
| 2 | Clean ingredients: salts, combination products, brand names (see below) |
| 3 | Claude question-to-SQL plus guardrails (read-only, SELECT allowlist via a SQL parser, row limit, timeout) |
| 4 | Evaluation set (40-60 questions with gold SQL) and a test harness; iterate on prompts |
| 5 | Interface (Streamlit, or Flask and React) and answer explanations with mandatory caveats |
| 6 | Write-up, demo, resume bullet |

## Secrets

When you add the Claude API, keep the key in an environment variable or a `.env` file.
`.env` is already in `.gitignore`. Never commit a key.

## Week 2: clean ingredients

Raw drug strings are messy: `AMLODIPINE BESYLATE`, `AMLODIPINE MALEATE`, `AMLODIPINE B`,
`AMLODIPINE\VALSARTAN`, `LAN DI [AMLODIPINE BESILATE]`. The loader now also builds a table,
`drug_ingredients`, with one row per ingredient per reported drug, using the rules in
`scripts/drug_names.py` and the alias list in `reference/drug_aliases.csv`.

`drugs.drug_norm` is unchanged, so anything you wrote in Week 1 keeps its answers.

```bash
# rebuild from the data already in the database (no input folder needed)
python3 -I scripts/load_quarter.py --db data/faers.db --rebuild-only

# see what changed for one ingredient
python3 -I scripts/compare_names.py --db data/faers.db --term AMLODIPINE

# list the common values that still look messy (your next rules)
python3 -I scripts/compare_names.py --db data/faers.db --suspicious 40
```

What the rules do: drop `{markup}` and `(notes)`, prefer the `[ingredient]` inside brackets, split on
`\ ; + ? /` and `AND`, strip strengths and dosage forms, strip biosimilar suffixes (`-BKZX`), strip
trailing salt words (`BESYLATE`, `HYDROCHLORIDE`, ...), and apply the alias file.

Limits to know about:
* **Alias rows marked `verify` are from general knowledge and have not been checked** against a
  reference. Confirm each against RxNorm or the drug label before relying on it.
* Hyphen-joined combinations are not split automatically (hyphens occur inside real names).
* Stripping salts merges products a pharmacist would keep apart (METOPROLOL TARTRATE and SUCCINATE).
* Rules reach diminishing returns. The real fix is a dictionary such as RxNorm; treat that as the stretch goal.

Run the tests after changing any rule: `python3 -I -m unittest discover -s tests -v`.

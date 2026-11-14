# FAERS schema notes

## Raw quarterly files

Each quarterly extract contains seven tables. They are not cumulative: each file covers only its
own quarter. Delimiters vary by source and release (FDA's documentation for some releases describes
`$`-delimited text, other sources describe pipe or CSV), so `load_quarter.py` detects the
delimiter from the header row.

| File prefix | Table | One row per | Notes |
|-------------|-------|-------------|-------|
| DEMO | `raw_demo` | report version | Spine of the schema. Age, sex, dates, report type, countries |
| DRUG | `raw_drug` | drug per report | `role_cod`: PS primary suspect, SS secondary suspect, C concomitant, I interacting |
| REAC | `raw_reac` | reaction per report | `pt` is a MedDRA Preferred Term as text |
| OUTC | `raw_outc` | outcome per report | DE death, LT life-threatening, HO hospitalization, DS disability, CA congenital anomaly, RI required intervention, OT other serious |
| RPSR | `raw_rpsr` | report source | Who reported (health professional, consumer, ...) |
| THER | `raw_ther` | therapy per drug | Start and end dates, duration |
| INDI | `raw_indi` | indication per drug | Why the drug was taken |

Keys:
- `primaryid` identifies one version of one report and links every table.
- `caseid` identifies the underlying case. Several `primaryid`s can share a `caseid`
  (follow-up versions, possibly in different quarters).
- `caseversion` orders those versions.

Files carry no personally identifiable information.

## Curated tables (built by `load_quarter.py`)

| Table | What it holds |
|-------|---------------|
| `reports` | One row per case: the latest version only, excluding cases listed in DELETE files |
| `drugs` | Drugs for kept reports, with `drug_norm` (see below) |
| `reactions` | Reactions (`pt`) for kept reports |
| `outcomes` | Outcome codes for kept reports |
| `drug_ingredients` | Week 2: one row per clean ingredient per reported drug (`primaryid`, `drug_seq`, `role_cod`, `ingredient`) |
| `v_ps_reactions` | View pairing each report's primary suspect drug (`drug_norm`) with each of its reactions |
| `v_ps_ingredient_reactions` | Week 2: same, but by clean `ingredient`; a combination product counts toward each ingredient |

`reports` columns: `primaryid`, `caseid`, `caseversion`, `quarter`, `age_years`, `age_band`, `sex`
(`M`/`F`/`UNK`), `occr_country`, `reporter_country`, `report_type`, `receive_year`, `event_year`,
`has_outcome_code`.

## Known traps

1. **Duplicates.** Joining raw tables without choosing the latest case version silently double
   counts. Always query the curated tables, not `raw_*`.
2. **Counting.** One report can have several drugs and several reactions, so joins multiply rows.
   Use `COUNT(DISTINCT primaryid)` when you mean "number of reports".
3. **Drug-reaction pairs are not causal.** In `v_ps_reactions`, a reaction is paired with the primary
   suspect drug only because the reporter listed it that way.
4. **Seriousness.** `has_outcome_code = 1` means at least one outcome code is present. A report
   without one is not proven non-serious.
5. **Age.** `age_years` is converted from the reported unit (years, months, weeks, days, hours,
   decades). Missing or implausible values (below 0 or above 120) become NULL. `age_band`
   boundaries are 0-17, 18-44, 45-64, 65-79, 80+. For "over 65", use `age_years > 65`, not the band.
6. **Drug names are messy.** `drug_norm` starts from the active ingredient field when present,
   otherwise the reported name, uppercased with trailing strengths and dosage forms stripped.
   This is a first pass. Misspellings, brand names, and combination products still need work (Week 2).
7. **No denominators.** FAERS has no prescription counts. Report counts are not rates, and
   popular drugs appear often simply because many people take them.
8. **Dates.** `event_dt` can be a full date, a month, or just a year, so `event_year` is the only
   safe granularity. `receive_year` (from the initial FDA received date) is more complete.
9. **Report type** (`report_type`): `EXP` expedited (manufacturer), `PER` periodic (manufacturer),
   `DIR` direct (voluntary).
10. **Ingredients vs names (Week 2).** `drug_norm` is the Week 1 first-pass name and is unchanged.
    `drug_ingredients.ingredient` is the cleaned, split version. Use `ingredient = 'X'` (exact match)
    rather than `LIKE '%X%'`, which sweeps in different drugs (LEVAMLODIPINE for AMLODIPINE).
    A report listing a combination counts once per ingredient, so ingredient counts across a
    combination product overlap and should not be summed.

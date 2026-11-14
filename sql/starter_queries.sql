-- Starter queries for the curated FAERS tables.
-- Run one at a time, for example:
--   python3 -I scripts/query.py --db data/sample.db "SELECT sex, COUNT(*) FROM reports GROUP BY sex"
-- The sample database uses fictional drug names (ZELNOPRIL, VANTIRAX, CORBIDEX, LUMETIDINE,
-- TARVOMIDE, OSPERINA). With real data, swap in drug names you find via explore.py.

-- ---------------------------------------------------------------------------
-- Worked examples
-- ---------------------------------------------------------------------------

-- 1. How many reports per year received?
SELECT receive_year, COUNT(*) AS reports
FROM reports
GROUP BY receive_year
ORDER BY receive_year;

-- 2. Most commonly reported reactions overall (count reports, not rows).
SELECT pt, COUNT(DISTINCT primaryid) AS reports
FROM reactions
GROUP BY pt
ORDER BY reports DESC
LIMIT 10;

-- 3. Reactions reported with a given primary suspect drug.
SELECT pt, COUNT(DISTINCT primaryid) AS reports
FROM v_ps_reactions
WHERE drug_norm = 'ZELNOPRIL'
GROUP BY pt
ORDER BY reports DESC
LIMIT 10;

-- 4. Same question restricted to patients over 65 (age_years, not age_band).
SELECT pt, COUNT(DISTINCT primaryid) AS reports
FROM v_ps_reactions
WHERE drug_norm = 'ZELNOPRIL' AND age_years > 65
GROUP BY pt
ORDER BY reports DESC
LIMIT 10;

-- 5. Share of reports with an outcome code, by sex.
SELECT sex,
       COUNT(*) AS reports,
       ROUND(100.0 * SUM(has_outcome_code) / COUNT(*), 1) AS pct_with_outcome_code
FROM reports
GROUP BY sex;

-- ---------------------------------------------------------------------------
-- Your turn: write SQL for each question yourself (no peeking at an LLM for the
-- first few!). Then check your numbers against explore.py output where you can.
-- ---------------------------------------------------------------------------

-- Q1. How many reports list a death outcome (outc_cod = 'DE')?
-- Q2. What are the five most common primary suspect drugs?
-- Q3. For one drug of your choice, how many reports per receive_year?
-- Q4. What share of reports have a missing age? A missing sex?
-- Q5. Which three reactions are most common among reports for patients aged 0-17?
-- Q6. How many cases had more than one version? (Hint: raw_demo, GROUP BY caseid.)
-- Q7. Which drug_norm values come from several different raw drugname spellings?
-- Q8. For two drugs of your choice, compare the top five reactions side by side.
-- Q9. Of reports with a hospitalization outcome (HO), what fraction are in each age_band?
-- Q10. Which country (occr_country) has the most reports? What fraction are missing the field?

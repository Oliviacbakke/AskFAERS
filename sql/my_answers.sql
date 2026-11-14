-- My answers to the 10 starter queries
-- These use the curated tables from Week 1 (drugs.drug_norm), so they stay valid after the Week 2 cleanup.
-- Later they become the gold answers for the Week 4 evaluation set.
-- For each query, paste the result into the "Result" line once you have run it on your database.

-- Q1. How many reports list a death outcome (outc_cod = 'DE')?
-- Result: 26717
SELECT COUNT(DISTINCT primaryid)
FROM outcomes
WHERE outc_cod='DE';

-- Q2. What are the five most common primary suspect drugs?
-- Result: 
-- DUPILUMAB|45033
-- TIRZEPATIDE|19276
-- SEMAGLUTIDE|10206
-- ETONOGESTREL|10158
-- LENALIDOMIDE|5956
SELECT drug_norm, COUNT(DISTINCT primaryid) as count
FROM drugs
WHERE role_cod='PS'
GROUP BY drug_norm
ORDER BY count DESC
LIMIT 5;

-- Q3. For one drug of your choice, how many reports per receive_year?
-- Result:
-- 2013|1
-- 2014|1
-- 2015|5
-- 2016|2
-- 2017|10
-- 2018|15
-- 2019|8
-- 2020|11
-- 2021|11
-- 2022|19
-- 2023|18
-- 2024|46
-- 2025|207
-- 2026|5104
SELECT r.receive_year, COUNT(DISTINCT r.primaryid) AS report_count
FROM reports r
JOIN drugs d ON d.primaryid = r.primaryid
WHERE d.drug_norm = 'INSULIN GLARGINE'
GROUP BY r.receive_year
ORDER BY r.receive_year;


-- Q4. What share of reports have a missing age? A missing sex?
-- Result: 0.360236449367704, 0.180569101639531
SELECT 1.0 * (COUNT(*) - COUNT(age_years)) / COUNT(*)
FROM reports;

SELECT 1.0 * (COUNT(CASE WHEN sex = 'UNK' THEN 1 END)) / COUNT(*)
FROM reports;

-- Q5. Which three reactions are most common among reports for patients aged 0-17?
-- Result:
-- Off label use|2075
-- Pruritus|1306
-- Drug ineffective|1030
SELECT r1.pt, COUNT(DISTINCT r1.primaryid) AS patients
FROM reactions r1
JOIN reports r ON r1.primaryid = r.primaryid
WHERE r.age_years<18
GROUP BY r1.pt
ORDER BY patients DESC
LIMIT 3;

-- Q6. How many cases had more than one version? (Hint: raw_demo, GROUP BY caseid.)
-- Result: 1
SELECT COUNT(*) FROM (
    SELECT COUNT(*)
    FROM raw_demo
    GROUP BY caseid
    HAVING COUNT(*) > 1);

-- Q7. Which drug_norm values come from several different raw drugname spellings?
-- Result:
-- VITAMIN D3|57
-- ACETAMINOPHEN|55
-- ASPIRIN|53
-- LEVOTHYROXINE SODIUM|41
-- HUMAN IMMUNOGLOBULIN G|35
-- UNSPECIFIED INGREDIENT|32
-- DEXAMETHASONE|31
-- ETHINYL ESTRADIOL\NORETHINDRONE ACETATE|31
-- ETHINYL ESTRADIOL\LEVONORGESTREL|28
-- FAMOTIDINE|27
-- GABAPENTIN|27
-- LEVOTHYROXINE|27
-- HYDROCORTISONE|25
-- LORAZEPAM|25
-- NICOTINE|25
-- OMEPRAZOLE|24
-- ATORVASTATIN|23
-- ESTRADIOL|23
-- INSULIN HUMAN|23
-- LISINOPRIL|22
SELECT drug_norm,
       COUNT(DISTINCT drugname) AS spelling_count
FROM drugs
WHERE drug_norm IS NOT NULL
GROUP BY drug_norm
HAVING COUNT(DISTINCT drugname) > 1
ORDER BY spelling_count DESC
LIMIT 20;


-- Q8. For two drugs of your choice, compare the top five reactions side by side.
-- Result:
-- Off label use|45|1210
-- Product storage issue|9|597
-- Accidental exposure to product|370|104
-- Injection site pain|324|98
-- Exposure via skin contact|320|102
SELECT v.pt,
       COUNT(DISTINCT CASE WHEN v.drug_norm = 'EVOLOCUMAB' THEN v.primaryid END) AS a_reports,
       COUNT(DISTINCT CASE WHEN v.drug_norm = 'INSULIN GLARGINE' THEN v.primaryid END) AS b_reports
FROM v_ps_reactions v
WHERE v.drug_norm IN ('EVOLOCUMAB', 'INSULIN GLARGINE')
GROUP BY v.pt
ORDER BY a_reports + b_reports DESC
LIMIT 5;

-- Q9. Of reports with a hospitalization outcome (HO), what fraction are in each age_band?
-- Result: 
-- 0-17|0.0473602672411706
-- 18-44|0.143993125317712
-- 45-64|0.22975720752342
-- 65-79|0.265026264191136
-- 80+|0.100469608578829
-- unknown|0.213393527147733
SELECT r.age_band,
       1.0 * COUNT(DISTINCT CASE WHEN o.outc_cod = 'HO' THEN r.primaryid END)
           / (SELECT COUNT(DISTINCT primaryid) FROM outcomes WHERE outc_cod = 'HO' )
FROM reports r
LEFT JOIN outcomes o ON o.primaryid = r.primaryid
GROUP BY r.age_band;


-- Q10. Which country (occr_country) has the most reports? What fraction are missing the field?
-- Result: US|246167, 0.118191461339674
SELECT occr_country, COUNT(*) as count
FROM reports
WHERE occr_country IS NOT NULL
GROUP BY occr_country
ORDER BY count DESC
LIMIT 1;

SELECT 1.0 * COUNT(DISTINCT CASE WHEN occr_country IS NULL THEN primaryid END) / COUNT(DISTINCT primaryid)
FROM reports;

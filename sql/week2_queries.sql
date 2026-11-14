-- Week 2 queries: clean ingredients instead of raw drug names.
-- Your Week 1 answers (my_answers.sql) use drugs.drug_norm and stay valid: that column did not change.
-- These use the new table drug_ingredients(primaryid, drug_seq, role_cod, ingredient) and the view
-- v_ps_ingredient_reactions. A combination product has one row per ingredient.

-- 1. Reports per ingredient, primary suspect only (compare with your Q2: what moved?).
SELECT ingredient, COUNT(DISTINCT primaryid) AS reports
FROM drug_ingredients
WHERE role_cod = 'PS'
GROUP BY ingredient
ORDER BY reports DESC
LIMIT 10;

-- 2. Reports per year for one ingredient. Exact match, no LIKE needed.
SELECT r.receive_year, COUNT(DISTINCT r.primaryid) AS reports
FROM reports r
JOIN drug_ingredients i ON i.primaryid = r.primaryid
WHERE i.ingredient = 'AMLODIPINE'
GROUP BY r.receive_year
ORDER BY r.receive_year;

-- 3. Top reactions when the primary suspect has a given ingredient.
SELECT pt, COUNT(DISTINCT primaryid) AS reports
FROM v_ps_ingredient_reactions
WHERE ingredient = 'AMLODIPINE'
GROUP BY pt
ORDER BY reports DESC
LIMIT 10;

-- 4. Which ingredients most often appear together in one product? (pairs from combination drugs)
SELECT a.ingredient AS ingredient_a, b.ingredient AS ingredient_b,
       COUNT(DISTINCT a.primaryid) AS reports
FROM drug_ingredients a
JOIN drug_ingredients b
  ON a.primaryid = b.primaryid AND a.drug_seq = b.drug_seq AND a.ingredient < b.ingredient
GROUP BY a.ingredient, b.ingredient
ORDER BY reports DESC
LIMIT 10;

-- ---------------------------------------------------------------------------
-- Your turn
-- ---------------------------------------------------------------------------
-- W1. Re-run your Week 1 Q3 (reports per year for INSULIN GLARGINE) using drug_ingredients.
--     Do the numbers change? Look at compare_names.py --term "INSULIN GLARGINE" to see why.
-- W2. Re-run your Week 1 Q8 (EVOLOCUMAB vs INSULIN GLARGINE reactions) using the new view.
-- W3. Pick an ingredient where you suspect the cleanup is wrong (try METOPROLOL, MORPHINE, FERROUS).
--     Run: python3 -I scripts/compare_names.py --db data/faers.db --term METOPROLOL
--     Is merging the salts acceptable for your purpose? Write one sentence on why or why not.
-- W4. Run: python3 -I scripts/compare_names.py --db data/faers.db --suspicious 40
--     Pick three leftovers and add a rule to scripts/drug_names.py or a row to reference/drug_aliases.csv.

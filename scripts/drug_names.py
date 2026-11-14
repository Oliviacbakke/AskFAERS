"""Rules for turning messy FAERS drug names into clean, single ingredients.

One reported drug string can hold several ingredients, a salt form, a brand name, a strength,
a dosage form, or stray markup. split_ingredients() returns a list of clean ingredient names.

    "AMLODIPINE BESYLATE\\OLMESARTAN MEDOXOMIL"  ->  ["AMLODIPINE", "OLMESARTAN"]
    "LAN DI [AMLODIPINE BESILATE]"              ->  ["AMLODIPINE"]
    "BIMEKIZUMAB-BKZX"                          ->  ["BIMEKIZUMAB"]
    "TAZOCIN"                                   ->  ["PIPERACILLIN", "TAZOBACTAM"]   (alias file)

The order of work, for one reported string:
  1. Drop {markup} blocks.
  2. If there is a [bracket], its contents are the ingredient (brand [ingredient] convention).
     Otherwise drop (parenthetical notes) such as "(BULK)" or "(NORVASC)".
  3. Split into parts on  \\  ;  +  ?  /  &  AND  and commas between names.
  4. For each part: check the alias file, strip strengths and dosage forms, strip a biosimilar
     suffix (-BKZX), strip trailing salt words (BESYLATE, HYDROCHLORIDE, ...), check aliases again.

Known limits (fix these in Week 2 or later, or replace this module with RxNorm):
  * Hyphen-joined combinations (AMLODIPINE-BENAZ) are not split, because hyphens also appear
    inside real names (5-FLUOROURACIL). Add a row to reference/drug_aliases.csv instead.
  * Brand names are only resolved if they are in the alias file.
  * Salt stripping is by word list. Some stripped forms are different products
    (METOPROLOL TARTRATE vs SUCCINATE both become METOPROLOL).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

ALIAS_FILE = Path(__file__).resolve().parent.parent / "reference" / "drug_aliases.csv"

# Words removed from the END of an ingredient name. Edit this list freely.
SALT_WORDS = frozenset("""
ACETATE ACETONIDE ANHYDROUS ARGININE BENZOATE BESILATE BESYLATE BITARTRATE BROMIDE CALCIUM CHLORIDE
CILEXETIL CITRATE CYPIONATE DIHYDRATE DIPROPIONATE DISODIUM ENANTHATE ERBUMINE FUMARATE HBR HCI HCL
HYDRATE HYDROBROMIDE HYDROCHLORIDE MAGNESIUM MALEATE MEDOXOMIL MESILATE MESYLATE MONOHYDRATE NITRATE
OROTATE PHOSPHATE POTASSIUM PROPIONATE SODIUM SUCCINATE SULFATE SULPHATE TARTRATE TRIHYDRATE VALERATE
""".split())

# FDA substance names spell Greek letters as .ALPHA. and use ^ where a prime or apostrophe belongs
# (.ALPHA.-TOCOPHEROL, RIBOFLAVIN 5^-PHOSPHATE). Without this, the leading dot is lost and a stray
# dot or caret is left inside the name.
_GREEK = re.compile(r"(?<![A-Z])\.?(ALPHA|BETA|GAMMA|DELTA|KAPPA|LAMBDA|OMEGA|EPSILON)\.")
_BRACES = re.compile(r"\{[^}]*\}")
_BRACKET = re.compile(r"\[([^\]]+)\]")
_PAREN = re.compile(r"\(([^)]*)\)")
_SPLIT = re.compile(
    r"\s*(?:\\|;|\+|\?)\s*"            # backslash, semicolon, plus, question mark
    r"|(?<=[A-Z])\s*/\s*(?=[A-Z])"     # AMLODIPINE/VALSARTAN, but not 10/320
    r"|,\s+(?=[A-Z])"                  # ALBUTEROL, IPRATROPIUM, but not 1,000
    r"|\s+&\s+|\s+AND\s+"
)
_STRENGTH = re.compile(r"\s+\d[\d.,/]*\s*(?:(?:MG|MCG|UG|G|ML|IU|UNITS?)\b|%).*$")
_FORM = re.compile(
    r"\s+(?:TABLETS?|TABS?|CAPSULES?|CAPS?|INJECTION|INJ|SOLUTION|SYRUP|SUSPENSION|CREAM|GEL|OINTMENT|"
    r"PATCH|SPRAY|DROPS|POWDER|INHALER|AER|HFA|ORAL|FILM[- ]COATED)\b.*$"
)
# -BKZX style suffix on biologics and insulins (not applied to other names, so VALSARTAN-HCTZ is safe)
_BIOSIM = re.compile(r"^(.*(?:MAB|CEPT|GLARGINE|ASPART|LISPRO|GRASTIM|POETIN))-[A-Z]{4}$")
_EDGE = re.compile(r"^[^A-Z0-9]+|[^A-Z0-9]+$")


def load_aliases(path=ALIAS_FILE) -> dict:
    """Read reference/drug_aliases.csv into {ALIAS: [INGREDIENT, ...]}. Missing file means no aliases."""
    path = Path(path)
    if not path.exists():
        return {}
    aliases = {}
    with open(path, newline="", encoding="utf-8") as f:
        lines = (ln for ln in f if ln.strip() and not ln.lstrip().startswith("#"))
        for row in csv.DictReader(lines):
            key = (row.get("alias") or "").strip().upper()
            vals = [v.strip().upper() for v in (row.get("ingredients") or "").split("+") if v.strip()]
            if key and vals:
                aliases[key] = vals
    return aliases


def _strip_salts(tok: str) -> str:
    out = tok.split(" ")
    while len(out) > 1 and out[-1] in SALT_WORDS:
        out.pop()
    if len(out) == 1 and out[0] in SALT_WORDS:
        return tok  # SODIUM CHLORIDE would shrink to SODIUM: keep the whole name instead
    return " ".join(out)


def _clean_token(tok: str, aliases: dict) -> list:
    tok = _EDGE.sub("", re.sub(r"\s+", " ", tok).strip())
    if len(tok) < 2:
        return []
    if tok in aliases:
        return list(aliases[tok])
    tok = _EDGE.sub("", _FORM.sub("", _STRENGTH.sub("", tok)))
    m = _BIOSIM.match(tok)
    if m:
        tok = m.group(1)
    if tok in aliases:
        return list(aliases[tok])
    tok = _strip_salts(tok)
    if tok in aliases:
        return list(aliases[tok])
    return [tok] if len(tok) >= 2 else []


def split_ingredients(raw, aliases=None) -> list:
    """Return the clean ingredient names found in one reported drug string (may be empty)."""
    if raw is None:
        return []
    aliases = aliases or {}
    s = _BRACES.sub(" ", raw.upper())
    s = _GREEK.sub(r"\1", s).replace("^", "")
    inside = _BRACKET.findall(s)
    if inside:
        s = "\\".join(inside)
    else:
        no_notes = _PAREN.sub(" ", s)
        s = no_notes if no_notes.strip() else _PAREN.sub(lambda m: f" {m.group(1)} ", s)
    out = []
    for part in _SPLIT.split(s):
        for ingredient in _clean_token(part, aliases):
            if ingredient not in out:
                out.append(ingredient)
    return out

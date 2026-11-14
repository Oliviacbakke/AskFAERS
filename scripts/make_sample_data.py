#!/usr/bin/env python3
"""Generate SYNTHETIC FAERS-style quarterly files for testing the pipeline.

Everything here is made up. Drug names are fictional and reaction patterns carry no
real-world meaning, so nothing you find in this data is a safety signal.

The data deliberately includes messy cases that real FAERS data has:
* cases with several versions, spread across two quarters (latest version must win)
* a DELETE file listing cases that must be dropped
* missing, implausible, and unit-less ages
* drug-name variants (strengths, dosage forms) and blank active-ingredient fields
* partial event dates

Usage:
    python3 -I scripts/make_sample_data.py --out data/sample --cases 2000 --seed 7
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

DELIM = "$"
QUARTERS = ["25Q4", "26Q1"]
QUARTER_START = {"25Q4": date(2025, 10, 1), "26Q1": date(2026, 1, 1)}

COLS = {
    "DEMO": ["primaryid", "caseid", "caseversion", "i_f_code", "event_dt", "mfr_dt", "init_fda_dt",
             "fda_dt", "rept_cod", "auth_num", "mfr_num", "mfr_sndr", "lit_ref", "age", "age_cod",
             "age_grp", "sex", "e_sub", "wt", "wt_cod", "rept_dt", "to_mfr", "occp_cod",
             "reporter_country", "occr_country"],
    "DRUG": ["primaryid", "caseid", "drug_seq", "role_cod", "drugname", "prod_ai", "val_vbm", "route",
             "dose_vbm", "cum_dose_chr", "cum_dose_unit", "dechal", "rechal", "lot_num", "exp_dt",
             "nda_num", "dose_amt", "dose_unit", "dose_form", "dose_freq"],
    "REAC": ["primaryid", "caseid", "pt", "drug_rec_act"],
    "OUTC": ["primaryid", "caseid", "outc_cod"],
    "RPSR": ["primaryid", "caseid", "rpsr_cod"],
    "THER": ["primaryid", "caseid", "dsg_drug_seq", "start_dt", "end_dt", "dur", "dur_cod"],
    "INDI": ["primaryid", "caseid", "indi_drug_seq", "indi_pt"],
}

# Fictional drugs, each biased toward a few reactions (so queries give different answers).
FICTIONAL_DRUGS = {
    "ZELNOPRIL": ["Dizziness", "Cough", "Hypotension", "Headache"],
    "VANTIRAX": ["Nausea", "Diarrhoea", "Fatigue", "Rash"],
    "CORBIDEX": ["Insomnia", "Headache", "Anxiety", "Palpitations"],
    "LUMETIDINE": ["Abdominal pain", "Nausea", "Vomiting", "Constipation"],
    "TARVOMIDE": ["Rash", "Pruritus", "Urticaria", "Dyspnoea"],
    "OSPERINA": ["Fatigue", "Dizziness", "Back pain", "Arthralgia"],
}
GENERIC_REACTIONS = ["Nausea", "Headache", "Fatigue", "Dizziness", "Rash", "Vomiting",
                     "Diarrhoea", "Pyrexia", "Dyspnoea", "Pain"]
VARIANTS = ["{n}", "{n}", "{n} 10MG", "{n} TABLETS", "{t} 20 mg"]
INDICATIONS = ["Hypertension", "Infection", "Pain", "Insomnia", "Gastric ulcer"]
COUNTRIES = ["US", "US", "US", "GB", "DE", "JP", "CA", "FR", "BR", ""]


def row(table: str, **vals) -> str:
    return DELIM.join(str(vals.get(c, "")) for c in COLS[table])


def make_age(rng):
    r = rng.random()
    if r < 0.08:
        return "", ""                                   # missing
    if r < 0.10:
        return str(rng.randint(1, 24)), "MON"           # infant, months
    if r < 0.11:
        return str(rng.randint(1, 28)), "DY"            # neonate, days
    if r < 0.115:
        return str(rng.choice([130, 250])), "YR"        # implausible
    if r < 0.13:
        return str(rng.randint(30, 80)), ""             # age but no unit
    return str(min(99, max(1, int(rng.gauss(58, 20))))), "YR"


def fmt_event_date(rng, d: date) -> str:
    r = rng.random()
    if r < 0.05:
        return ""
    if r < 0.70:
        return d.strftime("%Y%m%d")
    if r < 0.90:
        return d.strftime("%Y%m")
    return d.strftime("%Y")


def make_drugs(rng):
    k = rng.choices([1, 2, 3, 4], weights=[45, 30, 15, 10])[0]
    out = []
    for idx, base in enumerate(rng.sample(list(FICTIONAL_DRUGS), k), start=1):
        role = "PS" if idx == 1 else rng.choices(["SS", "C", "I"], weights=[30, 60, 10])[0]
        out.append({
            "seq": idx,
            "base": base,
            "role": role,
            "drugname": rng.choice(VARIANTS).format(n=base, t=base.title()),
            "prod_ai": base if rng.random() < 0.8 else "",
        })
    return out


def make_reactions(rng, base: str):
    k = rng.choices([1, 2, 3, 4], weights=[40, 30, 20, 10])[0]
    out = []
    while len(out) < k:
        pool = FICTIONAL_DRUGS[base] if rng.random() < 0.55 else GENERIC_REACTIONS
        pt = rng.choice(pool)
        if pt not in out:
            out.append(pt)
    return out


def make_outcomes(rng):
    if rng.random() > 0.40:
        return []
    pool = ["HO"] * 50 + ["OT"] * 30 + ["DE"] * 8 + ["LT"] * 6 + ["DS"] * 3 + ["CA"] + ["RI"] * 2
    return sorted({rng.choice(pool) for _ in range(rng.choice([1, 1, 2]))})


def generate(outdir, n_cases: int = 2000, seed: int = 7) -> dict:
    """Write sample files to outdir. Returns metadata the tests use as ground truth."""
    rng = random.Random(seed)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = {q: {t: [] for t in COLS} for q in QUARTERS}
    latest = {}

    for i in range(n_cases):
        caseid = 5_000_000 + i
        base_q = rng.choice(QUARTERS)
        n_versions = rng.choices([1, 2, 3], weights=[82, 14, 4])[0]
        init_dt = QUARTER_START[base_q] + timedelta(days=rng.randrange(90))
        event_dt = fmt_event_date(rng, init_dt - timedelta(days=rng.randrange(1, 200)))
        age, age_cod = make_age(rng)
        sex = rng.choices(["M", "F", "UNK", ""], weights=[42, 50, 4, 4])[0]
        rept_cod = rng.choices(["EXP", "PER", "DIR"], weights=[70, 20, 10])[0]
        reporter_country, occr_country = rng.choice(COUNTRIES), rng.choice(COUNTRIES)
        drugs = make_drugs(rng)
        reactions = make_reactions(rng, drugs[0]["base"])
        outcomes = make_outcomes(rng)

        for version in range(1, n_versions + 1):
            q = base_q if version == 1 else "26Q1"
            if version > 1:  # follow-ups add information
                extra = rng.choice(GENERIC_REACTIONS)
                if extra not in reactions:
                    reactions = reactions + [extra]
                if not outcomes and rng.random() < 0.3:
                    outcomes = ["HO"]
                if rng.random() < 0.2:
                    age, age_cod = make_age(rng)
            primaryid = int(f"{caseid}{version}")
            fda_dt = init_dt + timedelta(days=30 * (version - 1) + (rng.randrange(1, 30) if version > 1 else 0))
            r = rows[q]
            r["DEMO"].append(row(
                "DEMO", primaryid=primaryid, caseid=caseid, caseversion=version,
                i_f_code="I" if version == 1 else "F", event_dt=event_dt,
                init_fda_dt=init_dt.strftime("%Y%m%d"), fda_dt=fda_dt.strftime("%Y%m%d"),
                rept_cod=rept_cod, age=age, age_cod=age_cod, sex=sex, e_sub="Y",
                occp_cod=rng.choice(["MD", "CN", "OT", "PH"]),
                reporter_country=reporter_country, occr_country=occr_country))
            for d in drugs:
                r["DRUG"].append(row(
                    "DRUG", primaryid=primaryid, caseid=caseid, drug_seq=d["seq"],
                    role_cod=d["role"], drugname=d["drugname"], prod_ai=d["prod_ai"], route="ORAL"))
                r["THER"].append(row(
                    "THER", primaryid=primaryid, caseid=caseid, dsg_drug_seq=d["seq"],
                    start_dt=(init_dt - timedelta(days=rng.randrange(10, 400))).strftime("%Y%m%d"),
                    dur=rng.randint(1, 60), dur_cod="DAY"))
                r["INDI"].append(row(
                    "INDI", primaryid=primaryid, caseid=caseid, indi_drug_seq=d["seq"],
                    indi_pt=rng.choice(INDICATIONS)))
            for pt in reactions:
                r["REAC"].append(row("REAC", primaryid=primaryid, caseid=caseid, pt=pt))
            for oc in outcomes:
                r["OUTC"].append(row("OUTC", primaryid=primaryid, caseid=caseid, outc_cod=oc))
            for _ in range(rng.choice([1, 1, 2])):
                r["RPSR"].append(row(
                    "RPSR", primaryid=primaryid, caseid=caseid,
                    rpsr_cod=rng.choice(["HP", "CN", "OT", "PH"])))
        latest[caseid] = n_versions

    for q in QUARTERS:
        for table, cols in COLS.items():
            lines = [DELIM.join(cols)] + rows[q][table]
            (outdir / f"{table}{q}.txt").write_text("\n".join(lines) + "\n", encoding="latin-1")

    deleted = sorted(rng.sample(sorted(latest), 3))
    (outdir / "DELETE26Q1.txt").write_text("\n".join(str(c) for c in deleted) + "\n", encoding="latin-1")
    return {"quarters": QUARTERS, "latest_version": latest, "deleted": deleted, "n_cases": n_cases}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/sample", help="Output folder")
    ap.add_argument("--cases", type=int, default=2000, help="Number of distinct cases")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    meta = generate(args.out, args.cases, args.seed)
    multi = sum(1 for v in meta["latest_version"].values() if v > 1)
    print(f"Wrote synthetic quarters {', '.join(meta['quarters'])} to {args.out}")
    print(f"  {meta['n_cases']:,} cases ({multi:,} with multiple versions), {len(meta['deleted'])} deleted case ids")


if __name__ == "__main__":
    main()

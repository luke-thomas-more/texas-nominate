"""Compare our Texas scores with Shor-McCarty's (state legislator common-space scores, 1993-2022).

Shor & McCarty give each legislator one score (np_score), built from roll calls plus a national
candidate survey. We compare it with each member's average first-dimension score, overall and
within each party. Their data (Harvard Dataverse doi:10.7910/DVN/SGOQ7G, CC BY-NC-SA 4.0) is used
only to compute these correlations; their scores are not republished.

Writes data/processed/validation_texas.json.
"""
import csv, json, os, re, unicodedata
import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


def main():
    sm = pd.read_stata(os.path.join(ROOT, "data", "raw", "validation", "shor_mccarty_2025.tab"))
    sm = sm[sm["st"] == "TX"].copy()
    year_cols = [c for c in sm.columns if re.match(r"(house|senate)\d{4}$", c)]

    people = {int(r["member_id"]): r for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    scores = {}
    for r in csv.DictReader(open(os.path.join(PROC, "fit", "member_scores.csv"))):
        scores.setdefault(int(r["member_id"]), []).append((int(r["leg"]), r["chamber"], float(r["dim1"]), r["party"]))
    # in the modern era legislature n covers years 1847 + 2n and the next (74th = 1995-96, 89th = 2025-26)
    ours = {}
    for mid, rows in scores.items():
        years = {(ch, 1847 + 2 * leg + k) for leg, ch, _, _ in rows for k in (0, 1)}
        last = norm(people[mid]["name"]).split()
        ours[mid] = {"last": last, "first": last[0][0] if last else "", "years": years,
                     "dim1": float(np.mean([d for _, _, d, _ in rows])), "party": rows[-1][3]}

    pairs = []
    for _, row in sm.iterrows():
        if pd.isna(row["np_score"]):
            continue
        last, _, first = str(row["name"]).partition(",")
        last, first = norm(last), norm(first)
        yrs = {("H" if c.startswith("house") else "S", int(c[-4:])) for c in year_cols if not pd.isna(row[c]) and row[c]}
        cands = [mid for mid, o in ours.items()
                 if " ".join(o["last"]).endswith(last) and (not first or o["first"] == first[0]) and (o["years"] & yrs)]
        if len(cands) == 1:
            pairs.append((ours[cands[0]]["dim1"], float(row["np_score"]), row["party"]))
    a = np.array([(x, y) for x, y, _ in pairs])
    out = {"matched_legislators": len(pairs), "corr_overall": float(np.corrcoef(a[:, 0], a[:, 1])[0, 1])}
    for p in ("D", "R"):
        b = np.array([(x, y) for x, y, q in pairs if q == p])
        out[f"corr_within_{p}"] = float(np.corrcoef(b[:, 0], b[:, 1])[0, 1])
        out[f"n_{p}"] = len(b)
    json.dump(out, open(os.path.join(PROC, "validation_texas.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

"""Why does the map agree less with W-NOMINATE on its first axis before 1961? Four measurements per session.

validate_wnominate.py turns each session's W-NOMINATE map to line up with ours and correlates axis by axis, by OUR
axes. That mixes two things: how well the session's own main divide is recovered, and which of our axes it lies
along. Here each W-NOMINATE dimension is taken as W-NOMINATE orders them (first = the session's main divide) and
predicted from both of our axes at once (multiple correlation R: no rotation or stretch to choose).

  1. R of W-NOMINATE's first and second dimension given our shared map, and where its first dimension points on our
     map (nearer our axis 1 or axis 2).
  2. The ceiling: W-NOMINATE against itself. Each session's roll calls are split into two interleaved halves
     (wnominate_halves.py), each half scaled alone; R of one half's dimension given the other half's map, and the
     same stepped up to the full session's number of votes (Spearman-Brown).
  3. A third dimension: the same R given the shared map fit in three dimensions (FIT_DIR + "_k3").
  4. The estimator alone: our own code fit to one legislature at a time (fit_texas.py with first = last
     legislature, in data/processed/fit_single/<leg>/), against W-NOMINATE on the same votes.

Writes data/processed/wnominate_trace.json and prints medians by era.
Usage: FIT_DIR=data/processed/fit_v6 python pipeline/wnominate_trace.py
"""
import collections, csv, json, os

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
ERAS = [(1, 13, "1846-73"), (14, 26, "1874-1900"), (27, 41, "1901-30"), (42, 56, "1931-60"), (57, 62, "1961-72"),
        (63, 67, "1973-82"), (68, 73, "1983-94"), (74, 89, "1995-2025")]


def scores(path):
    out = collections.defaultdict(dict)
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            out[(int(r["leg"]), r["chamber"])][r["member_id"]] = tuple(float(r[k]) for k in r if k.startswith("dim"))
    return out


def coords(path):
    if not os.path.exists(path):
        return {}
    return {r["member_id"]: (float(r["coord1D"]), float(r["coord2D"])) for r in csv.DictReader(open(path))
            if r["coord1D"] not in ("", "NA") and r["coord2D"] not in ("", "NA")}


def multiple_r(y, X):
    """Correlation of y with its best linear prediction from the columns of X, and that prediction's slopes."""
    A = np.c_[np.ones(len(X)), X]
    b = np.linalg.lstsq(A, y, rcond=None)[0]
    return float(np.corrcoef(A @ b, y)[0, 1]), b[1:]


def given(target, basis, min_n=15):
    """R of each of target's two dimensions given basis's map, over the members both have (None if too few)."""
    ids = sorted(set(target) & set(basis))
    if len(ids) < min_n:
        return None
    T, B = np.array([target[i] for i in ids]), np.array([basis[i] for i in ids])
    (r1, b1), (r2, _) = multiple_r(T[:, 0], B), multiple_r(T[:, 1], B)      # (angle: read on the basis's first two axes)
    return {"n": len(ids), "r1": r1, "r2": r2, "angle": float(np.degrees(np.arctan2(b1[1], b1[0])) % 180)}


def main():
    ours = scores(os.path.join(FIT, "member_scores.csv"))
    ours3 = scores(os.path.join(FIT + "_k3", "member_scores.csv"))
    res = {}
    for line in open(os.path.join(PROC, "wnominate", "jobs.txt")):
        name = line.split()[0]
        leg, ch = int(name[:-1]), name[-1]
        w = coords(os.path.join(PROC, "wnominate", name + ".coords.csv"))
        if not w:
            continue
        row = {"shared": given(w, ours[(leg, ch)])}
        if ours3:
            row["shared3"] = given(w, ours3[(leg, ch)])
        a, b = (coords(os.path.join(PROC, "wnominate_halves", f"{name}_{h}.coords.csv")) for h in "ab")
        ab, ba = given(a, b), given(b, a)
        if ab and ba:
            half = {k: (ab[k] + ba[k]) / 2 for k in ("r1", "r2")}
            row["halves"] = dict(half, n=ab["n"], full1=2 * half["r1"] / (1 + half["r1"]), full2=2 * half["r2"] / (1 + half["r2"]))
        single = scores(os.path.join(PROC, "fit_single", str(leg), "member_scores.csv"))
        if single:
            row["single"] = given(w, single[(leg, ch)])
            row["single_vs_shared"] = given(single[(leg, ch)], ours[(leg, ch)])
        res[name] = row
    era = lambda leg: next(e for a, b, e in ERAS if a <= leg <= b)
    by_era = collections.defaultdict(list)
    for name, row in res.items():
        by_era[era(int(name[:-1]))].append(row)
    med = lambda rows, part, k: (lambda v: round(float(np.median(v)), 3) if v else None)([r[part][k] for r in rows if r.get(part)])
    summary = {}
    for e, rows in by_era.items():
        ang = [r["shared"]["angle"] for r in rows if r.get("shared")]
        summary[e] = {
            "sessions": len(rows),
            "w1_given_map": med(rows, "shared", "r1"), "w2_given_map": med(rows, "shared", "r2"),
            "w1_nearer_axis2": round(float(np.mean([45 < a < 135 for a in ang])), 2),
            "w1_halves": med(rows, "halves", "r1"), "w2_halves": med(rows, "halves", "r2"),
            "w1_halves_full": med(rows, "halves", "full1"), "w2_halves_full": med(rows, "halves", "full2"),
            "w1_given_map3": med(rows, "shared3", "r1"), "w2_given_map3": med(rows, "shared3", "r2"),
            "w1_given_single": med(rows, "single", "r1"), "w2_given_single": med(rows, "single", "r2"),
            "single1_given_map": med(rows, "single_vs_shared", "r1"), "single2_given_map": med(rows, "single_vs_shared", "r2"),
        }
    json.dump({"fit": os.path.basename(FIT), "by_era": summary, "by_session": res},
              open(os.path.join(PROC, "wnominate_trace.json"), "w"), indent=1)
    f = lambda v: "  -  " if v is None else f"{v:5.2f}"
    print("median multiple R by era. W1, W2: W-NOMINATE's first and second dimension of each session")
    print("era        n   W1|map W2|map  W1 on axis 2   W1,W2|3-D map   W1,W2 half|half   stepped up    W1,W2|our one-session fit   one-session|map")
    for e, s in summary.items():
        print(f"{e:10s}{s['sessions']:3d}   {f(s['w1_given_map'])}  {f(s['w2_given_map'])}     {s['w1_nearer_axis2']:4.0%}        "
              f"{f(s['w1_given_map3'])} {f(s['w2_given_map3'])}     "
              f"{f(s['w1_halves'])} {f(s['w2_halves'])}     {f(s['w1_halves_full'])} {f(s['w2_halves_full'])}       "
              f"{f(s['w1_given_single'])} {f(s['w2_given_single'])}            {f(s['single1_given_map'])} {f(s['single2_given_map'])}")


if __name__ == "__main__":
    main()

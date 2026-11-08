"""Check the shared map against W-NOMINATE (Poole, Lewis, Lo & Carroll's R package), fit separately to each
legislature and chamber.

W-NOMINATE is the static, one-session form of NOMINATE, estimated by its authors' own code, so agreement checks our
estimator and our choice of one shared space at once. For each legislature and chamber with at least 20 members,
the contested roll calls (2.5% or more on the losing side) and members with 20 or more of those votes are written to
data/processed/wnominate/<leg><chamber>.csv and scaled in 2 dimensions by pipeline/wnominate.R; its coordinates are
then compared with ours: the correlation on each axis after the best rotation (orthogonal Procrustes), since each
W-NOMINATE fit sets its own orientation. Writes data/processed/validation_wnominate.json.

Needs R with the wnominate package (see README: micromamba env "rstats", install.packages("wnominate")).
Usage: FIT_DIR=data/processed/fit_v5 python pipeline/validate_wnominate.py
"""
import collections, csv, gzip, io, json, os, subprocess, sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
OUT = os.path.join(PROC, "wnominate")
RSCRIPT = os.environ.get("RSCRIPT", os.path.expanduser("~/.local/micromamba/envs/rstats/bin/Rscript"))
GOOD = {"ok", "near", "ok_digit", "ok_lists", "ok_rest"}


def main():
    os.makedirs(OUT, exist_ok=True)
    rcs = {}
    for f in ("rollcalls.csv", "rollcalls_hist.csv"):
        for r in csv.DictReader(open(os.path.join(PROC, f))):
            if r["status"] in GOOD:
                rcs[r["rc_id"]] = (int(r["leg"]), r["chamber"])
    votes = collections.defaultdict(lambda: collections.defaultdict(dict))      # (leg, ch) -> rc -> member -> code
    for f in ("votes.csv.gz", "votes_hist.csv.gz"):
        with gzip.open(os.path.join(PROC, f), "rt") as fh:
            rd = csv.reader(fh); next(rd)
            for rc, m, v in rd:
                if rc in rcs and v in "YN":
                    votes[rcs[rc]][rc][m] = 1 if v == "Y" else 6
    ours = collections.defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(FIT, "member_scores.csv"))):
        ours[(int(r["leg"]), r["chamber"])][r["member_id"]] = (float(r["dim1"]), float(r["dim2"]))
    done = []
    for key, by_rc in sorted(votes.items()):
        keep = [rc for rc, vs in by_rc.items() if len(vs) >= 10 and
                min(sum(v == 1 for v in vs.values()), sum(v == 6 for v in vs.values())) >= 0.025 * len(vs)]
        n = collections.Counter(m for rc in keep for m in by_rc[rc])
        mems = sorted(m for m, c in n.items() if c >= 20 and m in ours[key])
        if len(mems) < 20 or len(keep) < 20:
            continue
        name = f"{key[0]}{key[1]}"
        # polarity: the members furthest out on each of our axes
        p1 = max(mems, key=lambda m: ours[key][m][0]); p2 = max(mems, key=lambda m: ours[key][m][1])
        buf = io.StringIO(newline="")
        w = csv.writer(buf)
        w.writerow(["member_id"] + keep)
        for m in mems:
            w.writerow([m] + [by_rc[rc].get(m, 9) for rc in keep])
        # wnominate.R skips a session whose coordinates exist, so they're kept only while its vote matrix is unchanged
        path = os.path.join(OUT, name + ".csv")
        try:
            same = open(path, newline="").read() == buf.getvalue()
        except OSError:
            same = False
        if not same:
            open(path, "w", newline="").write(buf.getvalue())
        coords = os.path.join(OUT, name + ".coords.csv")
        if os.path.exists(coords) and os.path.getmtime(coords) < os.path.getmtime(path):
            os.remove(coords)
        done.append((name, p1, p2))
    with open(os.path.join(OUT, "jobs.txt"), "w") as f:
        f.writelines(f"{a} {b} {c}\n" for a, b, c in done)
    env = dict(os.environ, PATH=os.path.dirname(RSCRIPT) + os.pathsep + os.environ.get("PATH", ""))
    subprocess.run([RSCRIPT, os.path.join(ROOT, "pipeline", "wnominate.R"), OUT], check=True, env=env)
    res = {}
    for name, _, _ in done:
        path = os.path.join(OUT, name + ".coords.csv")
        if not os.path.exists(path):
            continue
        key = (int(name[:-1]), name[-1])
        rows = [r for r in csv.DictReader(open(path)) if r["coord1D"] not in ("", "NA")]
        X = np.array([ours[key][r["member_id"]] for r in rows])
        Y = np.array([(float(r["coord1D"]), float(r["coord2D"])) for r in rows])
        U, _, Vt = np.linalg.svd((Y - Y.mean(0)).T @ (X - X.mean(0)))
        Yr = (Y - Y.mean(0)) @ (U @ Vt)
        res[name] = {"members": len(rows), "r_dim1": float(np.corrcoef(Yr[:, 0], X[:, 0])[0, 1]),
                     "r_dim2": float(np.corrcoef(Yr[:, 1], X[:, 1])[0, 1]),
                     "r_dim1_raw": float(np.corrcoef(Y[:, 0], X[:, 0])[0, 1])}
    era = lambda leg: next(e for a, b, e in [(1, 13, "1846-73"), (14, 26, "1874-1900"), (27, 41, "1901-30"), (42, 56, "1931-60"),
                                             (57, 62, "1961-72"), (63, 67, "1973-82"), (68, 73, "1983-94"), (74, 89, "1995-2025")] if a <= leg <= b)
    by_era = collections.defaultdict(list)
    for name, v in res.items():
        by_era[era(int(name[:-1]))].append(v)
    summary = {e: {"sessions": len(v), "median_r_dim1": float(np.median([x["r_dim1"] for x in v])),
                   "median_r_dim2": float(np.median([x["r_dim2"] for x in v]))} for e, v in by_era.items()}
    json.dump({"by_legislature": res, "by_era": summary}, open(os.path.join(PROC, "validation_wnominate.json"), "w"), indent=1)
    for e, v in summary.items():
        print(f"{e:10s} {v['sessions']:3d} sessions  median r dim1 {v['median_r_dim1']:.3f}  dim2 {v['median_r_dim2']:.3f}")


if __name__ == "__main__":
    main()

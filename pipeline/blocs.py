"""Voting blocs: groups of members who vote together, found from the votes themselves, legislature by legislature.

For one legislature and chamber, two members' agreement is the share of the contested roll calls they both
voted on (yea or nay; at least 20) where they voted alike. Members are clustered on that agreement (average
linkage), and the number of blocs, 2 or 3, is the one whose blocs are best separated (mean silhouette).
Members placed by fewer than 20 shared votes with most others stay out, and a bloc must hold at least a
tenth of the members (a handful of outliers isn't a bloc). Each bloc gets its size, its mean position
on the map, its cohesion (mean agreement within it) and its core (the members who agree most with the rest of
it); each pair of blocs, how often their majorities voted opposite ways. This is most telling where the map
has one party (1876 to the 1970s), when the Legislature's divisions ran inside the Democratic Party.

Writes docs/data/blocs.json. Usage: FIT_DIR=data/processed/fit_v4 python pipeline/blocs.py
"""
import collections, csv, gzip, json, os
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
DOCS = os.environ.get("DOCS_DIR", os.path.join(ROOT, "docs", "data"))
GOOD = {"ok", "near", "ok_digit", "ok_lists", "ok_rest"}
MIN_SHARED, MIN_BLOC, MIN_RC = 20, 0.10, 30      # a bloc: at least a tenth of the members compared
# checks against what is known, shown on the Trends page: the 1971 House's "Dirty Thirty", the 30 members (Library
# ids) who voted against Speaker Gus Mutscher's ruling in the Sharpstown scandal (as listed in Wikipedia's article
# on them), and how the 1911 House's blocs voted on a wet motion (Terrell of Bexar's, to table sending a bill to
# the Committee on Liquor Traffic)
DIRTY_THIRTY = [790, 622, 765, 540, 774, 592, 745, 559, 739, 652, 349, 717, 720, 721, 535, 725, 614, 109, 734, 602, 573, 740,
                631, 575, 749, 214, 771, 662, 42, 489]
WET_VOTE = ("32", "H", "1911-02-28", "Liquor Traffic")


def silhouette(D, lab):
    """Mean silhouette of a clustering, from a distance matrix."""
    s = []
    for i in range(len(lab)):
        same = lab == lab[i]
        if same.sum() <= 1:
            s.append(0.0); continue
        a = D[i, same].sum() / (same.sum() - 1)
        b = min(D[i, lab == c].mean() for c in set(lab) if c != lab[i])
        s.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(s))


def session_blocs(mids, V, pos):
    """mids: member ids; V: members x roll calls, +1 yea / -1 nay / 0 not voting; pos: {member: (x, y)}."""
    A = np.abs(V)
    both = A @ A.T
    agree = (V @ V.T + both) / 2
    keep = np.where((both >= MIN_SHARED).sum(axis=1) >= 0.8 * len(mids))[0]       # members comparable with most others
    if len(keep) < 20:
        return None
    both, agree = both[np.ix_(keep, keep)], agree[np.ix_(keep, keep)]
    with np.errstate(invalid="ignore", divide="ignore"):
        S = np.where(both >= MIN_SHARED, agree / np.maximum(both, 1), np.nan)
    S = np.where(np.isnan(S), np.nanmean(S), S)                                     # the few incomparable pairs: average
    np.fill_diagonal(S, 1.0)
    D = 1 - S
    Z = linkage(squareform(D, checks=False), "average")
    best = None
    for k in range(2, 4):                          # at most three: the map has three bloc colors
        lab = fcluster(Z, k, "maxclust")
        sizes = collections.Counter(lab)
        if min(sizes.values()) < max(5, MIN_BLOC * len(keep)) or len(sizes) < k:
            continue
        sc = silhouette(D, lab)
        if best is None or sc > best[0]:
            best = (sc, k, lab)
    if best is None:
        return None
    sc, k, lab = best
    ids = [mids[i] for i in keep]
    blocs = []
    for c in sorted(set(lab)):
        idx = np.where(lab == c)[0]
        sub = S[np.ix_(idx, idx)]
        typical = (sub.sum(axis=1) - 1) / max(len(idx) - 1, 1)
        xs = [pos[ids[i]] for i in idx if ids[i] in pos]
        blocs.append({"members": [ids[i] for i in idx], "core": [ids[i] for i in idx[np.argsort(-typical)[:5]]],
                      "cohesion": round(float(typical.mean()), 3),
                      "x": round(float(np.mean([p[0] for p in xs])), 3) if xs else None,
                      "y": round(float(np.mean([p[1] for p in xs])), 3) if xs else None})
    blocs.sort(key=lambda b: (b["x"] if b["x"] is not None else 0))
    # how often each pair of blocs' majorities split
    rows = {ids[i]: i for i in range(len(ids))}
    Vk = V[keep]
    opp = {}
    for a in range(len(blocs)):
        for b in range(a + 1, len(blocs)):
            ia = [rows[m] for m in blocs[a]["members"]]; ib = [rows[m] for m in blocs[b]["members"]]
            ma, mb = np.sign(Vk[ia].sum(axis=0)), np.sign(Vk[ib].sum(axis=0))
            ok = (ma != 0) & (mb != 0)
            opp[f"{a}-{b}"] = round(float(((ma != mb) & ok).sum() / max(ok.sum(), 1)), 3)
    return {"k": k, "silhouette": round(sc, 3), "blocs": blocs, "opposed": opp}


def main():
    pos, party = {}, {}
    for r in csv.DictReader(open(os.path.join(FIT, "member_scores.csv"))):
        key = (int(r["leg"]), r["chamber"])
        pos.setdefault(key, {})[int(r["member_id"])] = (float(r["dim1"]), float(r["dim2"]))
    rcs = collections.defaultdict(list)
    wet = None
    for name in ("rollcalls.csv", "rollcalls_hist.csv"):
        path = os.path.join(PROC, name)
        if os.path.exists(path):
            for r in csv.DictReader(open(path)):
                y, n = int(r["n_y"]), int(r["n_n"])
                if r["status"] in GOOD and y + n > 0 and min(y, n) / (y + n) >= 0.025:
                    rcs[(int(r["leg"]), r["chamber"])].append(r["rc_id"])
                    if (r["leg"], r["chamber"], r["date"]) == WET_VOTE[:3] and WET_VOTE[3] in r["motion"]:
                        wet = r["rc_id"]
    want = {rc: key for key, v in rcs.items() for rc in v}
    votes = collections.defaultdict(dict)
    for name in ("votes.csv.gz", "votes_hist.csv.gz"):
        path = os.path.join(PROC, name)
        if os.path.exists(path):
            with gzip.open(path, "rt") as f:
                rd = csv.reader(f); next(rd)
                for rc, m, v in rd:
                    if rc in want and v in ("Y", "N"):
                        votes[rc][int(m)] = 1 if v == "Y" else -1
    out = {}
    for key in sorted(rcs):
        if len(rcs[key]) < MIN_RC or key not in pos:
            continue
        mids = sorted(pos[key])
        ix = {m: i for i, m in enumerate(mids)}
        V = np.zeros((len(mids), len(rcs[key])), dtype=np.float32)
        for j, rc in enumerate(rcs[key]):
            for m, c in votes[rc].items():
                if m in ix:
                    V[ix[m], j] = c
        res = session_blocs(mids, V, pos[key])
        if res:
            out[f"{key[0]}{key[1]}"] = res
    checks = []
    if "62H" in out:
        b = out["62H"]
        best = max(range(len(b["blocs"])), key=lambda i: len(set(b["blocs"][i]["members"]) & set(DIRTY_THIRTY)))
        checks.append({"key": "62H", "kind": "dirty_thirty", "n": len(DIRTY_THIRTY), "bloc": best,
                       "found": len(set(b["blocs"][best]["members"]) & set(DIRTY_THIRTY)), "bloc_size": len(b["blocs"][best]["members"])})
    if wet and "32H" in out:
        b = out["32H"]
        tallies = [{c: sum(1 for m in bl["members"] if votes[wet].get(m) == (1 if c == "Y" else -1)) for c in "YN"} for bl in b["blocs"]]
        checks.append({"key": "32H", "kind": "wet_vote", "rc_id": wet, "tallies": tallies})
    out = {"sessions": out, "checks": checks}
    json.dump(out, open(os.path.join(DOCS, "blocs.json"), "w"), separators=(",", ":"))
    print(f"blocs: {len(out['sessions'])} legislature-chambers; k = {collections.Counter(v['k'] for v in out['sessions'].values())}; checks {checks}")


if __name__ == "__main__":
    main()

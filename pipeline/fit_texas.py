"""Fit DW-NOMINATE to the Texas House and Senate together, one shared space.

Each person is one legislator across both chambers, so members who moved from the House to the
Senate tie the two chambers' scales together. The time unit is the legislature (two years; called
sessions belong to their legislature). Filters follow Voteview: roll calls whose minority is under
2.5% of those voting are dropped, and so are members with fewer than 20 scaled votes.

Fits 1, 2 and 3 dimensions (to see what each extra dimension buys) and keeps the requested one.
Writes data/processed/fit/: summary.json, member_scores.csv, rollcall_params.csv.

Usage: python pipeline/fit_texas.py [dims=2] [first_leg] [last_leg]
"""
import collections, csv, gzip, json, os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nominate import DWNominate, svd_start

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
OUT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))


def load(first, last):
    rcs = {r["rc_id"]: r for r in csv.DictReader(open(os.path.join(PROC, "rollcalls.csv")))
           if r["status"] == "ok" and first <= int(r["leg"]) <= last}
    obs = []
    with gzip.open(os.path.join(PROC, "votes.csv.gz"), "rt") as f:
        for rc_id, mid, v in csv.reader(f):
            if v in ("Y", "N") and rc_id in rcs:
                obs.append((rc_id, int(mid), v == "Y"))
    return rcs, obs


def build(rcs, obs, min_frac=0.025, min_votes=20):
    rc_ids = np.array([o[0] for o in obs]); mids = np.array([o[1] for o in obs]); yea = np.array([o[2] for o in obs])
    keys, rc = np.unique(rc_ids, return_inverse=True)
    yeas = np.bincount(rc, weights=yea); tot = np.bincount(rc)
    sel = (np.minimum(yeas, tot - yeas) / tot >= min_frac)[rc]
    for _ in range(5):                         # dropping members can make roll calls lopsided and vice versa
        m_keys, m = np.unique(mids[sel], return_inverse=True)
        bad_m = np.bincount(m) < min_votes
        idx = np.flatnonzero(sel)
        sel[idx[bad_m[m]]] = False
        k2, r2 = np.unique(rc_ids[sel], return_inverse=True)
        y2 = np.bincount(r2, weights=yea[sel]); t2 = np.bincount(r2)
        bad_r = np.minimum(y2, t2 - y2) / t2 < min_frac
        idx = np.flatnonzero(sel)
        sel[idx[bad_r[r2]]] = False
        if not bad_m.any() and not bad_r.any():
            break
    rc_ids, mids, yea = rc_ids[sel], mids[sel], yea[sel]
    rc_keys, rc = np.unique(rc_ids, return_inverse=True)
    mem_keys, mem = np.unique(mids, return_inverse=True)
    leg = np.array([int(rcs[k]["leg"]) for k in rc_keys])[rc]
    first = np.full(len(mem_keys), 10 ** 6); last = np.zeros(len(mem_keys), int)
    np.minimum.at(first, mem, leg); np.maximum.at(last, mem, leg)
    frac = np.where(last[mem] > first[mem], (leg - first[mem]) / np.maximum(last[mem] - first[mem], 1), 0.0)
    return dict(rc=rc, mem=mem, yea=yea, frac=frac, rc_keys=rc_keys, mem_keys=mem_keys, first=first, last=last, leg=leg)


def party_lookup():
    party = {}
    for t in csv.DictReader(open(os.path.join(PROC, "terms.csv"))):
        party[(int(t["member_id"]), int(t["leg"]), t["chamber"])] = t
    return party


def main():
    dims = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    first_leg, last_leg = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (74, 89)
    os.makedirs(OUT, exist_ok=True)
    rcs, obs = load(first_leg, last_leg)
    d = build(rcs, obs)
    n_rc, n_mem = len(d["rc_keys"]), len(d["mem_keys"])
    chambers = collections.defaultdict(set)
    for k, m in zip(d["rc_keys"][d["rc"]], d["mem_keys"][d["mem"]]):
        pass
    rc_chamber = np.array([rcs[k]["chamber"] for k in d["rc_keys"]])
    both = len(set(d["mem"][rc_chamber[d["rc"]] == "H"]) & set(d["mem"][rc_chamber[d["rc"]] == "S"]))
    print(f"legislatures {first_leg}-{last_leg}: {n_rc:,} scaled roll calls of {len(rcs):,}, {n_mem:,} members "
          f"({both} served in both chambers), {len(d['rc']):,} votes", flush=True)

    terms = party_lookup()
    rep = np.array([any("Republican" in terms.get((int(mid), l, c), {}).get("party", "")
                        for l in range(first_leg, last_leg + 1) for c in "HS") for mid in d["mem_keys"]])

    summary = {"legislatures": [first_leg, last_leg], "roll_calls_ok": len(rcs), "roll_calls_scaled": n_rc,
               "members": n_mem, "members_both_chambers": both, "votes": int(len(d["rc"])), "by_dims": {}}
    X0 = svd_start(d["rc"], d["mem"], d["yea"], n_rc, n_mem, 3)
    models = {}
    for K in ([dims] if os.environ.get("FIT_ONLY") else sorted({1, 2, 3, dims})):
        t0 = time.time()
        print(f"--- {K} dimension(s)", flush=True)
        m = DWNominate(d["rc"], d["mem"], d["yea"], d["frac"], n_rc, n_mem, K=K, trend=os.environ.get("TREND", "1") == "1")
        m.start(X0[:, :K]).fit(verbose=True)
        st = m.fit_stats()
        st["seconds"] = round(time.time() - t0, 1)
        summary["by_dims"][K] = st
        models[K] = m
        print(f"    K={K}: correct {st['correct']:.4f}  APRE {st['apre']:.4f}  GMP {st['gmp']:.4f}  ({st['seconds']}s)", flush=True)

    m = models[dims]
    # polarity: Republicans on the positive side of dimension 1
    if m.Pm[rep, 0].mean() < m.Pm[~rep, 0].mean():
        for arr in (m.Pm, m.Pp, m.M, m.D):
            arr[:, 0] *= -1
    summary["chosen_dims"] = dims
    summary["beta"], summary["w"] = m.beta, [float(x) for x in np.sqrt(m.w2)]

    # member scores per legislature and chamber actually served
    served = collections.defaultdict(set)
    nvotes = collections.Counter()
    for r, i in zip(d["rc"], d["mem"]):
        k = d["rc_keys"][r]
        served[i].add((int(rcs[k]["leg"]), rcs[k]["chamber"]))
        nvotes[(i, int(rcs[k]["leg"]), rcs[k]["chamber"])] += 1
    with open(os.path.join(OUT, "member_scores.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["member_id", "leg", "chamber", "party", "district", "scaled_votes"] + [f"dim{k + 1}" for k in range(dims)])
        for i, mid in enumerate(d["mem_keys"]):
            for leg, ch in sorted(served[i]):
                fr = 0.0 if d["last"][i] == d["first"][i] else (leg - d["first"][i]) / (d["last"][i] - d["first"][i])
                x = m.position(np.array([i]), np.array([fr]))[0]
                t = terms.get((int(mid), leg, ch), {})
                w.writerow([int(mid), leg, ch, t.get("party", ""), t.get("district", ""), nvotes[(i, leg, ch)]] + [f"{v:.4f}" for v in x])
    # roll call parameters, plus how well each is classified
    from nominate_kernels import predict
    z, _ = predict(*m._state(), *m._data(), m.T)
    correct = (z > 0) == (m.s > 0)
    errors = np.bincount(d["rc"], weights=~correct, minlength=n_rc)
    yeas = np.bincount(d["rc"], weights=d["yea"], minlength=n_rc); tot = np.bincount(d["rc"], minlength=n_rc)
    minority = np.minimum(yeas, tot - yeas)
    with open(os.path.join(OUT, "rollcall_params.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rc_id"] + [f"mid{k + 1}" for k in range(dims)] + [f"spread{k + 1}" for k in range(dims)] + ["errors", "minority", "pre"])
        for j, k in enumerate(d["rc_keys"]):
            pre = (minority[j] - errors[j]) / minority[j] if minority[j] else 0
            w.writerow([k] + [f"{v:.4f}" for v in m.M[j]] + [f"{v:.4f}" for v in m.D[j]] + [int(errors[j]), int(minority[j]), f"{pre:.3f}"])
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "by_dims"}, indent=1))


if __name__ == "__main__":
    main()

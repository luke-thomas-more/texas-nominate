"""Fit DW-NOMINATE to the Texas House and Senate together, one shared space.

Each person is one legislator across both chambers, so members who moved from the House to the
Senate tie the two chambers' scales together. The time unit is the legislature (two years; called
sessions belong to their legislature). Filters follow Voteview: roll calls whose minority is under
2.5% of those voting are dropped, and so are members with fewer than 20 scaled votes.

Fits 1, 2 and 3 dimensions (to see what each extra dimension buys) and keeps the requested one.
Writes data/processed/fit/: summary.json, member_scores.csv, rollcall_params.csv.

Usage: python pipeline/fit_texas.py [dims=2] [first_leg] [last_leg]

HYBRID="1-8,27-41,42-56" fits the hybrid model instead (dims must be 2): dimension 1 is shared by
all of history, and each listed range of legislatures gets a second axis of its own; every other
legislature shares one second axis. Outputs keep the dim1/dim2 columns, with dim2 read on the
axis named in the axis2 column.

START_STITCH=<prefix> starts from the era-only fits <prefix><a>_<b>/ put in one frame
(stitch_start.py) instead of one SVD over every legislature.
"""
import collections, csv, gzip, json, os, sys, time, zlib
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nominate import DWNominate, svd_start, to_ball

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
OUT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))


GOOD = {"ok", "near", "ok_digit", "ok_lists"}     # verified roll calls (see extract_history.py for the statuses)


def load(first, last):
    """Verified roll calls and their yea/nay votes from both pipelines: the journals in the modern
    record-vote format (rollcalls.csv) and the older scanned journals (rollcalls_hist.csv)."""
    rcs, obs = {}, []
    for rc_file, v_file in (("rollcalls.csv", "votes.csv.gz"), ("rollcalls_hist.csv", "votes_hist.csv.gz")):
        if not os.path.exists(os.path.join(PROC, rc_file)):
            continue
        part = {r["rc_id"]: r for r in csv.DictReader(open(os.path.join(PROC, rc_file)))
                if r["status"] in GOOD and first <= int(r["leg"]) <= last}
        rcs.update(part)
        with gzip.open(os.path.join(PROC, v_file), "rt") as f:
            rd = csv.reader(f); next(rd)
            for rc_id, mid, v in rd:
                if v in ("Y", "N") and rc_id in part:
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


def hold_out(d, frac):
    """Set aside a share of votes for out-of-sample scoring. A vote is held out by a hash of its
    (roll call, member), so every fit over the same votes holds out the same ones. Held-out votes
    whose roll call or member has nothing left to fit are dropped."""
    cut = int(frac * 2 ** 32)
    h = np.array([zlib.crc32(f"{d['rc_keys'][r]}|{d['mem_keys'][i]}".encode()) < cut for r, i in zip(d["rc"], d["mem"])])
    held = {k: d[k][h] for k in ("rc", "mem", "yea", "frac")}
    for k in ("rc", "mem", "yea", "frac", "leg"):
        d[k] = d[k][~h]
    ok = np.isin(held["rc"], d["rc"]) & np.isin(held["mem"], d["mem"])
    return {k: v[ok] for k, v in held.items()}


def hybrid_axes(d, spec):
    """Axis names (dimension 2 onward) and the roll call x dimension mask G for HYBRID=spec."""
    eras = [tuple(int(x) for x in part.split("-")) for part in spec.split(",")]
    rc_leg = np.zeros(len(d["rc_keys"]), int)
    rc_leg[d["rc"]] = d["leg"]
    axis = np.zeros(len(rc_leg), int)                  # 0 = the shared second axis
    for g, (a, b) in enumerate(eras, 1):
        axis[(rc_leg >= a) & (rc_leg <= b)] = g
    G = np.zeros((len(rc_leg), 2 + len(eras)))
    G[:, 0] = 1
    G[np.arange(len(rc_leg)), 1 + axis] = 1
    names = ["shared"] + [f"legs {a}-{b}" for a, b in eras]
    return names, G, axis


def warm_start(m, d, folder, K):
    """Start from an earlier fit's positions, outcomes, beta and weights (constant model). The earlier
    fit may have fewer or more dimensions: shared dimensions are copied, an extra one starts small."""
    with open(os.path.join(folder, "member_scores.csv")) as f:
        Kf = sum(1 for c in next(csv.reader(f)) if c.startswith("dim"))
    k0 = min(K, Kf)
    if K > Kf:                               # the SVD start's extra dimensions, shrunk
        m.Pm[:, k0:] *= 0.1; m.M[:, k0:] *= 0.1
    pos = {}
    for r in csv.DictReader(open(os.path.join(folder, "member_scores.csv"))):
        pos[int(r["member_id"])] = [float(r[f"dim{k + 1}"]) for k in range(k0)]
    for i, mid in enumerate(d["mem_keys"]):
        if int(mid) in pos:
            m.Pm[i, :k0] = pos[int(mid)]
    m.Pm[:] = to_ball(m.Pm); m.Pp[:] = m.Pm
    rcp = {r["rc_id"]: r for r in csv.DictReader(open(os.path.join(folder, "rollcall_params.csv")))}
    for j, k in enumerate(d["rc_keys"]):
        if k in rcp:
            m.M[j, :k0] = [float(rcp[k][f"mid{q + 1}"]) for q in range(k0)]
            m.D[j, :k0] = [float(rcp[k][f"spread{q + 1}"]) for q in range(k0)]
    summ = json.load(open(os.path.join(folder, "summary.json")))
    m.beta = float(summ["beta"])
    m.w2[:k0] = [w * w for w in summ["w"][:k0]]
    print(f"    warm start from {folder} ({Kf}-d into {K}-d): beta {m.beta:.2f}", flush=True)


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
    held = hold_out(d, float(os.environ["HOLDOUT"])) if os.environ.get("HOLDOUT") else None
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
    if os.environ.get("START_STITCH"):
        # era-only fits put in one frame (stitch_start.py); members no era fit kept get the SVD start
        from stitch_start import stitched_positions
        Xs, found = stitched_positions(d["mem_keys"], os.environ["START_STITCH"])
        scale = np.abs(Xs[found]).max(0)
        X0[found, :2] = Xs[found] / scale
        print(f"    stitched start for {found.sum():,} of {n_mem:,} members", flush=True)
    hyb = os.environ.get("HYBRID")
    if hyb:
        assert dims == 2 and os.environ.get("FIT_ONLY"), "HYBRID fits 2 dimensions only (with FIT_ONLY=1)"
        axis_names, G, rc_axis = hybrid_axes(d, hyb)
        # each axis starts from the second singular vector, for the members who vote on it
        uses = np.zeros((n_mem, G.shape[1]), bool)
        uses[d["mem"], 1 + rc_axis[d["rc"]]] = True
        X0h = np.zeros((n_mem, G.shape[1]))
        X0h[:, 0] = X0[:, 0]
        X0h[:, 1:] = np.where(uses[:, 1:], X0[:, 1:2], 0.0)
        summary["axes2"] = axis_names
    models = {}
    for K in ([dims] if os.environ.get("FIT_ONLY") else sorted({1, 2, 3, dims})):
        t0 = time.time()
        print(f"--- {K} dimension(s)" + (f", hybrid second axes: {', '.join(axis_names)}" if hyb else ""), flush=True)
        if hyb:
            m = DWNominate(d["rc"], d["mem"], d["yea"], d["frac"], n_rc, n_mem, K=G.shape[1],
                           trend=os.environ.get("TREND", "1") == "1", G=G, hybrid=True)
            m.start(X0h)
        else:
            m = DWNominate(d["rc"], d["mem"], d["yea"], d["frac"], n_rc, n_mem, K=K, trend=os.environ.get("TREND", "1") == "1")
            m.start(X0[:, :K])
        if os.environ.get("FIT_START") and not hyb:
            warm_start(m, d, os.environ["FIT_START"], K)
        m.fix_beta = os.environ.get("FIX_BETA") == "1"
        m.fit(verbose=True, alternate_iters=int(os.environ.get("ALT_ITERS", 30)), rounds=int(os.environ.get("ROUNDS", 4)))
        st = m.fit_stats()
        st["seconds"] = round(time.time() - t0, 1)
        summary["by_dims"][K] = st
        models[K] = m
        print(f"    K={K}: correct {st['correct']:.4f}  APRE {st['apre']:.4f}  GMP {st['gmp']:.4f}  ({st['seconds']}s)", flush=True)

    m = models[dims]
    # polarity: Republicans on the positive side of dimension 1. Without a trend Pp is Pm (the same
    # array), so flip it only once or members end up unflipped against flipped roll calls.
    # Same rule for dimension 2 (a reflection, so the fit is unchanged): it puts the modern House
    # leadership's allies at the top, as in v1.
    for k in range(min(dims, 2)):
        if m.Pm[rep, k].mean() < m.Pm[~rep, k].mean():
            for arr in {id(a): a for a in (m.Pm, m.Pp, m.M, m.D)}.values():
                arr[:, k] *= -1
    summary["chosen_dims"] = dims
    summary["beta"], summary["w"] = m.beta, [float(x) for x in np.sqrt(m.w2)]
    if hyb:
        eras = [tuple(int(x) for x in part.split("-")) for part in hyb.split(",")]
        leg_axis = lambda leg: next((g for g, (a, b) in enumerate(eras, 1) if a <= leg <= b), 0)
        pick = lambda row, ax: [row[0], row[1 + ax]]          # (dim 1, the axis's own dim 2)
    else:
        leg_axis = lambda leg: None
        pick = lambda row, ax: row
    ax_name = lambda ax: axis_names[ax] if hyb else None

    # member scores per legislature and chamber actually served
    served = collections.defaultdict(set)
    nvotes = collections.Counter()
    for r, i in zip(d["rc"], d["mem"]):
        k = d["rc_keys"][r]
        served[i].add((int(rcs[k]["leg"]), rcs[k]["chamber"]))
        nvotes[(i, int(rcs[k]["leg"]), rcs[k]["chamber"])] += 1
    with open(os.path.join(OUT, "member_scores.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["member_id", "leg", "chamber", "party", "district", "scaled_votes"] + [f"dim{k + 1}" for k in range(dims)]
                   + (["axis2"] if hyb else []))
        for i, mid in enumerate(d["mem_keys"]):
            for leg, ch in sorted(served[i]):
                fr = 0.0 if d["last"][i] == d["first"][i] else (leg - d["first"][i]) / (d["last"][i] - d["first"][i])
                x = m.position(np.array([i]), np.array([fr]))[0]
                t = terms.get((int(mid), leg, ch), {})
                ax = leg_axis(leg)
                w.writerow([int(mid), leg, ch, t.get("party", ""), t.get("district", ""), nvotes[(i, leg, ch)]]
                           + [f"{v:.4f}" for v in pick(x, ax)] + ([ax_name(ax)] if hyb else []))
    # roll call parameters, plus how well each is classified
    from nominate_kernels import predict
    z, _ = predict(*m._state(), *m._data(), m.T)
    correct = (z > 0) == (m.s > 0)
    errors = np.bincount(d["rc"], weights=~correct, minlength=n_rc)
    yeas = np.bincount(d["rc"], weights=d["yea"], minlength=n_rc); tot = np.bincount(d["rc"], minlength=n_rc)
    minority = np.minimum(yeas, tot - yeas)
    with open(os.path.join(OUT, "rollcall_params.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rc_id"] + [f"mid{k + 1}" for k in range(dims)] + [f"spread{k + 1}" for k in range(dims)] + ["errors", "minority", "pre"]
                   + (["axis2"] if hyb else []))
        for j, k in enumerate(d["rc_keys"]):
            pre = (minority[j] - errors[j]) / minority[j] if minority[j] else 0
            ax = int(rc_axis[j]) if hyb else None
            w.writerow([k] + [f"{v:.4f}" for v in pick(m.M[j], ax)] + [f"{v:.4f}" for v in pick(m.D[j], ax)]
                       + [int(errors[j]), int(minority[j]), f"{pre:.3f}"] + ([ax_name(ax)] if hyb else []))
    if held is not None:
        # held-out votes: the model's utility difference z and the roll call's majority side among fitted votes
        hz, _ = predict(*m._state(), np.ascontiguousarray(held["rc"], np.int64), np.ascontiguousarray(held["mem"], np.int64),
                        np.where(held["yea"], 1.0, -1.0), np.ascontiguousarray(held["frac"], float) if m.trend else np.zeros(len(held["rc"])),
                        m.G, m.T)
        with open(os.path.join(OUT, "holdout_votes.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["rc_id", "member_id", "yea", "z", "majority_yea"])
            for r, i, y, z in zip(held["rc"], held["mem"], held["yea"], hz):
                w.writerow([d["rc_keys"][r], int(d["mem_keys"][i]), int(y), f"{z:.4f}", int(2 * yeas[r] >= tot[r])])
        summary["holdout_votes"] = int(len(hz))
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "by_dims"}, indent=1))


if __name__ == "__main__":
    main()

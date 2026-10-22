"""Check our DW-NOMINATE against Voteview's published scores for the U.S. House.

Fits House roll calls for a run of Congresses with the same filters Voteview uses (drop roll calls
whose minority is under 2.5%, members with fewer than 20 scaled votes) and compares our scores with
Voteview's nominate_dim1/dim2 for the same members. Voteview's scores come from one fit of all
Congresses since 1789, so a fit of a shorter window won't match exactly, but it should be close.

Usage: python pipeline/validate_voteview.py [first_congress] [last_congress]
Writes data/processed/validation_voteview.json.
"""
import csv, io, json, os, sys, time, urllib.request
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nominate import DWNominate, svd_start

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "voteview")
BASE = "https://voteview.com/static/data/out/"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:3128")}))


def load(kind, congress):
    path = os.path.join(RAW, f"H{congress}_{kind}.csv")
    if not os.path.exists(path):
        os.makedirs(RAW, exist_ok=True)
        body = opener.open(urllib.request.Request(BASE + f"{kind}/H{congress}_{kind}.csv", headers={"User-Agent": "texas-nominate validation"}), timeout=300).read()
        open(path, "wb").write(body)
    return list(csv.DictReader(open(path)))


def main():
    first, last = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (110, 117)
    votes, members = [], {}
    for c in range(first, last + 1):
        for m in load("members", c):
            if m["chamber"] == "House" and m["nominate_dim1"]:
                members[(int(float(m["icpsr"])), c)] = (float(m["nominate_dim1"]), float(m["nominate_dim2"]), m["party_code"], m["bioname"])
        for v in load("votes", c):
            code = int(float(v["cast_code"]))
            if code in (1, 2, 3, 4, 5, 6):
                votes.append((c, int(float(v["rollnumber"])), int(float(v["icpsr"])), code <= 3))
        print(f"loaded {c}: {len(votes):,} votes so far", flush=True)
    votes = np.array(votes, dtype=np.int64)
    cong, roll, icpsr, yea = votes[:, 0], votes[:, 1], votes[:, 2], votes[:, 3].astype(bool)

    # Voteview's filters: lopsided roll calls (minority < 2.5%) and members with < 20 votes
    key = cong * 100000 + roll
    rc_keys, rc = np.unique(key, return_inverse=True)
    yeas = np.bincount(rc, weights=yea); tot = np.bincount(rc)
    keep_rc = np.minimum(yeas, tot - yeas) / tot >= 0.025
    sel = keep_rc[rc]
    for _ in range(3):
        mem_keys, mem = np.unique(icpsr[sel], return_inverse=True)
        enough = np.bincount(mem) >= 20
        sel_idx = np.flatnonzero(sel)
        sel[sel_idx[~enough[mem]]] = False
    cong, icpsr, yea, key = cong[sel], icpsr[sel], yea[sel], key[sel]
    rc_keys, rc = np.unique(key, return_inverse=True)
    mem_keys, mem = np.unique(icpsr, return_inverse=True)
    first_c = np.full(len(mem_keys), 10 ** 9); last_c = np.zeros(len(mem_keys), int)
    np.minimum.at(first_c, mem, cong); np.maximum.at(last_c, mem, cong)
    span = np.maximum(last_c - first_c, 1)
    frac = np.where(last_c[mem] > first_c[mem], (cong - first_c[mem]) / span[mem], 0.0)
    print(f"scaling {len(rc_keys):,} roll calls, {len(mem_keys):,} members, {len(rc):,} votes", flush=True)

    results = {"congresses": [first, last], "roll_calls": int(len(rc_keys)), "members": int(len(mem_keys)), "votes": int(len(rc))}
    for K in (1, 2):
        t0 = time.time()
        model = DWNominate(rc, mem, yea, frac, len(rc_keys), len(mem_keys), K=K)
        model.start(svd_start(rc, mem, yea, len(rc_keys), len(mem_keys), K)).fit(verbose=True)
        secs = time.time() - t0
        ours, theirs, party = [], [], []
        for (ic, c), (d1, d2, pc, name) in members.items():
            idx = np.searchsorted(mem_keys, ic)
            if idx < len(mem_keys) and mem_keys[idx] == ic:
                f = 0.0 if last_c[idx] == first_c[idx] else (c - first_c[idx]) / span[idx]
                if 0 <= f <= 1:
                    ours.append(model.position(np.array([idx]), np.array([f]))[0]); theirs.append((d1, d2)); party.append(pc)
        ours, theirs, party = np.array(ours), np.array(theirs), np.array(party)
        np.savetxt(os.path.join(ROOT, "data", "processed", f"validation_voteview_k{K}.csv"),
                   np.column_stack([ours, theirs]), delimiter=",", fmt="%.4f")
        r = {"seconds": round(secs, 1), "fit": model.fit_stats()}
        if K == 1:
            r["corr_dim1"] = float(abs(np.corrcoef(ours[:, 0], theirs[:, 0])[0, 1]))
            r["corr_dim1_within_party"] = {lab: float(abs(np.corrcoef(ours[party == p, 0], theirs[party == p, 0])[0, 1]))
                                           for p, lab in (("100", "Democrats"), ("200", "Republicans"))}
        else:
            # our axes are identified only up to a rotation that the dimension weights pin down weakly;
            # find the rotation that best lines ours up with Voteview's, then compare
            best = None
            for deg in np.arange(-90, 90.5, 0.5):
                t = np.radians(deg)
                rot = ours @ np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
                for sgn in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    c1 = np.corrcoef(sgn[0] * rot[:, 0], theirs[:, 0])[0, 1]
                    c2 = np.corrcoef(sgn[1] * rot[:, 1], theirs[:, 1])[0, 1]
                    if best is None or c1 + c2 > best[0]:
                        best = (c1 + c2, deg, c1, c2, sgn, rot * np.array(sgn))
            _, deg, c1, c2, sgn, rot = best
            r.update({"rotation_deg": float(deg), "corr_dim1": float(c1), "corr_dim2": float(c2),
                      "corr_dim1_within_party": {lab: float(np.corrcoef(rot[party == p, 0], theirs[party == p, 0])[0, 1])
                                                 for p, lab in (("100", "Democrats"), ("200", "Republicans"))},
                      "corr_dim1_unrotated": float(max(abs(np.corrcoef(ours[:, 0], theirs[:, 0])[0, 1]), abs(np.corrcoef(ours[:, 1], theirs[:, 0])[0, 1])))})
        results[f"k{K}"] = r
        print(json.dumps({f"k{K}": {k: v for k, v in r.items() if k != "fit"}}, indent=1), flush=True)
    json.dump(results, open(os.path.join(ROOT, "data", "processed", "validation_voteview.json"), "w"), indent=1)


if __name__ == "__main__":
    main()

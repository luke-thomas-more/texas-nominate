"""Out-of-sample version of era_check.py: the shared fit and each era-only fit are run with
HOLDOUT=0.1 (the same 10% of votes held out by both), then scored only on held-out votes both
kept. In-sample APRE flatters the era-only fits, which have more freedom per era; this does not.

Reads data/processed/holdout/{shared,era_<first>_<last>}/holdout_votes.csv; writes
data/processed/holdout/era_holdout.json. With an argument (a folder under holdout/, e.g. hybrid)
that fit is scored in place of the shared one and the output is era_holdout_<folder>.json.
"""
import csv, json, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from era_check import ERAS

HO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "processed", "holdout")


def read(folder):
    return {(r["rc_id"], r["member_id"]): (r["yea"] == "1", float(r["z"]), r["majority_yea"] == "1")
            for r in csv.DictReader(open(os.path.join(folder, "holdout_votes.csv")))}


def score(votes, keys):
    """Correct classification, APRE against 'everyone votes with the majority', and GMP."""
    n = c = base = 0
    ll = 0.0
    for k in keys:
        y, z, maj = votes[k]
        n += 1
        c += (z > 0) == y
        base += y == maj
        ll += math.log(max(0.5 * math.erfc(-(z if y else -z) / math.sqrt(2)), 1e-300))
    return {"correct": c / n, "apre": (c - base) / (n - base), "gmp": math.exp(ll / n)}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "shared"
    shared = read(os.path.join(HO, name))
    out = []
    for a, b, label in ERAS:
        folder = os.path.join(HO, f"era_{a}_{b}")
        if not os.path.exists(os.path.join(folder, "holdout_votes.csv")):
            continue
        own = read(folder)
        keys = shared.keys() & own.keys()
        sh, eo = score(shared, keys), score(own, keys)
        out.append({"era": label, "legs": [a, b], "held_out_votes": len(keys), "shared": sh, "era_only": eo,
                    "apre_gap": eo["apre"] - sh["apre"]})
        print(f"{label:34} {len(keys):>8,} votes   APRE shared {sh['apre']:.3f}  era-only {eo['apre']:.3f}  gap {eo['apre'] - sh['apre']:+.3f}"
              f"   GMP {sh['gmp']:.3f} / {eo['gmp']:.3f}")
    json.dump(out, open(os.path.join(HO, "era_holdout.json" if name == "shared" else f"era_holdout_{name}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()

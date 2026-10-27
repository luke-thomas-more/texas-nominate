"""Does one shared space fit every era? Compare, era by era, how well the all-history fit predicts
that era's votes (APRE) with a fit of that era alone. A big gap means the shared axes miss what
that era was fighting about, which is the signal to consider per-era axes (the hybrid model).

Era fits are written to data/processed/fit_era_<first>_<last>/ (run them with fit_texas.py first,
or pass --fit to run them here). Prints a table; writes data/processed/era_check.json.
"""
import csv, json, os, subprocess, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
ERAS = [(1, 8, "1846-1860 antebellum"), (9, 13, "1861-1873 war and Reconstruction"), (14, 26, "1874-1900"),
        (27, 41, "1901-1930"), (42, 56, "1931-1960"), (57, 73, "1961-1994"), (74, 89, "1995-2025")]


def apre(params_csv, keep):
    """APRE over the roll calls in keep (both fits are scored on the same set)."""
    num = den = 0
    for r in csv.DictReader(open(params_csv)):
        if r["rc_id"] not in keep:
            continue
        m, e = int(r["minority"]), int(r["errors"])
        num += m - e
        den += m
    return num / den if den else None


def main():
    shared = os.path.join(os.environ.get("SHARED_FIT", os.path.join(PROC, "fit")), "rollcall_params.csv")
    rc_leg = {}
    for f in ("rollcalls.csv", "rollcalls_hist.csv"):
        for r in csv.DictReader(open(os.path.join(PROC, f))):
            rc_leg[r["rc_id"]] = int(r["leg"])
    out = []
    for a, b, label in ERAS:
        folder = os.path.join(PROC, f"fit_era_{a}_{b}")
        if "--fit" in sys.argv and not os.path.exists(os.path.join(folder, "rollcall_params.csv")):
            os.makedirs(folder, exist_ok=True)
            subprocess.run([sys.executable, os.path.join(ROOT, "pipeline", "fit_texas.py"), "2", str(a), str(b)],
                           env=dict(os.environ, FIT_DIR=folder, FIT_ONLY="1", TREND="0"), check=True)
        own_csv = os.path.join(folder, "rollcall_params.csv")
        if not os.path.exists(own_csv):
            continue
        keep = {r["rc_id"] for r in csv.DictReader(open(shared)) if a <= rc_leg.get(r["rc_id"], 0) <= b}
        keep &= {r["rc_id"] for r in csv.DictReader(open(own_csv))}
        own, sh = apre(own_csv, keep), apre(shared, keep)
        out.append({"era": label, "legs": [a, b], "roll_calls": len(keep), "apre_shared": sh, "apre_era_only": own,
                    "gap": (own - sh) if (own is not None and sh is not None) else None})
        print(f"{label:34} shared {sh if sh is None else round(sh, 3)}   era-only {own if own is None else round(own, 3)}"
              f"   gap {'' if out[-1]['gap'] is None else round(out[-1]['gap'], 3)}")
    json.dump(out, open(os.path.join(PROC, "era_check.json"), "w"), indent=1)


if __name__ == "__main__":
    main()

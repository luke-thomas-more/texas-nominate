"""Evidence for the era-by-era axis guide in axes.json (prints a report; nothing is written).

For each era: party means, which way its roll calls cut, the home counties at each end of
dimension 2, where well-known members sit, and how the two sides voted on the roll calls the
guide cites. Run it on the fit the site is built from (FIT_DIR) before changing the guide.
"""
import collections, csv, gzip, json, math, os
import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
GOOD = {"ok", "near", "ok_digit", "ok_lists"}
ERAS = [(1, 13, "1846-73"), (14, 26, "1874-1900"), (27, 41, "1901-30"), (42, 56, "1931-60"),
        (57, 67, "1961-82"), (68, 73, "1983-94"), (74, 89, "1995-2025")]
# (search string in the full name, what the guide says about them)
PEOPLE = [("Throckmorton", "Unionist, 1850s"), ("Louis Trezevant Wigfall", "secessionist fire-eater, 1850s"),
          ("Maury Maverick", "New Deal liberal"), ("Henry Barbosa Gonzalez", "liberal"), ("Eckhardt", "liberal"),
          ("Barbara Charline Jordan", "liberal"), ("Oscar Holcombe Mauzy", "liberal"), ("Farenthold", "liberal"),
          ("Dorsey B. Hardeman", "conservative"), ("Wardlow William Lane", "conservative"), ("Coke Robert Stevenson", "conservative"),
          ("Talmadge L. Heflin", "Craddick team"), ("Fred Hill", "Craddick team"), ("Four Price", "leadership"),
          ("Standard Dwight Lambert", "leadership"), ("Tommy Merritt", "anti-Craddick"), ("Delwin Jones", "anti-Craddick"),
          ("Stickland", "rebel"), ("Brian Harrison", "rebel"), ("Andy Hopper", "rebel"), ("Terry Canales", "maverick D"),
          ("Robert Lee Nichols", "Senate institutionalist"), ("Charles Schwertner", "Senate institutionalist"),
          ("Mayes Middleton", "Senate insurgent"), ("Sarah Eckhardt", "Senate")]
# roll calls the guide cites: (rc_id, what a yea meant)
CITED = [("24R-Sx0860", "yea = protect labor unions"), ("24R-Sx0874", "yea = add 'to better define and punish trusts'"),
         ("32R-Hx0206", "yea = Terrell of Bexar's (wet) motion to table referral to Liquor Traffic"),
         ("32R-Sx0700", "yea = wet substitute: 'in favor of local self government' for 'against state wide prohibition'"),
         ("33R-Sx0651", "yea = dry leader Lattimore's motion to table a wet substitute"),
         ("33R-Sx0655", "yea = dry leader Lattimore's motion to table a wet amendment")]


def era_of(leg):
    return next(n for a, b, n in ERAS if a <= leg <= b)


def pcode(p):
    return "R" if p.startswith("Republican") or p == "RA" else "D" if p.startswith("Democrat") else "O"


def main():
    fit = json.load(open(os.path.join(FIT, "summary.json")))
    w = fit["w"]
    print(f"fit {FIT}: w {w}, beta {fit['beta']:.2f}, dim2 oriented: {fit.get('dim2_oriented', 'by fit_texas')}")
    people = {r["member_id"]: r for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    terms = {(t["member_id"], t["leg"], t["chamber"]): t for t in csv.DictReader(open(os.path.join(PROC, "terms.csv")))}
    scores = list(csv.DictReader(open(os.path.join(FIT, "member_scores.csv"))))
    rcs = {}
    for f in ("rollcalls.csv", "rollcalls_hist.csv"):
        for r in csv.DictReader(open(os.path.join(PROC, f))):
            rcs[r["rc_id"]] = r

    print("\n=== party means (dim1, dim2) and counts, member-legislatures")
    g = collections.defaultdict(list)
    for s in scores:
        g[(era_of(int(s["leg"])), s["chamber"], pcode(s["party"]))].append((float(s["dim1"]), float(s["dim2"])))
    for _, _, e in ERAS:
        parts = []
        for ch in "HS":
            for p in "DRO":
                x = np.array(g.get((e, ch, p), []))
                if len(x) >= 3:
                    parts.append(f"{ch}{p} n{len(x)} ({x[:, 0].mean():+.2f},{x[:, 1].mean():+.2f})")
        print(f"  {e:10s} " + "  ".join(parts))

    print("\n=== 1874-1907 dimension 1 by party label (the guide's Greenback/Populist sentence)")
    g2 = collections.defaultdict(list)
    for s in scores:
        l = int(s["leg"])
        if 14 <= l <= 30:
            g2[(s["party"] or "?", s["chamber"], "1874-88" if l <= 20 else "1889-1907")].append(float(s["dim1"]))
    for k, v in sorted(g2.items(), key=lambda x: -len(x[1])):
        if len(v) >= 3:
            print(f"  {k[0][:20]:20s} {k[1]} {k[2]:9s} n{len(v):5d} dim1 {np.mean(v):+.2f}")

    print("\n=== cutting-line direction by era (share of minority-weighted, PRE>=.3 roll calls); 0 = splits left/right, 90 = up/down")
    h = collections.defaultdict(lambda: np.zeros(6))
    for p in csv.DictReader(open(os.path.join(FIT, "rollcall_params.csv"))):
        if float(p["pre"]) < 0.3:
            continue
        a = abs(math.degrees(math.atan2(w[1] ** 2 * float(p["spread2"]), w[0] ** 2 * float(p["spread1"])))) % 180
        a = min(a, 180 - a)
        h[era_of(int(rcs[p["rc_id"]]["leg"]))][min(int(a // 15), 5)] += int(p["minority"])
    for _, _, e in ERAS:
        x = h[e] / h[e].sum()
        print(f"  {e:10s} " + " ".join(f"{a * 15:2d}-{a * 15 + 15}:{v * 100:3.0f}%" for a, v in enumerate(x)))

    print("\n=== House home counties at each end of dimension 2 (counties with >= 4 member-legislatures)")
    for a, b, e in ERAS:
        c = collections.defaultdict(list)
        for s in scores:
            if a <= int(s["leg"]) <= b and s["chamber"] == "H":
                cty = (terms.get((s["member_id"], s["leg"], "H"), {}).get("county") or "").strip()
                if cty:
                    c[cty].append(float(s["dim2"]))
        rows = sorted((np.mean(v), k) for k, v in c.items() if len(v) >= 4)
        if rows:
            print(f"  {e}: bottom: " + ", ".join(f"{k} {m:+.2f}" for m, k in rows[:10]))
            print(f"  {' ' * len(e)}  top:    " + ", ".join(f"{k} {m:+.2f}" for m, k in rows[-10:][::-1]))

    print("\n=== named members")
    for pat, note in PEOPLE:
        for mid, p in people.items():
            if pat.lower() in (p["full_name"] or p["name"]).lower():
                rows = [s for s in scores if s["member_id"] == mid]
                if rows:
                    s = rows[0]
                    print(f"  {p['full_name'][:34]:34s} {rows[0]['leg']}{rows[0]['chamber']}-{rows[-1]['leg']}{rows[-1]['chamber']:4s} "
                          f"({float(s['dim1']):+.2f}, {float(s['dim2']):+.2f})  {note}")

    print("\n=== cited roll calls: mean position of yeas vs nays")
    want = {k for k, _ in CITED}
    votes = collections.defaultdict(list)
    with gzip.open(os.path.join(PROC, "votes_hist.csv.gz"), "rt") as f:
        for rc, m, v in csv.reader(f):
            if rc in want and v in "YN":
                votes[rc].append((m, v))
    sc = {(s["member_id"], s["leg"], s["chamber"]): (float(s["dim1"]), float(s["dim2"])) for s in scores}
    for k, note in CITED:
        r = rcs[k]
        side = {v: np.array([sc[(m, r["leg"], r["chamber"])] for m, vv in votes[k] if vv == v and (m, r["leg"], r["chamber"]) in sc]) for v in "YN"}
        print(f"  {k} {r['date']} [{r['status']}] Y{len(side['Y'])} ({side['Y'][:, 0].mean():+.2f},{side['Y'][:, 1].mean():+.2f})"
              f"  N{len(side['N'])} ({side['N'][:, 0].mean():+.2f},{side['N'][:, 1].mean():+.2f})  {note}")


if __name__ == "__main__":
    main()

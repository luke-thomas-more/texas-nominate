"""Diagnostics for naming the dimensions (prints a report; nothing is written).

For each scaled roll call the cutting line's orientation says which dimension it divides on:
the line's normal is (w1^2 d1, w2^2 d2), so a normal pointing up/down means a dimension-2 vote.
"""
import collections, csv, json, math, os, re, sys
import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))


def main():
    fit = json.load(open(os.path.join(FIT, "summary.json")))
    w = fit["w"]
    rcs = {r["rc_id"]: r for r in csv.DictReader(open(os.path.join(PROC, "rollcalls.csv")))}
    people = {int(r["member_id"]): r["name"] for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    scores = list(csv.DictReader(open(os.path.join(FIT, "member_scores.csv"))))
    params = list(csv.DictReader(open(os.path.join(FIT, "rollcall_params.csv"))))

    rows = []
    for p in params:
        n1, n2 = w[0] ** 2 * float(p["spread1"]), w[1] ** 2 * float(p["spread2"])
        ang = abs(math.degrees(math.atan2(n2, n1)))
        ang = min(ang, 180 - ang)            # 0 = pure dimension 1, 90 = pure dimension 2
        r = rcs[p["rc_id"]]
        rows.append((ang, float(p["pre"]), int(p["minority"]), r))
    print(f"scaled roll calls: {len(rows)}")
    bins = collections.Counter(min(int(a // 15) * 15, 75) for a, _, _, _ in rows)
    print("cutting-line angle from dimension 1 (0 = splits left/right, 90 = splits up/down):",
          {f"{k}-{k + 15}": v for k, v in sorted(bins.items())})

    dim2 = [x for x in rows if x[0] > 60 and x[1] > 0.4 and x[2] >= 10]
    print(f"\n=== {len(dim2)} well-fitted dimension-2 votes (angle > 60, PRE > .4, minority >= 10); sample by legislature:")
    by_leg = collections.defaultdict(list)
    for x in dim2:
        by_leg[(x[3]["leg"], x[3]["chamber"])].append(x)
    for k in sorted(by_leg, key=lambda k: (int(k[0]), k[1])):
        print(f"  {k[0]}{k[1]}: {len(by_leg[k])}")
    words = collections.Counter()
    for a, pre, mn, r in dim2:
        txt = (r["caption"] + " " + r["heading"]).lower()
        for wd in re.findall(r"[a-z]{5,}", txt):
            words[wd] += 1
    stop = {"relating", "entitled", "provision", "providing", "certain", "state", "texas", "authorizing", "amendment",
            "reading", "third", "second", "senate", "house", "establishment", "creation", "including", "offense",
            "district", "county", "penalty", "fee", "under", "public"}
    print("  frequent caption words:", [w_ for w_, c in words.most_common(60) if w_ not in stop][:40])
    for a, pre, mn, r in sorted(dim2, key=lambda x: -x[2])[:25]:
        print(f"   {r['rc_id']:14} {r['date']} {r['bill']:8} Y{r['n_y']:>3}-N{r['n_n']:<3} PRE {pre:.2f} | {r['text'][:60]} | {r['caption'][:90]}")

    # members at the ends of dimension 2, by legislature/chamber and party
    print("\n=== dimension 2 extremes (latest legislatures)")
    grp = collections.defaultdict(list)
    for s in scores:
        grp[(int(s["leg"]), s["chamber"], s["party"][:1])].append((float(s["dim2"]), people[int(s["member_id"])], float(s["dim1"])))
    for leg in (89, 88, 87, 85, 81, 78):
        for ch in "HS":
            for p in "RD":
                g = sorted(grp.get((leg, ch, p), []))
                if len(g) < 4:
                    continue
                lo = ", ".join(f"{n} ({d2:+.2f})" for d2, n, _ in g[:4]); hi = ", ".join(f"{n} ({d2:+.2f})" for d2, n, _ in g[-4:])
                print(f"  {leg}{ch} {p}: low: {lo}\n          high: {hi}")

    # party means by legislature
    print("\n=== party means (dim1, dim2) by legislature")
    for leg in range(74, 90):
        line = []
        for ch in "HS":
            for p in "DR":
                g = grp.get((leg, ch, p), [])
                if g:
                    line.append(f"{ch}{p} {np.mean([x[2] for x in g]):+.2f},{np.mean([x[0] for x in g]):+.2f}")
        print(f"  {leg}: " + "  ".join(line))

    # contested Speaker races: does dimension 2 separate the camps within the GOP?
    print("\n=== Speaker elections and other day-one House votes")
    import gzip
    votes = collections.defaultdict(dict)
    want = {k for k, r in rcs.items() if r["chamber"] == "H" and re.search(r"speaker", r["text"] + r["heading"], re.I) and r["status"] == "ok"}
    with gzip.open(os.path.join(PROC, "votes.csv.gz"), "rt") as f:
        for rc_id, mid, v in csv.reader(f):
            if rc_id in want:
                votes[rc_id][int(mid)] = v
    sc = {(int(s["member_id"]), int(s["leg"]), s["chamber"]): s for s in scores}
    for k in sorted(want):
        r = rcs[k]
        yes = [float(sc[(m, int(r["leg"]), "H")]["dim2"]) for m, v in votes[k].items() if v == "Y" and (m, int(r["leg"]), "H") in sc and sc[(m, int(r["leg"]), "H")]["party"].startswith("Rep")]
        no = [float(sc[(m, int(r["leg"]), "H")]["dim2"]) for m, v in votes[k].items() if v == "N" and (m, int(r["leg"]), "H") in sc and sc[(m, int(r["leg"]), "H")]["party"].startswith("Rep")]
        if yes and no:
            print(f"  {k} {r['date']} Y{r['n_y']}-N{r['n_n']} | {r['text'][:70]} | GOP dim2 mean yea {np.mean(yes):+.2f} (n={len(yes)}) vs nay {np.mean(no):+.2f} (n={len(no)})")


if __name__ == "__main__":
    main()

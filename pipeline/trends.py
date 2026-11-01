"""Polarization and party voting, legislature by legislature, 1846-2025, with Congress alongside.

For each legislature and chamber where both parties seat at least five members:
  the party means on each axis, and the gap between them on the first, in pooled standard deviations
  (scale-free, so it can be set beside Congress's, measured the same way on Voteview's DW-NOMINATE scores);
  how far the parties overlap (the share of members on the other party's side of the midpoint between the
  party medians);
  party unity votes: contested roll calls on which most Democrats voted one way and most Republicans the
  other (Congressional Quarterly's definition), as a share of contested roll calls; and each party's mean
  party unity score, the share of those votes on which its members voted with their party's majority.
Congress: the same gap and overlap per Congress and chamber, from Voteview's member file (HSall_members.csv
in data/raw/voteview/), for the whole Congress and for Texas's own delegation.

Writes docs/data/trends.json. Usage: FIT_DIR=data/processed/fit_v4 python pipeline/trends.py
"""
import collections, csv, gzip, json, os, statistics

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
DOCS = os.environ.get("DOCS_DIR", os.path.join(ROOT, "docs", "data"))
GOOD = {"ok", "near", "ok_digit", "ok_lists", "ok_rest"}
MIN_SEATS = 5            # each party's members placed, for a two-party comparison to mean anything


def party_code(p):
    return "R" if p.startswith("Republican") or p in ("RA", "Radical Republican") else "D" if p.startswith("Democrat") else "O"


def contested(y, n):
    return y + n > 0 and min(y, n) / (y + n) >= 0.025


def gap_stats(d, r):
    """(gap in pooled SDs, overlap share) for two parties' first-dimension scores."""
    md, mr = statistics.fmean(d), statistics.fmean(r)
    sd = (((len(d) - 1) * statistics.variance(d) + (len(r) - 1) * statistics.variance(r)) / (len(d) + len(r) - 2)) ** 0.5
    cut = (statistics.median(d) + statistics.median(r)) / 2
    side = 1 if mr > md else -1                    # which way Republicans lie
    wrong = sum(1 for x in d if (x - cut) * side > 0) + sum(1 for x in r if (x - cut) * side < 0)
    return (mr - md) / sd if sd else None, wrong / (len(d) + len(r))


def texas():
    scores = collections.defaultdict(dict)        # (leg, ch) -> {member: (party, dim1, dim2)}
    for r in csv.DictReader(open(os.path.join(FIT, "member_scores.csv"))):
        scores[(int(r["leg"]), r["chamber"])][int(r["member_id"])] = (party_code(r["party"]), float(r["dim1"]), float(r.get("dim2") or 0))
    rcs = collections.defaultdict(list)
    for name in ("rollcalls.csv", "rollcalls_hist.csv"):
        path = os.path.join(PROC, name)
        if os.path.exists(path):
            for r in csv.DictReader(open(path)):
                if r["status"] in GOOD and contested(int(r["n_y"]), int(r["n_n"])):
                    rcs[(int(r["leg"]), r["chamber"])].append((r["rc_id"], r["date"]))
    want = {rc for v in rcs.values() for rc, _ in v}
    votes = collections.defaultdict(dict)
    for name in ("votes.csv.gz", "votes_hist.csv.gz"):
        path = os.path.join(PROC, name)
        if os.path.exists(path):
            with gzip.open(path, "rt") as f:
                rd = csv.reader(f); next(rd)
                for rc, m, v in rd:
                    if rc in want and v in ("Y", "N"):
                        votes[rc][int(m)] = v
    # each legislature's first year: the year most of its members' terms began (a few scans carry a wrong
    # year, and a senator's four-year term may have begun two years before)
    starts = collections.defaultdict(collections.Counter)
    for t in csv.DictReader(open(os.path.join(PROC, "terms.csv"))):
        if t["start"][:4].isdigit():
            starts[int(t["leg"])][t["start"][:4]] += 1
    start = {leg: c.most_common(1)[0][0] for leg, c in starts.items()}
    out = []
    for (leg, ch), mem in sorted(scores.items()):
        by = collections.defaultdict(list)
        for p, x, y in mem.values():
            by[p].append((x, y))
        row = {"leg": leg, "ch": ch, "year": int(start[leg]) if leg in start else None,
               "n": {p: len(v) for p, v in by.items()},
               "mean": {p: [round(statistics.fmean(a for a, _ in v), 4), round(statistics.fmean(b for _, b in v), 4)] for p, v in by.items()}}
        if len(by["D"]) >= MIN_SEATS and len(by["R"]) >= MIN_SEATS:
            g, o = gap_stats([a for a, _ in by["D"]], [a for a, _ in by["R"]])
            row["gap"], row["overlap"] = round(g, 3), round(o, 3)
            # party unity votes, and each member's party unity score
            unity_n, with_party = 0, collections.defaultdict(lambda: [0, 0])
            for rc, _ in rcs[(leg, ch)]:
                v = votes.get(rc, {})
                tally = {p: collections.Counter(c for m, c in v.items() if mem.get(m, ("O",))[0] == p) for p in "DR"}
                if min(sum(t.values()) for t in tally.values()) < 2:
                    continue
                maj = {p: max("YN", key=lambda c: tally[p][c]) for p in "DR"}
                if tally["D"]["Y"] == tally["D"]["N"] or tally["R"]["Y"] == tally["R"]["N"] or maj["D"] == maj["R"]:
                    continue
                unity_n += 1
                for m, c in v.items():
                    p = mem.get(m, ("O",))[0]
                    if p in "DR":
                        with_party[m][0] += c == maj[p]; with_party[m][1] += 1
            row["unity_votes"] = round(unity_n / len(rcs[(leg, ch)]), 3) if rcs[(leg, ch)] else None
            for p in "DR":
                s = [a / b for m, (a, b) in with_party.items() if mem[m][0] == p and b >= 10]
                row[f"unity_{p}"] = round(statistics.fmean(s), 3) if s else None
        row["contested"] = len(rcs[(leg, ch)])
        out.append(row)
    return out


def congress():
    """Voteview: the party gap and overlap per Congress and chamber, all members and Texas's delegation."""
    path = os.path.join(ROOT, "data", "raw", "voteview", "HSall_members.csv")
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in csv.DictReader(open(path)):
        if r["chamber"] not in ("House", "Senate") or not r["nominate_dim1"]:
            continue
        code = r["party_code"].split(".")[0]                 # "100" or "100.0", by Congress
        p = "D" if code == "100" else "R" if code == "200" else None
        if p:
            by[(int(r["congress"]), r["chamber"][0], "all")][p].append(float(r["nominate_dim1"]))
            if r["state_abbrev"] == "TX":
                by[(int(r["congress"]), r["chamber"][0], "TX")][p].append(float(r["nominate_dim1"]))
    out = []
    for (c, ch, who), parties in sorted(by.items()):
        if len(parties["D"]) >= MIN_SEATS and len(parties["R"]) >= MIN_SEATS:
            g, o = gap_stats(parties["D"], parties["R"])
            out.append({"congress": c, "year": 1787 + 2 * c, "ch": ch, "who": who, "gap": round(g, 3), "overlap": round(o, 3),
                        "n": {p: len(v) for p, v in parties.items()}})
    return out


def main():
    data = {"texas": texas(), "congress": congress(), "min_seats": MIN_SEATS}
    json.dump(data, open(os.path.join(DOCS, "trends.json"), "w"), separators=(",", ":"))
    two = [r for r in data["texas"] if "gap" in r]
    print(f"trends: {len(data['texas'])} legislature-chambers, {len(two)} with two parties; {len(data['congress'])} Congress series points")


if __name__ == "__main__":
    main()

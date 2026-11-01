"""Texas legislators who also served in Congress: their TX-NOMINATE scores beside their Voteview DW-NOMINATE ones.

Members are matched to Voteview's Texas delegation (data/raw/voteview/HSall_members.csv) by surname, then by
first name, nickname or initials, and by dates: the two careers must both fall inside the Voteview member's
lifetime, and no more than 50 years apart. pipeline/congress_bridge_review.csv records each pair as checked
(keep = 1 or 0); pairs not yet in it are written there with keep blank, and only kept pairs are used.

Writes docs/data/congress.json: the pairs (with both careers' years), and the correlation of the first
dimensions, overall and for members whose congressional careers began before and after 1980.
Usage: FIT_DIR=data/processed/fit_v4 python pipeline/congress_bridge.py
"""
import collections, csv, json, os, re, statistics, unicodedata

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
DOCS = os.environ.get("DOCS_DIR", os.path.join(ROOT, "docs", "data"))
REVIEW = os.path.join(ROOT, "pipeline", "congress_bridge_review.csv")
NICK = {"bill": "william", "billy": "william", "will": "william", "willie": "william", "bob": "robert", "bobby": "robert",
        "jim": "james", "jimmy": "james", "joe": "joseph", "jack": "john", "johnny": "john", "tom": "thomas", "tommy": "thomas",
        "dick": "richard", "rick": "richard", "chuck": "charles", "charlie": "charles", "dan": "daniel", "danny": "daniel",
        "ed": "edward", "eddie": "edward", "ted": "edward", "mike": "michael", "mickey": "michael", "sam": "samuel",
        "pete": "peter", "ron": "ronald", "don": "donald", "ken": "kenneth", "larry": "lawrence", "jerry": "gerald",
        "tony": "anthony", "al": "albert", "fred": "frederick", "frank": "francis", "hank": "henry", "harry": "henry",
        "kika": "eligio", "lloyd": "lloyd", "gene": "eugene", "ben": "benjamin", "andy": "andrew", "pat": "patrick",
        "steve": "stephen", "greg": "gregory", "chet": "chester", "kay": "kathryn", "liz": "elizabeth", "beto": "robert"}


def fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def given_names(names):
    """The given names, initials and nicknames in a string of them, normalized: 'William Roark "Bill"' ->
    {william, roark, bill}, with each nickname's full name too ('bill' -> 'william')."""
    toks = {t for t in re.findall(r"[a-z]+", fold(names)) if t not in ("jr", "sr", "ii", "iii", "iv")}
    return toks | {NICK.get(t, t) for t in toks}


def given_of(full):
    """The given names of a full name ('William Roark "Bill" Ratliff' -> {william, roark, bill})."""
    toks = re.findall(r"[A-Za-z][A-Za-z'.\-]*", full)
    return given_names(" ".join(t for t in toks[:-1] if t.lower().strip(".") not in ("jr", "sr", "ii", "iii", "iv")))


def surname(full):
    toks = [t for t in re.findall(r"[A-Za-z][A-Za-z'\-]+", fold(full)) if t not in ("jr", "sr", "ii", "iii", "iv")]
    return toks[-1] if toks else ""


def main():
    people = {int(r["member_id"]): r for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    years = collections.defaultdict(list)                 # member -> service years in the Legislature
    for t in csv.DictReader(open(os.path.join(PROC, "terms.csv"))):
        if t["start"][:4].isdigit():
            years[int(t["member_id"])] += [int(t["start"][:4]), int((t["end"] or t["start"])[:4])]
    score = {}
    for r in csv.DictReader(open(os.path.join(FIT, "member_scores.csv"))):
        score[int(r["member_id"])] = (float(r["dim1"]), float(r["dim2"]))
    vv = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "data", "raw", "voteview", "HSall_members.csv"))):
        if r["state_abbrev"] != "TX" or r["chamber"] not in ("House", "Senate") or not r["nominate_dim1"]:
            continue
        v = vv.setdefault(r["icpsr"], {"icpsr": r["icpsr"], "name": r["bioname"], "born": r["born"], "died": r["died"],
                                       "dim1": float(r["nominate_dim1"]), "dim2": float(r["nominate_dim2"] or 0),
                                       "party": {"100": "D", "200": "R"}.get(r["party_code"].split(".")[0], "O"),   # "100" or "100.0"
                                       "chambers": set(), "years": []})
        v["chambers"].add(r["chamber"]); v["years"].append(1787 + 2 * int(r["congress"]))
    by_surname = collections.defaultdict(list)
    for v in vv.values():
        last, _, first = v["name"].partition(",")
        v["surname"], v["given"] = surname(last), given_names(first)
        by_surname[v["surname"]].append(v)

    review = {}
    if os.path.exists(REVIEW):
        review = {(int(r["member_id"]), r["icpsr"]): r for r in csv.DictReader(open(REVIEW))}
    pairs, new = [], []
    for m, p in people.items():
        if m not in score or not years[m]:
            continue
        cands = []
        for v in by_surname.get(surname(p["full_name"] or p["name"]), []):
            given = given_of(p["full_name"] or p["name"]) | given_of(p["name"])
            # a given name or nickname in common, or the same initial where one of them gives only the initial
            initials = {g[0] for g in given} & {g[0] for g in v["given"]}
            if not (given & v["given"] or initials and any(len(g) == 1 for g in given | v["given"])):
                continue
            ty, cy = (min(years[m]), max(years[m])), (min(v["years"]), max(v["years"]) + 2)
            born = int(float(v["born"])) if v["born"] else None
            died = int(float(v["died"])) if v["died"] else None
            if born and ty[0] < born + 18 or died and ty[0] > died or abs(cy[0] - ty[1]) > 50 and abs(ty[0] - cy[1]) > 50:
                continue
            cands.append(v)
        for v in cands:
            key = (m, v["icpsr"])
            r = review.get(key)
            if r is None:
                new.append({"member_id": m, "name": p["name"], "full_name": p["full_name"], "tx_years": f"{min(years[m])}-{max(years[m])}",
                            "icpsr": v["icpsr"], "vv_name": v["name"], "vv_years": f"{min(v['years'])}-{max(v['years']) + 2}",
                            "born": v["born"], "died": v["died"], "keep": ""})
            elif r["keep"] == "1":
                pairs.append((m, v))
    if new:
        rows = list(review.values()) + new
        with open(REVIEW, "w", newline="") as f:
            w = csv.DictWriter(f, ["member_id", "name", "full_name", "tx_years", "icpsr", "vv_name", "vv_years", "born", "died", "keep"])
            w.writeheader(); w.writerows(sorted(rows, key=lambda r: (r["vv_name"], int(r["member_id"]))))
        print(f"{len(new)} new candidate pairs written to {REVIEW} for review (keep = 1 or 0)")

    def corr(sel):
        if len(sel) < 5:
            return None
        a, b = [score[m][0] for m, _ in sel], [v["dim1"] for _, v in sel]
        return round(statistics.correlation(a, b), 3)
    early = [(m, v) for m, v in pairs if min(v["years"]) < 1980]
    late = [(m, v) for m, v in pairs if min(v["years"]) >= 1980]
    out = {"pairs": [{"id": m, "n": people[m]["name"], "tx": [round(score[m][0], 3), round(score[m][1], 3)],
                      "tx_years": [min(years[m]), max(years[m])], "vv": [v["dim1"], v["dim2"]], "vv_name": v["name"],
                      "vv_years": [min(v["years"]), max(v["years"]) + 2], "party": v["party"], "chambers": sorted(v["chambers"])}
                     for m, v in sorted(pairs, key=lambda mv: min(mv[1]["years"]))],
           "corr": {"all": corr(pairs), "before_1980": corr(early), "from_1980": corr(late)},
           "n": {"all": len(pairs), "before_1980": len(early), "from_1980": len(late)}}
    json.dump(out, open(os.path.join(DOCS, "congress.json"), "w"), separators=(",", ":"))
    print(f"congress: {len(pairs)} kept pairs; r = {out['corr']}")


if __name__ == "__main__":
    main()

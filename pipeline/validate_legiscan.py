"""Check this project's votes against LegiScan's, for the sessions both cover (the 81st Legislature, 2009, on).

LegiScan (legiscan.com, CC BY 4.0) parses the same journals independently, so agreement is a test of our
parsing and name matching. Its legislators are matched to ours by chamber, legislature, district and surname;
its roll calls to ours by chamber, date, yea and nay totals and, where several share those, bill number. For
each matched roll call, every member in both is compared yea for yea and nay for nay.

The API key is read from ~/.config/texas-nominate/legiscan.key (never from the repository). Datasets are
cached in data/raw/legiscan/. Writes data/processed/validation_legiscan.json and, for review,
data/processed/validation_legiscan_disagreements.csv.
Usage: python pipeline/validate_legiscan.py
"""
import base64, collections, csv, gzip, io, json, os, re, sys, urllib.parse, urllib.request, zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
RAW = os.path.join(ROOT, "data", "raw", "legiscan")
KEY_FILE = os.path.expanduser("~/.config/texas-nominate/legiscan.key")
API = "https://api.legiscan.com/"
GOOD = {"ok", "near", "ok_digit", "ok_lists", "ok_rest"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:3128")}))


def call(key, **params):
    url = API + "?" + urllib.parse.urlencode({"key": key, **params})
    body = json.loads(opener.open(urllib.request.Request(url, headers={"User-Agent": "texas-nominate validation"}), timeout=300).read())
    if body.get("status") != "OK":
        raise RuntimeError(f"LegiScan {params.get('op')}: {body.get('alert', body)}")
    return body


def datasets(key):
    """{session_id: (session info, {path: json}) } for every Texas session from 2009 on, cached as zips."""
    os.makedirs(RAW, exist_ok=True)
    listing = call(key, op="getDatasetList", state="TX")["datasetlist"]
    out = {}
    for d in listing:
        if d["year_start"] < 2009:
            continue
        path = os.path.join(RAW, f"{d['session_id']}_{d['dataset_hash']}.zip")
        if not os.path.exists(path):
            ds = call(key, op="getDataset", id=d["session_id"], access_key=d["access_key"])["dataset"]
            open(path, "wb").write(base64.b64decode(ds["zip"]))
        z = zipfile.ZipFile(path)
        out[d["session_id"]] = (d, {n: json.loads(z.read(n)) for n in z.namelist() if n.endswith(".json")})
    return out


def surname(s):
    toks = [t for t in re.findall(r"[a-z]+", s.lower()) if t not in ("jr", "sr", "ii", "iii", "iv")]
    return toks[-1] if toks else ""


def main():
    if not os.path.exists(KEY_FILE):
        sys.exit(f"no LegiScan API key: put it in {KEY_FILE}")
    key = open(KEY_FILE).read().strip()
    people = {int(r["member_id"]): r for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    terms = collections.defaultdict(list)                       # (chamber, leg, district) -> members
    for t in csv.DictReader(open(os.path.join(PROC, "terms.csv"))):
        terms[(t["chamber"], int(t["leg"]), t["district"].lstrip("0"))].append(int(t["member_id"]))
    ours = collections.defaultdict(list)                        # (chamber, date, yeas, nays) -> roll calls
    rows = {}
    for r in csv.DictReader(open(os.path.join(PROC, "rollcalls.csv"))):
        if r["status"] in GOOD and int(r["leg"]) >= 81:
            ours[(r["chamber"], r["date"][:10], int(r["n_y"]), int(r["n_n"]))].append(r["rc_id"])
            rows[r["rc_id"]] = r
    votes = collections.defaultdict(dict)
    with gzip.open(os.path.join(PROC, "votes.csv.gz"), "rt") as f:
        rd = csv.reader(f); next(rd)
        for rc, m, v in rd:
            if rc in rows:
                votes[rc][int(m)] = v

    stats = collections.Counter()
    disagree = []
    per_session = {}
    for sid, (info, files) in sorted(datasets(key).items()):
        leg = int(re.search(r"\d+", info["session_name"]).group())
        who = {}
        for n, j in files.items():
            if "/people/" in n:
                p = j["person"]
                ch = "H" if p["role"] == "Rep" else "S" if p["role"] == "Sen" else None
                dist = re.sub(r"^[HS]D-0*", "", p.get("district", ""))
                cands = [m for m in terms.get((ch, leg, dist), []) if surname(people[m]["name"]) == surname(p["last_name"] or p["name"])]
                cands = cands or terms.get((ch, leg, dist), [])
                if len(cands) == 1:
                    who[p["people_id"]] = cands[0]
                stats["people"] += 1; stats["people_matched"] += len(cands) == 1
        s = collections.Counter()
        for n, j in files.items():
            if "/vote/" not in n:
                continue
            rc = j["roll_call"]
            ch = "H" if rc["chamber"] == "H" else "S"
            s["legiscan_rollcalls"] += 1
            cands = ours.get((ch, rc["date"], rc["yea"], rc["nay"]), [])
            if not cands:
                continue
            s["matched_rollcalls"] += 1
            # several of ours with that day and tally: the one whose members agree most
            best = max(cands, key=lambda c: sum(1 for v in rc["votes"] if who.get(v["people_id"]) in votes[c]
                                               and votes[c][who[v["people_id"]]] == {1: "Y", 2: "N"}.get(v["vote_id"])))
            for v in rc["votes"]:
                m = who.get(v["people_id"])
                theirs = {1: "Y", 2: "N"}.get(v["vote_id"])
                mine = votes[best].get(m) if m else None
                if theirs and mine in ("Y", "N"):
                    s["votes_compared"] += 1
                    if theirs == mine:
                        s["votes_agree"] += 1
                    else:
                        disagree.append((best, rows[best]["date"], m, people[m]["name"], mine, theirs, rc["roll_call_id"], rc["desc"][:80]))
        per_session[info["session_name"]] = dict(s)
        stats.update(s)
    out = {"sessions": per_session, "totals": dict(stats),
           "agreement": round(stats["votes_agree"] / max(stats["votes_compared"], 1), 5),
           "rollcalls_matched_share": round(stats["matched_rollcalls"] / max(stats["legiscan_rollcalls"], 1), 4)}
    json.dump(out, open(os.path.join(PROC, "validation_legiscan.json"), "w"), indent=1)
    with open(os.path.join(PROC, "validation_legiscan_disagreements.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["rc_id", "date", "member_id", "name", "ours", "legiscan", "legiscan_roll_call_id", "legiscan_desc"])
        w.writerows(disagree)
    print(json.dumps(out["totals"]), "agreement", out["agreement"], "roll calls matched", out["rollcalls_matched_share"])


if __name__ == "__main__":
    main()

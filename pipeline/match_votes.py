"""Resolve journal names to members and check every roll call against its printed totals.

The journals name members by surname, adding an initial when two share one ("Bell, C." in the
House, "A. Hinojosa" in the Senate), and "Mr. Speaker" for the Speaker. Each name is matched to the
roster for that legislature and chamber by surname (multi-word surnames included), initial, and
service dates. Within a session, a name that can only be one member rules that member out for
other names ("Ward Johnson" settles who plain "Johnson" is).

The Senate often prints only the nays and absentees ("Yeas 29, Nays 1. Nays: Wentworth."); the
yeas are then everyone serving that day who isn't listed elsewhere, and the result must equal the
printed count. "Same as previous roll call" copies the previous roll call's lists.

A roll call is "ok" only if every name resolved and the yea and nay counts equal the printed
totals. Writes data/processed/rollcalls.csv, votes.csv.gz and match_report.txt.
"""
import collections, csv, gzip, json, os, re, sys, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_journals import bill_ref

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INTERIM = os.environ.get("PARSE_OUT", os.path.join(ROOT, "data", "interim"))
PROC = os.path.join(ROOT, "data", "processed")
OUT = os.environ.get("MATCH_OUT", PROC)          # PARSE_OUT / MATCH_OUT: a trial run elsewhere
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
CODES = {"Y", "N", "P", "E", "M", "A"}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[-.'’`]", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


def name_keys(display, full):
    """For a roster member: strong surname keys (the last 1-3 words), weak keys (any run of words
    after the first, for journals that use part of a compound name, e.g. "Neave" for Victoria Neave
    Criado), and the name token lists and nicknames used to work out initials."""
    strong, weak, toklists, nicks = set(), set(), [], []
    for raw in (display, full):
        nicks += [norm(n) for n in re.findall(r"[\"“”]([^\"“”]+)[\"“”]", raw) if norm(n)]
        base = re.sub(r"[\"“”][^\"“”]+[\"“”]", " ", raw)
        toks = [t for t in norm(base).split() if t not in SUFFIXES]
        if not toks:
            continue
        toklists.append(toks)
        for k in range(1, min(3, len(toks) - 1) + 1 if len(toks) > 1 else 2):
            strong.add(" ".join(toks[-k:]))
        for i in range(1, len(toks)):
            for j in range(i + 1, len(toks) + 1):
                if len(" ".join(toks[i:j])) >= 3:
                    weak.add(" ".join(toks[i:j]))
    return strong, weak - strong, toklists, nicks


def initial_seqs(toklists, nicks, surname):
    """Initials of the given names, i.e. the words before the surname the journal used:
    Caroline Esther Harris Davila matched as 'harris' -> {'c', 'ce'}."""
    sn = surname.split()
    seqs = set()
    for toks in toklists:
        idx = next((i for i in range(len(toks) - len(sn), 0, -1) if toks[i:i + len(sn)] == sn), len(toks) - 1)
        given = toks[:idx] or toks[:1]
        seqs.add("".join(t[0] for t in given))
        for n in nicks:
            seqs.add(n[0] + "".join(t[0] for t in given[1:]))
            seqs.add(n[0])
    return seqs


def parse_journal_name(chamber, raw):
    """-> (surname, initials, county, chair). 'Bell, C.' -> ('bell', 'c', None, False);
    'Johnson, J.D.' -> initials 'jd'; 'A. Hinojosa' -> ('hinojosa', 'a'); 'Taylor of Collin' ->
    county 'collin'; 'Harris(C)' -> chair (presiding, not voting)."""
    chair = bool(re.search(r"\(C\)\s*$", raw))
    s = re.sub(r"\(C\)\s*$", "", raw).strip()
    if re.match(r"^(Mr\.?|Madam|Mrs\.?)\s+Speaker$", s, re.I):
        return "SPEAKER", None, None, chair
    if re.match(r"^(Mr\.?|Madam|Mrs\.?)\s+President$", s, re.I):
        return "PRESIDENT", None, None, chair
    initials, county = None, None
    m = re.match(r"^(.*?)\s+of\s+([A-Z][A-Za-z .'-]+)$", s)
    if m:
        s, county = m.group(1), norm(m.group(2))
    m = re.match(r"^(.*?),\s*((?:[A-Z]\.?\s*){1,3})$", s)          # House: "Davis, Y.", "Johnson, J.D."
    if m:
        s, initials = m.group(1), re.sub(r"[^a-z]", "", m.group(2).lower())
    else:
        m = re.match(r"^((?:[A-Z]\.\s*){1,2})\s*(\S.*)$", s)         # Senate: "J. Hinojosa"
        if m:
            initials, s = re.sub(r"[^a-z]", "", m.group(1).lower()), m.group(2)
    return norm(s), initials, county, chair


def initial_score(ini, seqs):
    """How well journal initials ('cj') fit a member's initial sequences ({'c', 'ce'}): the longest
    agreeing prefix, or -1 if any sequence with the same first letter disagrees later on."""
    best = 0
    for seq in seqs:
        if not seq or seq[0] != ini[0]:
            continue
        n = min(len(seq), len(ini))
        if seq[:n] != ini[:n]:
            return -1
        best = max(best, n)
    return best


def load_roster():
    people = {int(r["member_id"]): r for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    terms = collections.defaultdict(list)
    for t in csv.DictReader(open(os.path.join(PROC, "terms.csv"))):
        t["member_id"] = int(t["member_id"])
        p = people[t["member_id"]]
        t["surn"], t["weak"], t["toks"], t["nicks"] = name_keys(p["name"], p["full_name"])
        t["places"] = {norm(x) for x in re.split(r"[,/;]", ",".join([t["counties"], t["county"], t["city"]])) if norm(x)}
        terms[(t["chamber"], int(t["leg"]))].append(t)
    aliases = {}
    for a in csv.DictReader(open(os.path.join(ROOT, "pipeline", "aliases.csv"))):
        aliases[(a["chamber"], int(a["leg"]) if a["leg"] else None, a["journal_name"])] = int(a["member_id"])
    return people, terms, aliases


def serving(t, date):
    return (not t["start"] or t["start"] <= date) and (not t["end"] or date <= t["end"])


def resolve_session(names, roster, fuzzy_log, reserved=()):
    """names: set of (surname, initials, county). Returns {name: [candidate terms]}.
    reserved: members the journal refers to some other way (e.g. as "Mr. Speaker")."""
    from rapidfuzz import fuzz
    cand = {}
    for key in names:
        sn, ini, county = key
        strong = [t for t in roster if sn in t["surn"]]
        weak = [t for t in roster if sn in t["weak"]]
        c = strong or weak
        # with initials, weaker surname matches compete too ("Harris, C.E." is Caroline Harris Davila)
        pool = strong + weak if ini else c
        if ini and len({t["member_id"] for t in pool}) > 1:
            scored = [(initial_score(ini, initial_seqs(t["toks"], t["nicks"], sn)), t) for t in pool]
            top = max(sc for sc, _ in scored)
            if top > 0:
                c = [t for sc, t in scored if sc == top]
        if county and len({t["member_id"] for t in c}) > 1:
            c = [t for t in c if county in t["places"]] or c
        if not c:
            from match_scans import fold        # OCR-confusion folding: "Hom"/"horn", "Oark"/"clark"
            best = {}
            for t in roster:
                score = max(max(fuzz.ratio(sn, k), fuzz.ratio(fold(sn), fold(k))) for k in t["surn"])
                best[t["member_id"]] = max(best.get(t["member_id"], (0, None)), (score, t), key=lambda x: x[0])
            ranked = sorted(best.values(), key=lambda x: -x[0])
            if ranked and ranked[0][0] >= 75 and (len(ranked) == 1 or ranked[1][0] <= ranked[0][0] - 6):
                c = [ranked[0][1]]
                fuzzy_log.append((sn, ini, ranked[0][1]["member_id"], ranked[0][0]))
        cand[key] = c
    # a name that can only be one member rules that member out for every other name
    changed = True
    while changed:
        changed = False
        fixed = {k: c[0]["member_id"] for k, c in cand.items() if len({t["member_id"] for t in c}) == 1}
        for k, c in cand.items():
            ids = {t["member_id"] for t in c}
            if len(ids) > 1:
                for taken in (set(reserved), {m for kk, m in fixed.items() if kk != k}):
                    keep = [t for t in c if t["member_id"] not in taken]
                    if keep and len(keep) < len(c):
                        cand[k], c, changed = keep, keep, True
    return cand


def main():
    people, terms, aliases = load_roster()
    raw = [json.loads(l) for l in open(os.path.join(INTERIM, "rollcalls_raw.jsonl"))]
    sessions = collections.defaultdict(list)
    for rv in raw:
        sessions[(rv["chamber"], rv["leg"], rv["session"])].append(rv)

    rc_rows, vote_rows, report, fuzzy_log = [], [], [], []
    status_count = collections.Counter()
    for (chamber, leg, sess), rvs in sorted(sessions.items(), key=lambda x: (x[0][1], x[0][0], x[0][2])):
        roster = terms.get((chamber, leg), [])
        speaker = [t for t in roster if chamber == "H" and re.match(r"^Speaker\b(?!\s+pro)", t["note"], re.I)]
        if chamber == "H" and not speaker:          # roster rows without notes: use the Library's list of Speakers
            from match_scans import speakers as speaker_list
            ids = {m for m, a, b in speaker_list() if any(a <= r["date"] <= b for r in rvs)}
            speaker = [t for t in roster if t["member_id"] in ids]
        alias = {n: mid for (ch, lg, n), mid in aliases.items() if ch == chamber and lg in (None, leg)}
        parsed = {}
        for rv in rvs:
            for code, lst in rv["lists"].items():
                for n in lst:
                    parsed[n] = parse_journal_name(chamber, n)
        special = {"SPEAKER", "PRESIDENT"}
        cand = resolve_session({p[:3] for n, p in parsed.items() if p[0] not in special and re.sub(r"\(C\)$", "", n) not in alias},
                               roster, fuzzy_log, reserved={t["member_id"] for t in speaker})
        by_day = {}
        rvs.sort(key=lambda r: (r["date"], r["file"], r.get("record") or 0, r.get("seq") or 0))
        # scanned years (before 1995): OCR'd names go through the scan matcher instead
        scanned = leg < 74
        if scanned:
            from match_scans import SessionMatcher, speakers as speaker_list, presidents_pro_tem, chair_on
            sm = SessionMatcher(roster)
            chairs = speaker_list() if chamber == "H" else presidents_pro_tem()

            def scan_match(rv):
                sm.set_speaker([m for m, a, b in chairs if a <= rv["date"] <= b] if chamber == "H" else chair_on(chairs, rv["date"]))
                # names in aliases.csv (a member printed under another name) bypass the fuzzy matcher
                bare = lambda n: re.sub(r"\(C\)$", "", n).strip()
                votes, unmatched = sm.match({code: [n for n in lst if bare(n) not in alias] for code, lst in rv["lists"].items()})
                votes.update({alias[bare(n)]: code for code, lst in rv["lists"].items() for n in lst if bare(n) in alias})
                return votes, unmatched
            sm.learn_activity([scan_match(rv)[0] for rv in rvs if "members" not in rv]); sm.cache = {}
        for n_in_session, rv in enumerate(rvs, 1):
            date = rv["date"]
            votes, problems = {}, []
            if rv.get("same_as") and (rv["file"], rv["same_as"]) in by_day:
                votes = dict(by_day[(rv["file"], rv["same_as"])])
            if scanned and "members" in rv:
                # a tally sheet's rows, resolved to members across the session's sheets by parse_journals
                votes.update({int(m): code for m, code in rv["members"].items()})
            elif scanned:
                got, unmatched = scan_match(rv)
                votes.update(got)
                problems = [f"unmatched: {u}" for u in unmatched]
            for code, lst in ({} if scanned else rv["lists"]).items():
                for n in lst:
                    sn, ini, county, chair = parsed[n]
                    bare = re.sub(r"\(C\)$", "", n).strip()
                    if bare in alias:
                        c = [t for t in roster if t["member_id"] == alias[bare]]
                    elif sn == "SPEAKER":
                        c = [t for t in speaker if serving(t, date)] or speaker
                    elif sn == "PRESIDENT":
                        c = []
                    else:
                        c = [t for t in cand.get((sn, ini, county), []) if serving(t, date)] or cand.get((sn, ini, county), [])
                    ids = {t["member_id"] for t in c}
                    if len(ids) != 1:
                        problems.append(f"{'unmatched' if not ids else 'ambiguous'}: {n}")
                        continue
                    mid = ids.pop()
                    if mid in votes and not rv.get("same_as"):
                        problems.append(f"collision: {n} (member {mid} already listed)")
                        continue
                    votes[mid] = code
            # Senate: yeas (or nays) that weren't printed are everyone else serving that day
            if chamber == "S" and not problems:
                listed = collections.Counter(votes.values())
                for code in ("Y", "N"):
                    other = "N" if code == "Y" else "Y"
                    if rv["printed"][code] > 0 and listed[code] == 0 and (listed[other] == rv["printed"][other]):
                        rest = [t["member_id"] for t in roster if serving(t, date) and t["member_id"] not in votes]
                        rest = sorted(set(rest))
                        if len(rest) == rv["printed"][code]:
                            for mid in rest:
                                votes[mid] = code
                            rv["implied"] = code
            ny = sum(1 for v in votes.values() if v == "Y")
            nn = sum(1 for v in votes.values() if v == "N")
            if problems:
                status = "unresolved_names"
            elif ny != rv["printed"]["Y"] or nn != rv["printed"]["N"]:
                status = "count_mismatch"
            else:
                status = "ok"
            status_count[(chamber, status)] += 1
            if status != "ok":
                report.append(f"{leg}{sess} {chamber} {date} rec={rv.get('record')} seq={rv.get('seq')} printed={rv['printed']} got Y={ny} N={nn} {status} {'; '.join(problems[:4])}")
            if chamber == "H" and "members" in rv:      # tally sheets: record numbers restart every day
                rc_id = f"{leg}{sess}-HT{n_in_session:04d}"
            elif chamber == "H":
                rc_id = f"{leg}{sess}-H{rv['record']:04d}"
            else:
                rc_id = f"{leg}{sess}-S{n_in_session:04d}"
            if rv.get("seq"):
                by_day[(rv["file"], rv["seq"])] = votes
            rc_rows.append({"rc_id": rc_id, "chamber": chamber, "leg": leg, "session": sess, "date": date,
                            "record": rv.get("record") or "", "bill": rv.get("bill") or bill_ref(rv.get("text") or "") or bill_ref(rv.get("motion") or "") or bill_ref(rv.get("amendment") or "") or "",
                            "caption": rv.get("caption") or "", "heading": rv.get("heading") or "",
                            "text": rv.get("text") or "", "motion": rv.get("motion") or "",
                            "amendment": rv.get("amendment") or "", "printed_y": rv["printed"]["Y"],
                            "printed_n": rv["printed"]["N"], "printed_p": rv["printed"]["P"], "n_y": ny, "n_n": nn,
                            "implied": rv.get("implied", ""), "status": status, "file": rv["file"]})
            for mid, code in votes.items():
                vote_rows.append((rc_id, mid, code))

    with open(os.path.join(OUT, "rollcalls.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, list(rc_rows[0].keys()))
        w.writeheader(); w.writerows(rc_rows)
    with gzip.open(os.path.join(OUT, "votes.csv.gz"), "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rc_id", "member_id", "vote"]); w.writerows(vote_rows)
    with open(os.path.join(OUT, "match_report.txt"), "w") as f:
        f.write("STATUS\n" + "\n".join(f"{k}: {v}" for k, v in sorted(status_count.items())) + "\n\nFUZZY MATCHES\n")
        f.write("\n".join(f"{sn!r} {ini} -> {people[mid]['name']} ({score:.0f})" for sn, ini, mid, score in sorted(set(fuzzy_log), key=lambda x: (x[0], x[1] or "", str(x[2]), x[3]))) + "\n\nPROBLEMS\n")
        f.write("\n".join(report) + "\n")
    print("\n".join(f"{k}: {v}" for k, v in sorted(status_count.items())))
    print(f"fuzzy matches: {len(set(fuzzy_log))}; votes: {len(vote_rows):,}")


if __name__ == "__main__":
    main()

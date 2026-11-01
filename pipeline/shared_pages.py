"""Roll calls on a page that two days' journal files share.

The Library's day files overlap: a day's last page is usually the next day's first, one day ending and the next
beginning on it, and some files run several pages into the next day's (1927 House: March 7's file holds twelve of
March 8's pages), so a roll call there was read from each file and counted for each day (v12: 1,559 contested roll
calls twice). For each page with roll calls that more than one file holds (the same page: its text in each file
matched; the 1853-54 journals number their pages afresh partway, so a page number recurs months later), the
days that begin on it are found in turn down the page: each one's "...DAY." heading; else a short dateline with
its date ("Wednesday, February 18, 1863"); else a "House met" / "Senate met" line. ("Senate Chamber," won't do:
committee reports carry it in their datelines.) A roll call above the first is the day's the page opens with (the
latest one whose own pages include it), one below it the next day's, and so on. Readings of one roll call from
different files (the same place on the page; or, contested, the same totals and nine in ten of the same votes)
are kept once, the best verified (else the one from the file of its day), and each roll call there is dated and
filed to its day. Where a day's start isn't found, a roll call read twice is still kept once (the earliest file's
reading when as good), but none past the last day start found is moved.

Rewrites rollcalls_hist.csv and votes_hist.csv.gz in place (HIST_OUT, default data/processed), keeping the
originals as *.pre_shared; writes shared_pages_report.txt beside them. Run after extract_history.py and before
check_terms.py.

`records`: the same for the record-vote route (rollcalls.csv, votes.csv.gz in data/processed), where a record
vote's number is its id, so a vote read from two files is one id twice (1985 House: the April 10 file runs 22
pages into April 11's). Kept once: from the later day's file when the earlier one runs more than a page into it,
else the earlier's; a failing copy beside a verified one goes. An id whose copies give members different votes
is two roll calls under one misread number, their votes mixed: neither is kept.
Usage: HIST_OUT=data/raw/v13_stage/processed python pipeline/shared_pages.py; python pipeline/shared_pages.py records
"""
import collections, csv, gzip, html, json, os, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_layout
from extract_history import fix_dates, RAW

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.environ.get("HIST_OUT", os.path.join(ROOT, "data", "processed"))
RANK = ["ok", "near", "ok_digit", "ok_lists", "ok_rest"]           # better first; anything else after
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
# the ordinal OCR'd any which way ("SEOONDDAY.", "FOURTEEl\"TH DAY.", "l'Il'TII DAY."), its DAY in capitals, alone
DAY_HEAD = re.compile(r"^\W{0,4}(?:(?=\S*[A-Z]\S*[A-Z])\S{3,25}(?:[\s-]\S{1,14})?\s?D\s?A\s?Y(?:'?S\s+PROCEEDINGS)?"
                      r"|(?:[A-Z][a-z]+-)?[A-Z][a-z]+(?:st|nd|rd|th)\s+Day(?:'s\s+Proceedings)?)\W{0,4}$")
MET = re.compile(r"(?i)(?:^|\s)(\S{3,8})\s+met\b")


def page_count(path):
    out = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def layer_page(path, i):
    """One page of the PDF's text layer, as scan_layout.pdf_pages gives it."""
    out = subprocess.run(["pdftotext", "-bbox", "-f", str(i + 1), "-l", str(i + 1), path, "-"],
                         capture_output=True, text=True).stdout
    m = re.search(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', out, re.S)
    if not m:
        return None
    words = [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in re.findall(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', m.group(3))]
    return float(m.group(1)), float(m.group(2)), words


def day_starts(lines, dates, before):
    """Where each of `dates` (the days that begin on a page, in order) begins, in reading order: a list of line
    indexes (None where not found, and for every day after one not found), with how each was found. Found in turn
    down the page: the day's "...DAY." heading; else a short dateline with its date (not when the day before has the
    same date: two legislative days in one, 1937 Senate); else a "House met" / "Senate met" line (for the last day,
    the last such line). `before`: the date of the day the page opens with (None if it opens with the first)."""
    from rapidfuzz import fuzz
    texts = [" ".join(c["text"] for c in ln["cells"]).strip() for ln in lines]
    heads = [k for k, t in enumerate(texts) if DAY_HEAD.match(t)]
    met = [k for k, t in enumerate(texts) for m in [MET.search(t)]
           if m and max(fuzz.ratio(m.group(1).lower().strip(".,;:"), w) for w in ("house", "senate")) >= 60]

    def dateline(date, at):
        month, day = MONTHS[int(date[5:7]) - 1], str(int(date[8:10]))
        for k in range(at, len(texts)):
            t = texts[k]
            # a dateline, not a sentence that mentions the date ("approved March 21, 1871, so as to"): little else on it
            words = [w for w in re.findall(r"[A-Za-z]{2,}", t) if fuzz.ratio(w.lower(), month) < 75
                     and w.lower() not in WEEKDAYS and max(fuzz.ratio(w.lower(), x) for x in ("austin", "texas")) < 70]
            if len(t) <= 70 and re.search(r"(?<!\d)" + day + r"(?!\d)", t) and len(words) <= 2 and \
                    any(fuzz.ratio(w.lower(), month) >= 75 for w in re.findall(r"[A-Za-z]{3,}", t)):
                return k
        return None
    out, at, prev = [], 0, before
    for i, date in enumerate(dates):
        h = [k for k in heads if k >= at]
        k, how = (h[0], "heading") if h else (None, None)
        if k is None and date != prev:
            k, how = dateline(date, at), "date"
        if k is None:
            m = [x for x in met if x >= at]
            k, how = ((m[-1] if i == len(dates) - 1 else m[0]), "met") if m else (None, None)
        if k is None:
            return out + [(None, None)] * (len(dates) - i)
        out.append((k, how))
        at, prev = k + 1, date
    return out


def position(lines, col, y):
    """Where a roll call's heading (col, y) falls in the page's reading order: the index of the nearest line in
    its column (a full-width one by height alone)."""
    same = [k for k, ln in enumerate(lines) if ln["col"] == col] or list(range(len(lines)))
    return min(same, key=lambda k: abs(lines[k]["y"] - y)) if same else 0


def main():
    import datetime
    rc_path, v_path = os.path.join(OUT, "rollcalls_hist.csv"), os.path.join(OUT, "votes_hist.csv.gz")
    rows = list(csv.DictReader(open(rc_path)))
    cols = list(rows[0].keys())
    with gzip.open(v_path, "rt") as f:
        rd = csv.reader(f)
        head = next(rd)
        votes = list(rd)
    placed = collections.defaultdict(dict)
    for rc, m, v in votes:
        placed[rc][m] = v
    by_file = collections.defaultdict(list)
    for r in rows:
        if r["source"] != "record" and r.get("y", "") != "":
            by_file[r["file"]].append(r)

    # every day file of each session holding a roll call: its first page's number, page count and (fixed) date
    index = json.load(open(os.path.join(RAW, "index.json")))
    sessions = collections.defaultdict(list)
    for d in index:
        if d.get("kind", "pdf") == "pdf" and str(d.get("pages") or "").isdigit():
            sessions[(d["chamber"], d["leg"], d["session"])].append(d)
    day, sess_of, files_of = {}, {}, collections.defaultdict(list)
    for key, days in sessions.items():
        if any(d["file"] in by_file for d in days):
            for d in fix_dates([dict(d) for d in days]):
                day[d["file"]] = d
                sess_of[d["file"]] = key
                files_of[key].append(d["file"])
    with ThreadPoolExecutor(8) as ex:
        n = dict(zip(day, ex.map(page_count, [os.path.join(RAW, f) for f in day])))
    start = {f: int(d["pages"]) for f, d in day.items()}
    def ordinal(s):
        try:
            return datetime.date.fromisoformat(s).toordinal()
        except ValueError:
            return None

    # the pages with roll calls that more than one file holds (no more than a week apart: the 1853-54 journals
    # number their pages afresh partway, so a page number recurs months later)
    groups = collections.defaultdict(list)
    for f, rs in by_file.items():
        if f in day:
            for r in rs:
                groups[(sess_of[f], start[f] + int(r["page"]))].append(r)
    holders = {}
    for (key, P), rs in groups.items():
        # (one page number, two pages: the files that read roll calls there, in runs of days a week apart at most)
        days_read = sorted({o for o in (ordinal(day[r["file"]]["date"]) for r in rs) if o})
        runs = [[days_read[0]]] if days_read else []
        for o in days_read[1:]:
            runs[-1].append(o) if o - runs[-1][-1] <= 7 else runs.append([o])
        for run in runs:
            F = [f for f in files_of[key] if n.get(f) and start[f] <= P < start[f] + n[f] and ordinal(day[f]["date"])
                 and run[0] - 7 <= ordinal(day[f]["date"]) <= run[-1] + 7]
            if len(F) > 1:
                holders[(key, P, run[0])] = F
    need = {(f, P - start[f]) for (key, P, _), F in holders.items() for f in F}
    with ThreadPoolExecutor(8) as ex:
        text = dict(zip(need, ex.map(lambda fp: layer_page(os.path.join(RAW, fp[0]), fp[1]), need)))
    words = lambda page: {w[4].lower() for w in page[2] if re.fullmatch(r"[A-Za-z]{3,}", w[4])} if page else set()
    sim = lambda A, B: len(A & B) / len(A | B) if A and B else 0.0

    def aligned(f, i, ref):
        """Is page i of file f the page `ref` holds? The same text layer, nearly always; or, where the files carry
        different OCR of it (1895 House: 151 words against 96), plainly nearer it than the pages either side."""
        x = sim(words(text[(f, i)]), ref)
        if x >= 0.5:
            return True
        if x < 0.2:
            return False
        others = [sim(words(layer_page(os.path.join(RAW, f), j)), ref) for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < n[f]]
        return x >= 2 * max(others, default=0)

    def alike(x, y):
        """Two files' readings of one roll call on the page: in the same place; or, contested, with the same totals
        and nine in ten of the same votes (unanimous ones by the same members are alike in everything)."""
        if x["col"] == y["col"] and abs(float(x["y"]) - float(y["y"])) <= 15:
            return 0
        A, B = placed[x["rc_id"]], placed[y["rc_id"]]
        if (x["printed_y"], x["printed_n"]) == (y["printed_y"], y["printed_n"]) and A and min(int(x["n_y"]), int(x["n_n"])) > 0 \
                and sum(1 for m, v in A.items() if B.get(m) == v) >= 0.9 * max(len(A), len(B)):
            return 1
        return None

    rank = lambda r: RANK.index(r["status"]) if r["status"] in RANK else len(RANK)
    drop, report, kinds, moved = set(), [], collections.Counter(), collections.Counter()
    for (key, P, _), F in sorted(holders.items()):
        # the same page in each (a file whose page numbers are off holds another page there), as in the file that
        # read the most roll calls on it
        most = max(F, key=lambda f: sum(1 for r in by_file.get(f, []) if start[f] + int(r["page"]) == P))
        ref = words(text[(most, P - start[most])])
        F = [f for f in F if f == most or aligned(f, P - start[f], ref)]
        if len(F) < 2 or not ref:
            continue
        F.sort(key=lambda f: (day[f]["date"], start[f]))
        opening = [f for f in F if start[f] < P]          # the day the page opens with ends here
        begins = [f for f in F if start[f] == P]          # the days that begin here
        top = opening[-1] if opening else None
        page = text[(F[0], P - start[F[0]])]
        lines = scan_layout.page_lines(page) if page else []
        found = day_starts(lines, [day[b]["date"] for b in begins], day[top]["date"] if top else None) if lines else \
            [(None, None)] * len(begins)
        segments = [(-1, top)] + [(k, b) for (k, how), b in zip(found, begins) if k is not None]
        complete = all(k is not None for k, _ in found)
        for _, how in found:
            kinds[how or "none"] += 1
        how = ",".join(h or "none" for _, h in found) or "opening"

        def owner(r):
            k = position(lines, r["col"], float(r["y"])) if lines else 0
            o = [f for s, f in segments if s <= k][-1]
            # past the last day start found, when a later one wasn't, the day can't be told
            return None if not complete and k > segments[-1][0] else o
        rs = [r for f in F for r in by_file.get(f, []) if start[f] + int(r["page"]) == P and r["rc_id"] not in drop]
        # readings of one roll call from different files: the closest pairs first, each file's reading in one group
        pairs = sorted((a, abs(float(x["y"]) - float(y["y"])), i, j) for i, x in enumerate(rs) for j, y in enumerate(rs)
                       if i < j and x["file"] != y["file"] for a in [alike(x, y)] if a is not None)
        group = list(range(len(rs)))
        find = lambda i: i if group[i] == i else find(group[i])
        members = {i: {rs[i]["file"]} for i in range(len(rs))}
        for _, _, i, j in pairs:
            gi, gj = find(i), find(j)
            if gi != gj and not members[gi] & members[gj]:
                group[gj] = gi
                members[gi] |= members.pop(gj)
        clusters = collections.defaultdict(list)
        for i in range(len(rs)):
            clusters[find(i)].append(rs[i])
        for c in clusters.values():
            best = min(c, key=rank)
            o = owner(best)
            keep = min(c, key=lambda r: (rank(r), r["file"] != (o or F[0])))
            for r in c:
                if r is not keep:
                    drop.add(r["rc_id"])
                    report.append(f"{r['rc_id']} {r['date']} {r['status']} {r['n_y']}-{r['n_n']} dropped: read again as "
                                  f"{keep['rc_id']} ({keep['status']}) [{how}]")
            if o is not None and keep["file"] != o:
                moved[day[o]["date"] < keep["date"]] += 1
                report.append(f"{keep['rc_id']} {keep['date']} -> {day[o]['date']} (on page {P}, {o}) [{how}]")
                keep["date"], keep["file"], keep["page"] = day[o]["date"], o, str(P - start[o])
    for p in (rc_path, v_path):
        shutil.copy2(p, p + ".pre_shared")
    with open(rc_path, "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(r for r in rows if r["rc_id"] not in drop)
    with gzip.open(v_path, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(head); w.writerows(v for v in votes if v[0] not in drop)
    open(os.path.join(OUT, "shared_pages_report.txt"), "w").write("\n".join(report) + "\n")
    print(f"shared_pages: {len(holders)} pages with roll calls in more than one file; day starts found by {dict(kinds)}; "
          f"{len(drop)} roll calls read twice dropped; {moved[True]} moved to an earlier day, {moved[False]} to a later")


def records():
    proc = os.environ.get("RECORDS_OUT", os.path.join(ROOT, "data", "processed"))
    rc_path, v_path = os.path.join(proc, "rollcalls.csv"), os.path.join(proc, "votes.csv.gz")
    rows = list(csv.DictReader(open(rc_path)))
    cols = list(rows[0].keys())
    with gzip.open(v_path, "rt") as f:
        rd = csv.reader(f)
        head = next(rd)
        votes = list(rd)
    ids = collections.defaultdict(list)
    for i, r in enumerate(rows):
        ids[r["rc_id"]].append(i)
    twice = {k for k, ix in ids.items() if len(ix) > 1}
    seen = collections.defaultdict(set)
    for rc, m, v in votes:
        if rc in twice:
            seen[(rc, m)].add(v)
    mixed = {rc for (rc, m), vs in seen.items() if len(vs) > 1}
    index = {d["file"]: d for d in json.load(open(os.path.join(RAW, "index.json")))}
    start = lambda f: int(index[f]["pages"]) if str(index.get(f, {}).get("pages") or "").isdigit() else None
    drop, report = set(), []
    for k in sorted(twice):
        ix = ids[k]
        if k in mixed:
            drop |= set(ix)
            report.append(f"{k}: {len(ix)} copies whose votes differ ({', '.join(rows[i]['date'][:10] for i in ix)}): none kept")
            continue
        good = [i for i in ix if rows[i]["status"] in RANK] or ix[:1]
        keep = good[0]
        if len(good) > 1:
            a, b = sorted(good[:2], key=lambda i: rows[i]["date"])
        if len(good) > 1 and rows[a]["file"] != rows[b]["file"] and start(rows[a]["file"]) is not None \
                and start(rows[b]["file"]) is not None:
            run_on = start(rows[a]["file"]) + page_count(os.path.join(RAW, rows[a]["file"])) - start(rows[b]["file"])
            keep = b if run_on > 1 else a
        drop |= set(ix) - {keep}
        report.append(f"{k}: kept the {rows[keep]['date'][:10]} copy ({rows[keep]['status']}) of {len(ix)}")
    out_rows = [r for i, r in enumerate(rows) if i not in drop]
    gone = {k for k in mixed}
    for p in (rc_path, v_path):
        shutil.copy2(p, p + ".pre_shared")
    with open(rc_path, "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(out_rows)
    written = set()
    with gzip.open(v_path, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(head)
        for v in votes:
            if v[0] in gone or (v[0], v[1]) in written:
                continue
            if v[0] in twice:
                written.add((v[0], v[1]))
            w.writerow(v)
    open(os.path.join(proc, "shared_records_report.txt"), "w").write("\n".join(report) + "\n")
    print(f"shared_pages records: {len(twice)} ids read more than once; {len(mixed)} with mixed votes dropped; "
          f"{len(drop)} rows dropped")


if __name__ == "__main__":
    records() if sys.argv[1:] == ["records"] else main()

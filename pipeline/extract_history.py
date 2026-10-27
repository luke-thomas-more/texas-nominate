"""Roll calls from the scanned journals (1846-1993), session by session, with a second read.

  1. Parse every day from the scans' own OCR text layer, match names, check the totals.
  2. Re-read with Tesseract the pages around each roll call that failed, and the pages that show
     more yea headings than roll calls found (every vote page of a day where the layer found
     none). The two OCRs fail differently (Tesseract is far better on 19th-century type, the
     layer on mid-century two-column lists), so a roll call that fails one often verifies with
     the other.
  3. Use the re-read's version of a roll call when it verifies, and add roll calls only the
     re-read found. Pages around roll calls still failing get a third read (each page column on
     its own, read row by row). Roll calls that verify under no reading are kept but marked, and
     are left out of the scaling and the site.

Writes data/processed/rollcalls_hist.csv, votes_hist.csv.gz and match_hist_report.txt.
Usage: python pipeline/extract_history.py [legs, e.g. 1,12,24 or 1-73]
"""
import collections, csv, fcntl, gzip, json, os, re, statistics, sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_journals, parse_scans, scan_layout
from match_scans import SessionMatcher, check, check_parts, speakers, presidents_pro_tem, chair_on, parse_raw
from match_votes import load_roster

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "journals")
OUT = os.path.join(ROOT, "data", "processed")
GOOD = ("ok", "near", "ok_digit", "ok_lists")       # statuses that count as verified
MARK = re.compile(r"\byeas\b|\bnays\b|following vote", re.I)


# the Tesseract reads, in order: automatic layout (psm 3), then each page column on its own, read row
# by row (psm 6; nearly clean on two-column vote lists). A further read of the whole page in
# single-column mode ("tesseract4", psm 4) rescued 6 more roll calls of 1,148 in 1921 for 43% more
# OCR time, and none in 1859, so it isn't run.
READS = ("tesseract", "columns")
# re-read only the pages around failures (below) instead of every vote page of a failing day: 23%
# less OCR in 1921 and 36% in 1949, for 0.4% and 0.7% fewer verified roll calls
TARGETED = True


def ocr_read(source, path, pages):
    """One Tesseract read of a day's vote pages (only `pages`, if given); returns how many pages it covers."""
    if source == "columns":
        scan_layout.column_pages(path, pages=pages)
    else:
        scan_layout.tesseract_pages(path, psm="4" if source == "tesseract4" else "3", pages=pages)
    votes = scan_layout.vote_pages(path)
    return len(votes if pages is None else votes & set(pages))


def mentions_votes(path):
    return bool(MARK.search(" ".join(w[4] for _, _, ws in scan_layout.pdf_pages(path) for w in ws)))


DIGIT_CONFUSION = {"0": "0689", "1": "174", "2": "27", "3": "38", "4": "41", "5": "568", "6": "6580", "7": "71",
                   "8": "83560", "9": "908"}


def confusable(printed, got):
    """Equal, or one digit apart in a way OCR commonly confuses (3/8, 5/6/8, 1/7, ...)."""
    p, g = str(printed), str(got)
    if p == g:
        return True
    diff = [(a, b) for a, b in zip(p, g) if a != b]
    return len(p) == len(g) and len(diff) == 1 and diff[0][1] in DIGIT_CONFUSION.get(diff[0][0], "")


def layer_shift(layer_words, image_words):
    """How far below the image the scan's own text layer sits on one page, in points (it varies page
    to page, up to 50 or so in 1921, and the layer is often off sideways too; the scale matches):
    the median over words each reading has exactly once. 0 when too few match."""
    def once(ws):
        n = collections.Counter(w[4] for w in ws if len(w[4]) >= 6)
        return {w[4]: w for w in ws if n[w[4]] == 1}
    a, b = once(layer_words), once(image_words)
    d = [(a[k][1] + a[k][3] - b[k][1] - b[k][3]) / 2 for k in a.keys() & b.keys()]
    return statistics.median(d) if len(d) >= 5 else 0.0


def same_spot(a, b):
    """Are readings a and b one roll call? Its heading in the same spot on the same page. (Two roll
    calls can share totals and voters, a run of unanimous votes; two readings of one total can
    differ, one OCR's misread "36" checking against the other's "35".)"""
    (ca, ya), (cb, yb) = a["at"], b["at"]
    return a["page_in_day"] == b["page_in_day"] and abs(ya - yb) <= 10 and (ca == cb or "F" in (ca, cb))


def fragment(a, b):
    """Is reading a a piece of roll call b? Its yea heading where b has another list's heading (an
    OCR that takes "Nays—13" for a yea heading splits one roll call in two)."""
    col, y = a["at"]
    return any(code != "Y" and page == a["page_in_day"] and abs(y - ly) <= 10 and (col == lcol or "F" in (col, lcol))
               for code, page, lcol, ly in b["labels"])


def cross_reference(a, b):
    """Give each roll call the other reading's printed totals for the same roll call: two OCRs of
    the same digits ("35" vs "85")."""
    for x in a:
        for y in b:
            if same_spot(x, y):
                x.setdefault("alt_printed", []).append(dict(y["printed"]))     # keep every other reading
                y.setdefault("alt_printed", []).append(dict(x["printed"]))


def fix_dates(days):
    """The Library's file names occasionally carry the wrong year ("01181857_594.pdf" sits among
    January 1858 pages). Walk the session in page order; a date that falls before the previous day
    is moved forward a year when that puts it in order."""
    days = sorted(days, key=lambda d: int(d.get("pages") or 0))
    for i, d in enumerate(days):
        if i and d["date"] < days[i - 1]["date"]:
            y = int(d["date"][:4]) + 1
            fixed = f"{y}{d['date'][4:]}"
            nxt = days[i + 1]["date"] if i + 1 < len(days) else "9999"
            if days[i - 1]["date"] <= fixed <= max(nxt, fixed):
                d["date"] = fixed
    return days


def run_session(days, roster, workers):
    paths = {d["file"]: os.path.join(RAW, d["file"]) for d in days}
    layer = {d["file"]: parse_scans.parse_day(d, paths[d["file"]], ocr="layer") for d in days}
    sm = SessionMatcher(roster)
    house = days[0]["chamber"] == "H"
    chairs = speakers() if house else presidents_pro_tem()

    def match(d, rv):
        sm.set_speaker([m for m, a, b in chairs if a <= d["date"] <= b] if house else chair_on(chairs, d["date"]))
        return sm.match(rv["lists"])
    day_of = {d["file"]: d for d in days}
    sm.learn_activity([match(day_of[f], rv)[0] for f, rvs in layer.items() for rv in rvs]); sm.cache = {}

    memo = {}

    def verifies(rv, d=None):
        # keyed by object id, so the memo keeps each roll call alive: a freed one's id can be reused
        key = (id(rv), float(sm.active.sum()))
        if key not in memo:
            memo[key] = (rv, check(rv, match(d or day_of[rv["file"]], rv)[0])[2])
        return memo[key][1]

    marks = {}

    def reread(f, rvs):
        """Pages of a day to read again: around each roll call still failing (another reading can
        put its start a page earlier, and lists run onto the next page), and each page that mentions
        a vote but where fewer roll calls were found than it shows yea headings, or none at all (the
        layer garbles some headings beyond recognition); every vote page (None) when the day's
        reading found no roll call at all."""
        if not rvs or not TARGETED:
            return None
        if f not in marks:
            marks[f] = scan_layout.vote_marks(paths[f])
        pages = {p for rv in rvs if not verifies(rv) for p in range(rv["page_in_day"] - 1, rv["page_in_day"] + 2)}
        found = collections.Counter(rv["page_in_day"] for rv in rvs)
        missed = {p for p, n in marks[f].items() if not found[p] or n > found[p]}
        return pages | {q for p in missed for q in (p, p + 1)}

    def read_all(source, todo):
        with ProcessPoolExecutor(workers) as ex:
            return sum(ex.map(ocr_read, [source] * len(todo), [paths[f] for f in todo], list(todo.values())))

    retry = {f: reread(f, rvs) for f, rvs in layer.items()
             if any(not verifies(rv) for rv in rvs) or (not rvs and mentions_votes(paths[f]))}
    page_reads = read_all(READS[0], retry) if retry else 0
    # put the layer's roll calls where the image has them, so readings can be told apart by position
    for f in retry:
        if layer[f]:
            lay, img = scan_layout.pdf_pages(paths[f]), scan_layout.tesseract_pages(paths[f], pages=retry[f])
            dy = {p: layer_shift(lay[p][2], img[p][2]) for p in
                  {rv["page_in_day"] for rv in layer[f]} | {lab[1] for rv in layer[f] for lab in rv["labels"]}}
            for rv in layer[f]:
                rv["at"] = (rv["at"][0], rv["at"][1] - dy[rv["page_in_day"]])
                rv["labels"] = [(code, p, col, y - dy[p]) for code, p, col, y in rv["labels"]]
    def yeas_nays(rv):
        votes = match(day_of[rv["file"]], rv)[0]
        return {m for m, v in votes.items() if v == "Y"}, {m for m, v in votes.items() if v == "N"}

    def merge(rvs, other):
        """Bring in verified roll calls from another reading: in place of the same roll call when that
        failed (its count may be the garbled part) or found fewer votes (a nay list missed), else as
        one the first reading missed; and drop what either reading took for a roll call that is only
        a piece of one."""
        for tv in other:
            if not verifies(tv) or any(verifies(rv) and fragment(tv, rv) for rv in rvs):
                continue
            same = [i for i, rv in enumerate(rvs) if same_spot(rv, tv)]
            if not same:
                rvs.append(tv)
            elif not verifies(rvs[same[0]]) or sum(map(len, yeas_nays(tv))) > sum(map(len, yeas_nays(rvs[same[0]]))):
                rvs[same[0]] = tv
            rvs[:] = [rv for rv in rvs if not fragment(rv, tv)]     # pieces of it taken for roll calls
        return rvs

    chosen = {}
    for d in days:
        f = d["file"]
        rvs = [dict(rv, source="layer") for rv in layer[f]]
        if f in retry:
            tess = [dict(tv, source=READS[0]) for tv in parse_scans.parse_day(d, paths[f], ocr=READS[0], pages=retry[f])]
            cross_reference(rvs, tess)
            # the day's better reading wins outright; then patch its failures from the other reading
            if sum(map(verifies, tess)) > sum(map(verifies, rvs)):
                rvs, other = tess, rvs
            else:
                other = tess
            rvs = merge(rvs, other)
        chosen[f] = rvs
    # more reads around the roll calls still failing
    for source in READS[1:]:
        again = {f: reread(f, rvs) for f, rvs in chosen.items() if f in retry and any(not verifies(rv) for rv in rvs)}
        if not again:
            break
        page_reads += read_all(source, again)
        for f, pages in again.items():
            more = [dict(tv, source=source) for tv in parse_scans.parse_day(day_of[f], paths[f], ocr=source, pages=pages)]
            cross_reference(chosen[f], more)
            chosen[f] = merge(chosen[f], more)
    for f in chosen:
        chosen[f] = sorted(chosen[f], key=lambda r: (r["page_in_day"], r["seq"]))
    # final pass: learn who's active from everything, then match for keeps
    sm.learn_activity([match(day_of[f], rv)[0] for f, rvs in chosen.items() for rv in rvs]); sm.cache = {}
    out = []
    for d in days:
        for rv in chosen[d["file"]]:
            votes, unmatched = match(d, rv)
            ny, nn, ok = check(rv, votes)
            out.append((d, rv, votes, unmatched, ny, nn, ok))
    return out, len(retry), page_reads


def main():
    legs = set(parse_scans.legs_arg(sys.argv[1])) if len(sys.argv) > 1 else set(range(1, 74))
    workers = int(os.environ.get("WORKERS", 8))
    people, terms, aliases = load_roster()
    index = json.load(open(os.path.join(RAW, "index.json")))
    sessions = collections.defaultdict(list)
    for d in index:
        if d["leg"] in legs and d["leg"] < 74 and os.path.exists(os.path.join(RAW, d["file"])) \
                and not (d["leg"] >= 60 and parse_journals.modern_format(d)):     # those go through the modern parser
            sessions[(d["chamber"], d["leg"], d["session"])].append(d)
    redo = {f"{leg}{sess}-{ch}" for ch, leg, sess in sessions}
    rows, votes_out, report = [], [], []
    status = collections.Counter()
    for (ch, leg, sess) in sorted(sessions, key=lambda k: (k[1], k[0], k[2])):
        days = sorted(fix_dates(sessions[(ch, leg, sess)]), key=lambda d: (d["date"], int(d.get("pages") or 0)))
        results, n_retry, page_reads = run_session(days, terms.get((ch, leg), []), workers)
        most = collections.defaultdict(int)                  # most members any roll call placed, per day
        for d, rv, votes, *_ in results:
            most[d["date"]] = max(most[d["date"]], len(votes))
        # how many members a complete roll (yeas through absentees) places in this session
        sizes = sorted(len(r[2]) for r in results)
        full = sizes[int(0.9 * (len(sizes) - 1))] if sizes else 0
        session_status = collections.Counter()
        for n, (d, rv, votes, unmatched, ny, nn, ok) in enumerate(results, 1):
            rc_id = f"{leg}{sess}-{ch}x{n:04d}"           # "x": read from the scans
            py, pn = rv["printed"].get("Y"), rv["printed"].get("N") or 0
            # names left unresolved in the yea or nay lists (junk caught in the absentee lists doesn't matter;
            # nor does a "Mr. President" no pro tem on the roster can be, when one is listed by name)
            yn_raw = " | ".join(rv["lists"].get("Y", []) + rv["lists"].get("N", []))
            unresolved = [u for u in unmatched if u in yn_raw and parse_raw(u)[3] is None]
            # every name resolved and each total within one of the printed one: the journal's own
            # count is sometimes off (1846: 22 names under "Yeas—23"); at worst a vote is missing
            near = (not ok and not unresolved and py is not None and abs(ny - py) <= 1 and abs(nn - pn) <= 1
                    and ny + nn >= 4)
            named = sum(len(x) for x in rv["lists"].values())
            # the printed total misread by one OCR'd digit ("84" for 54): accepted only when every name
            # resolved, the lists account for the whole chamber that day, and a nay list was found
            # (a missed "Nays" heading would otherwise pass for a misread yea total)
            digit = (not ok and not near and not unresolved and py is not None and len(votes) >= 10
                     and len(votes) >= most[d["date"]] - 1 and not (nn == 0 and pn == 0 and ny != py)
                     and confusable(py, ny) and confusable(pn, nn))
            # the printed yea total itself misprinted (1921: "Yeas—117." over 110 names, with every member
            # of the House listed once): accepted only when every name resolved, the nay total checks
            # against a real nay list, and the lists account for the whole chamber, so no name is missing
            # and none came from another list (absentees and the rest are printed after the nays)
            misprint = (not ok and not near and not digit and not unresolved and py is not None and pn > 0
                        and check_parts(rv, votes)[3] and full >= 20 and len(votes) >= full - 1)
            st = "ok" if ok else "near" if near else "ok_digit" if digit else "ok_lists" if misprint \
                else "tally_only" if named == 0 else "count_mismatch"
            status[(ch, leg, st)] += 1
            session_status[st] += 1
            if not ok:
                report.append(f"{rc_id} {st} {d['file']} p{rv['page_in_day']} [{rv['source']}] printed Y{rv['printed'].get('Y')} "
                              f"N{rv['printed'].get('N')} got Y{ny} N{nn} unmatched: {'; '.join(unmatched[:6])}")
            rows.append({"rc_id": rc_id, "chamber": ch, "leg": leg, "session": sess, "date": d["date"], "record": "",
                         "bill": rv.get("bill") or "", "caption": "", "heading": "", "text": rv.get("text") or "",
                         "motion": rv.get("motion") or "", "amendment": "",
                         "printed_y": rv["printed"].get("Y") if rv["printed"].get("Y") is not None else "",
                         "printed_n": rv["printed"].get("N") or 0, "printed_p": rv["printed"].get("P") or 0,
                         "n_y": ny, "n_n": nn, "implied": "", "status": st, "file": d["file"],
                         "page": rv["page_in_day"], "source": rv["source"], "style": rv.get("style", "")})
            votes_out += [(rc_id, mid, code) for mid, code in votes.items()]
        n_all = sum(session_status.values())
        good = sum(session_status[k] for k in GOOD)
        print(f"{leg}{sess} {ch}: {n_all} roll calls, {good} verified ({session_status['ok']} exact, "
              f"{session_status['near']} within one, {session_status['ok_digit']} misread total, "
              f"{session_status['ok_lists']} misprinted total); "
              f"{n_retry} of {len(days)} days re-read, {page_reads} page reads", flush=True)
    if os.environ.get("DRY_RUN"):
        with open(os.path.join(OUT, "match_hist_report_dry.txt"), "w") as f:
            f.write("\n".join(report) + "\n")
        return
    save(rows, votes_out, report, redo)


def save(rows, votes_out, report, redo):
    """Merge this run's sessions into the outputs, keeping every other session there. Under a lock,
    so that two runs on different legislatures can finish at the same time."""
    rc_path, v_path = os.path.join(OUT, "rollcalls_hist.csv"), os.path.join(OUT, "votes_hist.csv.gz")
    report_path = os.path.join(OUT, "match_hist_report.txt")
    cols = ["rc_id", "chamber", "leg", "session", "date", "record", "bill", "caption", "heading", "text", "motion",
            "amendment", "printed_y", "printed_n", "printed_p", "n_y", "n_n", "implied", "status", "file", "page", "source", "style"]
    with open(os.path.join(OUT, ".hist.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = [r for r in csv.DictReader(open(rc_path))] if os.path.exists(rc_path) else []
        keep = [r for r in old if f"{r['leg']}{r['session']}-{r['chamber']}" not in redo]
        keep_ids = {r["rc_id"] for r in keep}
        keep_votes = []
        if os.path.exists(v_path):
            with gzip.open(v_path, "rt") as f:
                rd = csv.reader(f); next(rd)
                keep_votes = [row for row in rd if row[0] in keep_ids]
        old_report = open(report_path).read().splitlines() if os.path.exists(report_path) else []
        report = [ln for ln in old_report if ln.strip() and ln.split(" ", 1)[0].rsplit("x", 1)[0] not in redo] + report
        rows = sorted(keep + rows, key=lambda r: (int(r["leg"]), r["chamber"], r["session"], r["rc_id"]))
        tmp = rc_path + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, cols, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
        os.replace(tmp, rc_path)
        tmp = v_path + ".tmp"
        with gzip.open(tmp, "wt", newline="") as f:
            w = csv.writer(f); w.writerow(["rc_id", "member_id", "vote"]); w.writerows(keep_votes + votes_out)
        os.replace(tmp, v_path)
        with open(report_path + ".tmp", "w") as f:
            f.write("\n".join(report) + "\n")
        os.replace(report_path + ".tmp", report_path)


if __name__ == "__main__":
    main()

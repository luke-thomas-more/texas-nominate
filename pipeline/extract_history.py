"""Roll calls from the scanned journals (1846-1993), session by session, with a second read.

  1. Parse every day from the scans' own OCR text layer, match names, check the totals.
  2. Re-read with Tesseract the pages around each roll call that failed, and the pages that show
     more yea headings than roll calls found (every vote page of a day where the layer found
     none, and of every day in running text, 1846-76, whose headings the layer garbles past
     counting). The two OCRs fail differently (Tesseract is far better on 19th-century type, the
     layer on mid-century two-column lists), so a roll call that fails one often verifies with
     the other.
  3. Use the re-read's version of a roll call when it verifies, and add roll calls only the
     re-read found (failing ones too, for the reads and checks after). Pages around roll calls
     still failing, and pages still showing more yea headings than roll calls found, get a third
     read (each page column on its own, read row by row). A roll call
     that still fails goes through the session's last checks (a misread or misprinted total,
     names missing from the roll) with each of its readings. Roll calls that verify under no
     reading are kept but marked, and are left out of the scaling and the site.

Writes data/processed/rollcalls_hist.csv, votes_hist.csv.gz and match_hist_report.txt.
Usage: python pipeline/extract_history.py [legs, e.g. 1,12,24 or 1-73]
"""
import bisect, collections, csv, datetime, fcntl, gzip, json, os, re, statistics, sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_journals, parse_scans, scan_layout
from match_scans import SessionMatcher, check, check_parts, speakers, presidents_pro_tem, chair_on, parse_raw
from match_votes import load_roster, serving

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "journals")
OUT = os.environ.get("HIST_OUT", os.path.join(ROOT, "data", "processed"))     # HIST_OUT: a full run kept aside
GOOD = ("ok", "near", "ok_digit", "ok_lists", "ok_rest")       # statuses that count as verified
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


def dropped_digit(n):
    """Each number with one digit of n left out: 110 -> {10, 11}."""
    s = str(n)
    return {int(s[:i] + s[i + 1:]) for i in range(len(s))} if len(s) > 1 else set()


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
    January 1858 pages; "1959" among 1859's, "9129" for 1929). A session's days all fall within a few
    months: a day outside the 300 days that hold the most of them takes the year that puts it inside,
    if one does. (Not by page order: the 1853-54 journals number their pages afresh partway through,
    and the 1927 House has a day filed out of order; walking the pages, a day "before" the one
    ahead of it was moved on a year, and every day after it with it.)"""
    def ordinal(s):
        try:
            return datetime.date.fromisoformat(s).toordinal()
        except ValueError:
            return None
    known = sorted(o for o in map(ordinal, (d["date"] for d in days)) if o)
    if known:
        start = max(range(len(known)), key=lambda i: (bisect.bisect_right(known, known[i] + 300) - i, -i))
        lo, hi = known[start], known[start] + 300
        years = range(datetime.date.fromordinal(lo).year, datetime.date.fromordinal(hi).year + 1)
        for d in days:
            o = ordinal(d["date"])
            if o is None or not lo <= o <= hi:
                inside = [f"{y}{d['date'][4:]}" for y in years if lo <= (ordinal(f"{y}{d['date'][4:]}") or 0) <= hi]
                if inside:
                    d["date"] = inside[0]
    return sorted(days, key=lambda d: int(d.get("pages") or 0))


def run_session(days, roster, workers):
    paths = {d["file"]: os.path.join(RAW, d["file"]) for d in days}
    layer = {d["file"]: parse_scans.parse_day(d, paths[d["file"]], ocr="layer") for d in days}
    sm = SessionMatcher(roster)
    house = days[0]["chamber"] == "H"
    chairs = speakers() if house else presidents_pro_tem()

    def match(d, rv):
        sm.set_speaker([m for m, a, b in chairs if a <= d["date"] <= b] if house else chair_on(chairs, d["date"]))
        sm.set_date(d["date"])
        return sm.match(rv["lists"])
    day_of = {d["file"]: d for d in days}

    def learn(by_file):
        """Who's active in the session, and who votes each day, from one pass over these readings."""
        first, sure = [], collections.defaultdict(list)
        sm.day_voters = {}
        for f, rvs in by_file.items():
            for rv in rvs:
                first.append(match(day_of[f], rv)[0]); sure[day_of[f]["date"]].append(sm.sure)
        sm.learn_activity(first); sm.learn_days(sure); sm.cache = {}
    learn(layer)

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

    def headings_over(f, rvs):
        """Does a page show more yea headings than the reading found roll calls on it? (1969 Senate: two 30-1
        votes whose garbled headings the layer never took for lists, on a day whose other roll calls verified)
        In running text, so does any page that mentions a vote where none was found: the layer garbles its
        headings past counting there ("Y EAs-Messs.", "Y~As-lfessrs."; 1852 Senate, two votes)."""
        if f not in marks:
            marks[f] = scan_layout.vote_marks(paths[f])
        found = collections.Counter(rv["page_in_day"] for rv in rvs)
        running = any(rv.get("style") == "running" for rv in rvs)
        return any(n > found[p] or running and not found[p] for p, n in marks[f].items())

    # a running-text day gets Tesseract on every vote page: there the layer garbles headings past counting, and a
    # roll call it can't see at all sits beside ones it reads and verifies (1852 Senate: 15-7 by a 13-8 vote)
    running_days = {f for f, rvs in layer.items() if any(rv.get("style") == "running" for rv in rvs)}
    retry = {f: None if f in running_days and TARGETED else reread(f, rvs) for f, rvs in layer.items()
             if any(not verifies(rv) for rv in rvs) or (not rvs and mentions_votes(paths[f]))
             or rvs and TARGETED and (f in running_days or headings_over(f, rvs))}
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

    def twin(a, b, strict=False):
        """One roll call twice: totals alike (strict: the nays' too, as a 30-1 vote isn't a 30-0 one), a page
        apart at most, and nine in ten of the same votes."""
        if abs(a["page_in_day"] - b["page_in_day"]) > 1 or a["printed"].get("Y") is None \
                or a["printed"].get("Y") != b["printed"].get("Y") \
                or strict and (a["printed"].get("N") or 0) != (b["printed"].get("N") or 0):
            return False
        va, vb = match(day_of[a["file"]], a)[0], match(day_of[b["file"]], b)[0]
        return bool(va) and sum(1 for m, c in va.items() if vb.get(m) == c) >= 0.9 * max(len(va), len(vb))

    def near(rv):
        votes, unmatched = match(day_of[rv["file"]], rv)
        ny, nn, ok = check(rv, votes)
        return ok or near_ok(rv, ny, nn, unresolved_names(unmatched))

    def alike(a, b):
        """Two readings of one roll call, whatever totals they read: a page apart at most, and nine in ten
        of the votes the shorter one has the same in the other."""
        if abs(a["page_in_day"] - b["page_in_day"]) > 1:
            return False
        va, vb = match(day_of[a["file"]], a)[0], match(day_of[b["file"]], b)[0]
        return bool(va and vb) and sum(1 for m, c in va.items() if vb.get(m) == c) >= 0.9 * min(len(va), len(vb))

    def merge(rvs, other, pages=None):
        """Bring in roll calls from another reading (of `pages`, or of the whole day): a verified one in
        place of the same roll call when that failed (its count may be the garbled part) or found fewer
        votes (a nay list missed), and one within one of its totals in place of a reading that found no
        nay list where this one has one (1871 Senate: "N ays---Douglass, Ford, Mills---3." the layer
        missed, so 21-3 read as 21-0); else as one the first reading missed; and drop what either reading
        took for a roll call that is only a piece of one. One the first reading missed comes in too when
        it's within one of its totals (1918: two roll calls only Tesseract found, each a yea short), or
        failing, unless it's another reading of one already in: the later reads, the readings combined
        and the final checks may yet verify it (1927: a 101-14 vote only Tesseract found, three yeas
        short, on a day the layer read better overall and so led); and so does a failing one cut off at a page
        this reading skipped, whatever it's alike, to be read again with that page. A reading that has both roll calls, each
        in its own spot, isn't one of them read twice: two votes by the same members both come in."""
        def apart(tv, rv):
            # this reading has roll call rv too, in rv's own spot, so tv, read elsewhere, is another roll call with
            # the same voters, not rv again (a motion to suspend the rules, then the bill's passage: 1927 House,
            # 100-3 and 100-4 a page apart; 1893 Senate, 28-1 twice on a page)
            return any(x is not tv and same_spot(x, rv) for x in other)
        for tv in other:
            exact = verifies(tv)
            close = exact or near(tv)
            if any((verifies(rv) or not close) and fragment(tv, rv) for rv in rvs):
                continue
            spot_taken = False
            if close:
                # a failing one brought in that way gives way to a closer reading of it, in its spot or a
                # twin (1893: Tesseract's eight yeas of a 25-2 vote kept out the column read's 24-2)
                spot_taken = any(rv.get("added") and same_spot(rv, tv) for rv in rvs)
                rvs[:] = [rv for rv in rvs if not (rv.get("added") and (same_spot(rv, tv) or twin(tv, rv) and not apart(tv, rv)))]
            same = [i for i, rv in enumerate(rvs) if same_spot(rv, tv)]
            if not same:
                if not close:
                    # (one cut off at a page this reading skipped is a piece whatever it's alike: 1959 House, 52 of
                    # a 119-7 vote's yeas, page 12 unread, alike the 115-6 one there)
                    if sum(map(len, yeas_nays(tv))) >= 4 and (tv.get("cut") or not any(alike(tv, rv) and not apart(tv, rv) for rv in rvs)):
                        tv["added"] = True      # kept in the end only if it verifies
                        rvs.append(tv)
                    continue
                # read at another spot, but the same roll call (not when it takes the spot of one brought in
                # there before: two readings put a roll call there; 1891 House, 78-15 and 78-16 a page apart)
                if not exact and not spot_taken and any(twin(tv, rv) and not apart(tv, rv) for rv in rvs):
                    continue
                # so too a contested one read exactly (1858: the layer's 13-13 vote a few lines from where
                # Tesseract put it) when it's in already as many times as this reading has it (a day can hold
                # two motions lost 8-17 by the same members, both then in this reading; and a run of unanimous
                # votes, alike in everything, is left as read). Only the pages this reading covers count: a
                # 14-14 Senate split repeated on the next page, which it didn't read, isn't this one (1934); nor
                # does one this reading has in its own spot too (1939 House: four 130-2 votes on pages 137-139,
                # the column read's of page 138 kept out by the two on page 139 it also read)
                if exact and min(tv["printed"].get("Y") or 0, tv["printed"].get("N") or 0) > 0:
                    twins = sum(1 for rv in rvs if twin(tv, rv, strict=True) and not apart(tv, rv)
                                and (pages is None or rv["page_in_day"] in pages))
                    if twins and twins >= sum(1 for x in other if twin(tv, x, strict=True)):
                        continue
                rvs.append(tv)
            elif exact and (not verifies(rvs[same[0]]) or sum(map(len, yeas_nays(tv))) > sum(map(len, yeas_nays(rvs[same[0]])))):
                rvs[same[0]] = tv
            elif close and tv["printed"].get("N") and yeas_nays(tv)[1] and not rvs[same[0]]["printed"].get("N") \
                    and not yeas_nays(rvs[same[0]])[1]:
                rvs[same[0]] = tv       # a nay list the reading in its place missed, which made the vote unanimous
            if exact:
                # pieces of it taken for roll calls, and a failing reading of it brought in at another spot
                # (not one with under half its votes: a few names alike prove little)
                rvs[:] = [rv for rv in rvs if rv is tv or not (fragment(rv, tv) or rv.get("added") and not verifies(rv) and alike(rv, tv)
                                                               and not apart(tv, rv)
                                                               and 2 * sum(map(len, yeas_nays(rv))) >= sum(map(len, yeas_nays(tv))))]
        return rvs

    chosen = {}
    readings = collections.defaultdict(list)      # every reading of the day, for the consensus below
    for d in days:
        f = d["file"]
        rvs = [dict(rv, source="layer") for rv in layer[f]]
        readings[f] += rvs
        if f in retry:
            tess = [dict(tv, source=READS[0]) for tv in parse_scans.parse_day(d, paths[f], ocr=READS[0], pages=retry[f])]
            readings[f] += tess
            cross_reference(rvs, tess)
            # the day's better reading wins outright; then patch its failures from the other reading
            if sum(map(verifies, tess)) > sum(map(verifies, rvs)):
                rvs, other, pages = tess, rvs, None          # (the layer covers every page)
            else:
                other, pages = tess, retry[f]
            rvs = merge(rvs, other, pages)
        chosen[f] = rvs
    # more reads around the roll calls still failing, and of pages still showing more yea headings than
    # roll calls found (1884 Senate: two 4-22 votes only the column read finds, on days whose other roll
    # calls the first two readings verify)
    for source in READS[1:]:
        again = {f: reread(f, rvs) for f, rvs in chosen.items()
                 if f in retry and (any(not verifies(rv) for rv in rvs) or TARGETED and headings_over(f, rvs))}
        if not again:
            break
        page_reads += read_all(source, again)
        for f, pages in again.items():
            more = [dict(tv, source=source) for tv in parse_scans.parse_day(day_of[f], paths[f], ocr=source, pages=pages)]
            readings[f] += more
            cross_reference(chosen[f], more)
            chosen[f] = merge(chosen[f], more, pages)
    for f in chosen:
        chosen[f] = sorted(chosen[f], key=lambda r: (r["page_in_day"], r["seq"]))
    # final pass: learn who's active from everything, then match for keeps
    learn(chosen)
    out, taken = [], set()
    for d in days:
        for rv in chosen[d["file"]]:
            votes, unmatched = match(d, rv)
            ny, nn, ok = check(rv, votes)
            if not ok and CONSENSUS:
                both = consensus(rv, [x for x in readings[d["file"]] if x is not rv and same_spot(x, rv)], lambda x: match(d, x)[0])
                if both is not None and check(rv, both)[2]:
                    votes, unmatched = both, []
                    ny, nn, ok = check(rv, votes)
                    rv["source"] = rv["source"] + "+consensus"
            alts = []           # the other readings of a roll call that fails, for the session's own checks
            if not ok and not near_ok(rv, ny, nn, unresolved_names(unmatched)):
                # another reading of it may come within one where this one doesn't (1947 Senate: Tesseract
                # read 19 of the 20 yeas, the layer 18): the closest such, exact first (not a roll call
                # kept in its own right a few lines off; nor, for one brought in from another reading, a
                # reading where a roll call kept is: 1933 House, Tesseract's failing 67-35 eleven points
                # below the layer's, which then took the column read of that same roll call)
                best = None
                for x in readings[d["file"]]:
                    if x is not rv and same_spot(x, rv) and id(x) not in taken and \
                            not any(x is y or rv.get("added") and y is not rv and same_spot(x, y) for y in chosen[d["file"]]):
                        xv, xu = match(d, x)
                        xy, xn, xok = check(x, xv)
                        if (xok or near_ok(x, xy, xn, unresolved_names(xu))) and (best is None or xok and not best[5]):
                            best = (x, xv, xu, xy, xn, xok)
                        alts.append((x, xv, xu, xy, xn, xok))
                if best:
                    rv, votes, unmatched, ny, nn, ok = best
                    taken.add(id(rv))
                    alts = []
            out.append((d, rv, votes, unmatched, ny, nn, ok, alts))
    return out, len(retry), page_reads


def unresolved_names(unmatched):
    """Names left unresolved in the yea or nay lists (junk caught in the absentee lists doesn't matter;
    nor does a "Mr. President" no pro tem on the roster can be, when one is listed by name)."""
    return [u for u in unmatched if getattr(u, "code", "Y") in ("Y", "N") and parse_raw(u)[3] is None]


def near_ok(rv, ny, nn, unresolved):
    """Every name resolved and each total within one of the printed one: the journal's own count is
    sometimes off (1846: 22 names under "Yeas—23"); at worst a vote is missing (against any OCR's
    reading of the totals: "70" in the layer may be "76" to Tesseract). A list a name short may hold
    that name garbled past reading: so long as the lists hold no more unread names than they lack
    votes, the unread ones are the missing votes, left out (a list over its total with a name unread
    may have taken in another list's names)."""
    py, pn = rv["printed"].get("Y"), rv["printed"].get("N") or 0
    alts = rv.get("alt_printed", [])
    alts = [alts] if isinstance(alts, dict) else alts
    ys = {py} | {a["Y"] for a in alts if a.get("Y") is not None}
    ns = {pn} | {a.get("N") or 0 for a in alts}
    if rv["printed"].get("N") is None and not rv["lists"].get("N") and any((a.get("N") or 0) >= 2 for a in alts):
        # it found neither a nay total nor a nay list where another reading of the spot has nays: it missed
        # the nay list, so it isn't within one of none (1897 House: Tesseract's 17 yeas and no nays, against
        # "Nays—91"). A nay list without its total is the journal's ("Nays—Mr. Millican.", 1856 Senate)
        ns.discard(0)
    # (its own yea total unread, another OCR's of the same spot will do)
    return (any(y is not None for y in ys) and ny + nn >= 4
            and (not unresolved and any(abs(ny - y) <= 1 for y in ys if y is not None) and any(abs(nn - n) <= 1 for n in ns)
                 or any(0 <= y - ny <= 1 and 0 <= n - nn <= 1 and len(unresolved) <= y - ny + n - nn
                        for y in ys if y is not None for n in ns)))


# a roll call no single reading verifies, its readings combined member by member: each OCR drops or garbles
# different names, so between them they may have every name
CONSENSUS = os.environ.get("CONSENSUS", "1") == "1"


def consensus(rv, others, votes_of):
    """{member_id: code} from every reading of one roll call: each member any reading placed, with the code
    most readings gave (a tie between codes leaves the member out). None with no other reading."""
    if not others:
        return None
    seen = collections.defaultdict(collections.Counter)
    for x in [rv] + others:
        for m, code in votes_of(x).items():
            seen[m][code] += 1
    out = {}
    for m, codes in seen.items():
        (code, n), *rest = codes.most_common()
        if not rest or rest[0][1] < n:
            out[m] = code
    # a name the readings disagree on: one reading places member A in a list where another places B instead
    # (1860 Senate: the layer read a garbled "Whaley" as Wallace, Tesseract read Whaley). The union would keep
    # both, one vote too many that a journal's own miscount can make look exact, so a member only some
    # readings place, in a list where a member only the other readings place sits too, is left out
    readings = [votes_of(x) for x in [rv] + others]
    partial = collections.defaultdict(list)
    for m, code in out.items():
        has = frozenset(i for i, v in enumerate(readings) if m in v)
        if len(has) < len(readings):
            partial[code].append((m, has))
    for code, ms in partial.items():
        for m, has in ms:
            if any(not (has & h2) for m2, h2 in ms if m2 != m):
                out.pop(m, None)
    return out


def fill_rest(votes, py, pn, ny, nn, roster, date, named, chair, full, other_unresolved):
    """A list short of its printed total, the other list exact, in a session whose roll calls list every
    member somewhere (yeas, nays, present, absent, excused): when exactly as many members serving that
    day appear in no list as the short list lacks, they're its missing names (garbled or dropped by OCR),
    as the Senate's unprinted yeas are everyone else serving. The Speaker, who rarely votes and often
    isn't listed, isn't counted among them. (votes with them added, code) or None."""
    on = {t["member_id"] for t in roster if serving(t, date)} | named
    if full < len(on) - 2:                   # the session's rolls leave members out (no absentee lists)
        return None
    unplaced = sorted(on - set(votes) - set(chair))
    for code, short, other_exact in (("Y", py - ny, nn == pn), ("N", pn - nn, ny == py)):
        if other_exact and not other_unresolved(code) and 1 <= short <= 6 and len(unplaced) == short:
            filled = dict(votes)
            filled.update({m: code for m in unplaced})
            return filled, code
    return None


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
        # the session's measures from the roll calls the day's leading readings found (not the failing ones
        # brought in from another reading, which may be pieces or misreadings)
        found = [r for r in results if not r[1].get("added")]
        placed = collections.defaultdict(list)               # each roll call's reading and the members it placed, per day
        present = collections.defaultdict(set)               # and those placed by readings whose lists keep to their totals
        for d, rv, votes, unmatched, ny, nn, *_ in found:
            placed[d["date"]].append((rv, set(votes)))
            # (a list run on past its total may take in anyone: 1913 House, a 9-nay list read as 36 named a member
            # who joined the House in 1914)
            py, pn = rv["printed"].get("Y"), rv["printed"].get("N") or 0
            if not (py is not None and ny > py + 1 or nn > pn + 1):
                present[d["date"]] |= set(votes)
        # how many members a complete roll (yeas through absentees) places in this session
        sizes = sorted(len(r[2]) for r in found)
        full = sizes[int(0.9 * (len(sizes) - 1))] if sizes else 0
        # how many members a roll call usually lists as not voting (present, absent, excused) this session
        idle = sorted(sum(c not in "YN" for c in r[2].values()) for r in found)
        usual_idle = idle[len(idle) // 2] if idle else 0
        roster = terms.get((ch, leg), [])
        seats = len({t["member_id"] for t in roster})       # at most: replacements make it more than seats

        def others(date, chosen):
            """The members each other roll call of the day placed: not the one being judged (whose own failing
            reading, its list run on, may place most of the roster: 1856 House, 90 against the 71 Tesseract read)."""
            return [members for x, members in placed[date] if x is not chosen]
        house_chairs = speakers() if ch == "H" else []
        session_status = collections.Counter()

        def judge(d, rv, votes, unmatched, ny, nn, ok, chosen):
            """(status, votes, yeas, nays, implied) for one reading of a roll call (`chosen`: the reading kept for it)."""
            py, pn = rv["printed"].get("Y"), rv["printed"].get("N") or 0
            unresolved = unresolved_names(unmatched)
            near = not ok and near_ok(rv, ny, nn, unresolved)
            named = sum(len(x) for x in rv["lists"].values())
            # the printed total misread by one OCR'd digit ("84" for 54), or with a stray one that makes
            # it more than the chamber's seats ("110" nays in a House of 66, 1848): accepted only when
            # every name resolved, the lists account for the whole chamber that day, and a nay list was
            # found (a missed "Nays" heading would otherwise pass for a misread yea total)
            fits = lambda p, g: confusable(p, g) or p > seats and g in dropped_digit(p)
            digit = (not ok and not near and not unresolved and py is not None and len(votes) >= 10
                     and len(votes) >= max(map(len, others(d["date"], chosen)), default=0) - 1 and not (nn == 0 and pn == 0 and ny != py)
                     and fits(py, ny) and fits(pn, nn))
            # the printed yea total itself misprinted (1921: "Yeas—117." over 110 names, with every member
            # of the House listed once): accepted only when every name resolved, the nay total checks
            # against a real nay list, and the lists account for the whole chamber, so no name is missing
            # and none came from another list (absentees and the rest are printed after the nays). Or the
            # nay total misprinted, the yeas exact (1929: "Nays—77." over 80 names, the whole House listed):
            # only a few off, as a short list after the nays, its heading missed, would run into them. The
            # nay total may be another reading's (1929: "Nays—7." over the page, read only by Tesseract)
            y_ok, n_ok = check_parts(rv, votes)[2:]
            misprint = (not ok and not near and not digit and not unresolved and py is not None
                        and ((pn > 0 or nn > 0) and n_ok or pn > 0 and y_ok and abs(nn - pn) <= 3)
                        and full >= 20 and len(votes) >= full - 1
                        # and not a heading missed inside the yea list, which moves the rest of the yeas into
                        # a not-voting list (1963 House: 39 yeas and 109 "excused" for a printed 147-1)
                        and sum(c not in "YN" for c in votes.values()) <= usual_idle + (15 if ch == "H" else 5))
            st = "ok" if ok else "near" if near else "ok_digit" if digit else "ok_lists" if misprint \
                else "tally_only" if named == 0 else "count_mismatch"
            implied = ""
            if st == "count_mismatch" and py is not None:
                # serving that day: anyone a reading kept placed (the reading kept for this roll call too, 1961 House:
                # Tesseract's failing reading of a 118-4 vote names the member the layer's, three yeas short, lacks),
                # but not from a list run on past its total
                filled = fill_rest(votes, py, pn, ny, nn, roster, d["date"], set(votes) | present[d["date"]],
                                   [m for m, a, b in house_chairs if a <= d["date"] <= b], full,
                                   lambda code: any(getattr(u, "code", "") == ("N" if code == "Y" else "Y") for u in unresolved))
                if filled:
                    votes, implied = filled
                    ny, nn = (sum(1 for v in votes.values() if v == c) for c in ("Y", "N"))
                    st = "ok_rest"
            return st, votes, ny, nn, implied

        used = {id(r[1]) for r in results}      # the readings kept (each stands for one roll call)
        n = 0
        for d, rv, votes, unmatched, ny, nn, ok, alts in results:
            st, votes, ny, nn, implied = judge(d, rv, votes, unmatched, ny, nn, ok, rv)
            if st not in GOOD:
                # another reading of it may pass these checks where this one doesn't (1927: Tesseract's
                # lists of a 99-21 vote account for the whole House; the layer, leading, lost the nay total)
                passed = [(GOOD.index(j[0]), i, alt, j) for i, alt in enumerate(alts) if id(alt[0]) not in used
                          for j in [judge(d, *alt, rv)] if j[0] in GOOD]
                if passed:
                    _, _, (rv, _, unmatched, *_), (st, votes, ny, nn, implied) = min(passed, key=lambda t: t[:2])
                    used.add(id(rv))
                elif rv.get("added"):
                    continue                    # brought in from another reading, and never verified
            n += 1
            rc_id = f"{leg}{sess}-{ch}x{n:04d}"           # "x": read from the scans
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
                         "n_y": ny, "n_n": nn, "implied": implied, "status": st, "file": d["file"],
                         "page": rv["page_in_day"], "col": rv["at"][0], "y": rv["at"][1],
                         "source": rv["source"], "style": rv.get("style", "")})
            votes_out += [(rc_id, mid, code) for mid, code in votes.items()]
        n_all = sum(session_status.values())
        good = sum(session_status[k] for k in GOOD)
        print(f"{leg}{sess} {ch}: {n_all} roll calls, {good} verified ({session_status['ok']} exact, "
              f"{session_status['near']} within one, {session_status['ok_digit']} misread total, "
              f"{session_status['ok_lists']} misprinted total, {session_status['ok_rest']} completed from the roll); "
              f"{n_retry} of {len(days)} days re-read, {page_reads} page reads", flush=True)
    if os.environ.get("DRY_RUN"):
        # a trial run: the report (and with DRY_OUT, the roll calls) go aside, the outputs stay as they are
        out = os.environ.get("DRY_OUT") or OUT
        with open(os.path.join(out, "match_hist_report_dry.txt"), "w") as f:
            f.write("\n".join(report) + "\n")
        if os.environ.get("DRY_OUT"):
            with open(os.path.join(out, "rollcalls.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, list(rows[0].keys()) if rows else ["rc_id"])
                w.writeheader(); w.writerows(rows)
            with gzip.open(os.path.join(out, "votes.csv.gz"), "wt", newline="") as f:
                w = csv.writer(f); w.writerow(["rc_id", "member_id", "vote"]); w.writerows(votes_out)
        return
    save(rows, votes_out, report, redo)


def save(rows, votes_out, report, redo):
    """Merge this run's sessions into the outputs, keeping every other session there. Under a lock,
    so that two runs on different legislatures can finish at the same time."""
    rc_path, v_path = os.path.join(OUT, "rollcalls_hist.csv"), os.path.join(OUT, "votes_hist.csv.gz")
    report_path = os.path.join(OUT, "match_hist_report.txt")
    cols = ["rc_id", "chamber", "leg", "session", "date", "record", "bill", "caption", "heading", "text", "motion",
            "amendment", "printed_y", "printed_n", "printed_p", "n_y", "n_n", "implied", "status", "file", "page", "col", "y",
            "source", "style"]
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

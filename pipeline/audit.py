"""Accuracy audit: a random sample of roll calls, each read name by name off its journal page and compared with
the published data (docs/data/downloads).

  sample          draw the sample into audit/sample.csv (fixed seed; strata below)
  show RC ...     render each roll call's journal page(s) to data/audit/pages/ for reading and say where its
                  lists start. The data's names aren't shown, so the reading is blind.
  check RC ...    compare the reading in audit/read/RC.txt with the data, list by list
  report          error rates by era, each stratum weighted by its share of the votes; writes audit/report.json
                  and docs/data/downloads/audit.csv (every name read, and what the data has for it)

A reading gives the names as the page prints them, one line per list, codes as in votes.csv:
    Y: Aikin; Alexander; Butler of Brazos; Barton, B; Mr. Speaker
    N: ...                      (P present not voting, A absent, E excused; a list the page doesn't print is left out,
                                 and "Y: implied" marks the Senate's unprinted yeas)
Lines starting with # are notes. After `check`, every difference is looked at again on the page: a misreading
is corrected in the file, a name the roster spells differently from the page goes in audit/aliases.csv, and
what remains are the data's errors.

Strata: era x chamber x source (scan, tally sheet, journal text) x status ("ok", or one of the looser checks,
oversampled). The journal text since 2009 is also checked in full against LegiScan (validate_legiscan.py).
"""
import collections
import csv
import functools
import gzip
import json
import os
import re
import subprocess
import sys
import unicodedata

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DL = os.path.join(ROOT, "docs", "data", "downloads")
PROC = os.path.join(ROOT, "data", "processed")
JOURNALS = os.path.join(ROOT, "data", "raw", "journals")
AUDIT = os.path.join(ROOT, "audit")
PAGES = os.path.join(ROOT, "data", "audit", "pages")
SEED = 20261101
ERAS = [(1, 13, "1846-73"), (14, 26, "1874-1900"), (27, 41, "1901-30"), (42, 56, "1931-60"), (57, 62, "1961-72"),
        (63, 67, "1973-82"), (68, 73, "1983-94"), (74, 89, "1995-2025")]
LISTS = "YNPAE"


def era_of(leg):
    return next(name for a, b, name in ERAS if a <= leg <= b)


def allocation(source, group, era):
    """Roll calls to draw from a stratum: 12 verified exactly, 4 by the looser checks (near, ok_digit, ok_lists,
    ok_rest), whose errors are likelier; fewer from the journal text, which needs no reading of scans."""
    if source == "journal":
        return 4
    if era == "1995-2025":
        return 6
    return 12 if group == "ok" else 4


def load_rollcalls():
    with gzip.open(os.path.join(DL, "rollcalls.csv.gz"), "rt") as f:
        return {r["rc_id"]: r for r in csv.DictReader(f)}


def sample():
    rcs = load_rollcalls()
    strata = collections.defaultdict(list)
    for rc, r in rcs.items():
        group = "ok" if r["status"] == "ok" else "other"
        strata[(era_of(int(r["leg"])), r["chamber"], r["source"], group)].append(rc)
    rng = np.random.default_rng(SEED)
    rows = []
    for key in sorted(strata):
        pop = sorted(strata[key])
        n = min(len(pop), allocation(key[2], key[3], key[0]))
        votes = sum(int(rcs[rc]["n_y"]) + int(rcs[rc]["n_n"]) for rc in pop)
        for k, i in enumerate(rng.choice(len(pop), n, replace=False)):
            rows.append({"rc_id": pop[i], "era": key[0], "chamber": key[1], "source": key[2], "group": key[3],
                         "stratum_rollcalls": len(pop), "stratum_votes": votes, "stratum_sample": n, "draw": k + 1})
    # the work goes round the strata draw by draw, so an audit stopped part way still has a random sample of each
    rows.sort(key=lambda r: (r["draw"], r["era"], r["chamber"], r["source"], r["group"]))
    os.makedirs(AUDIT, exist_ok=True)
    with open(os.path.join(AUDIT, "sample.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} roll calls from {len(strata)} strata -> audit/sample.csv")


# ---- finding and rendering the page

def hist_spots():
    out = {}
    for r in csv.DictReader(open(os.path.join(PROC, "rollcalls_hist.csv"))):
        out[r["rc_id"]] = r
    return out


@functools.lru_cache(maxsize=8)
def pdf_texts(path):
    txt = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True).stdout
    return txt.split("\f")


def words(s):
    return re.findall(r"[a-z]{3,}", s.lower())


def find_page(r, path):
    """The PDF page (1-based) holding a modern-format roll call: where its record number or its totals appear,
    the page whose text before them best matches the words the data keeps from around the vote."""
    pages = pdf_texts(path)
    # the data's counts, or within one of them where the roll call passed as near, as OCR may print them
    # ("Nays II" for 11, "Nays I" for 1)
    ocr = {"1": "[1Il|IJ]", "0": "[0OoD]", "5": "[5S]", "8": "[8B]", "6": "[6b]", "2": "[2Z]"}
    digits = lambda n: r"\s?".join(ocr.get(ch, ch) for ch in str(n))
    alt = lambda n: "(?:" + "|".join(digits(int(n) + d) for d in ((0,) if r["status"] == "ok" else (0, -1, 1)) if int(n) + d >= 0) + ")"
    pats = []
    if r["record"]:
        pats.append(re.compile(rf"Re?c\s?o\s?r\s?d\s*(?:No\W{{0,2}}\s*)?{digits(int(float(r['record'])))}\b", re.I))
    pats.append(re.compile(rf"Yeas\W{{0,3}}\s*{alt(r['n_y'])}\s*\W{{1,3}}\s*Nays\W{{0,3}}\s*{alt(r['n_n'])}\b", re.I))
    pats.append(re.compile(rf"\b{alt(r['n_y'])}\s*Yeas\W{{1,3}}\s*{alt(r['n_n'])}\s*Nays", re.I))
    context = set(words(" ".join(r[k] for k in ("text", "motion", "amendment") if r.get(k))))
    # the bill named nearest before the vote counts most: the motions around votes are boilerplate
    bill = re.match(r"([A-Z]+)\s*(\d+)", r.get("bill") or "")
    bill_re = re.compile(r"\b" + r"\.?\s?".join(bill.group(1)) + rf"\.?\s*(?:No\.?\s*)?{bill.group(2)}\b") if bill else None
    best = None
    for pat in pats:
      if best:
        break
      for i, t in enumerate(pages):
        for m in pat.finditer(t):
            before = ((pages[i - 1] if i else "") + t[:m.start()])[-1500:]
            named = [b for b in bill_re.finditer(before)] if bill_re else []
            others = re.findall(r"\b[HS]\.\s?[BCJR]\.(?:\s?[RB]\.)?\s*\d+", before[named[-1].end():]) if named else []
            score = len(context & set(words(before))) + (100 if named and not others else 30 if named else 0)
            if best is None or score > best[0]:
                best = (score, i + 1)
    return best[1] if best else None


def render(path, page, tag, dpi=150, crop=None):
    """Render one PDF page to data/audit/pages/<tag>.png; crop = (x, y, w, h) in points."""
    os.makedirs(PAGES, exist_ok=True)
    out = os.path.join(PAGES, tag)
    args = ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile"]
    if crop:
        s = dpi / 72
        args += ["-x", str(int(crop[0] * s)), "-y", str(int(crop[1] * s)), "-W", str(int(crop[2] * s)), "-H", str(int(crop[3] * s))]
    subprocess.run(args + [path, out], check=True)
    return out + ".png"


def page_size(path, page):
    info = subprocess.run(["pdfinfo", "-f", str(page), "-l", str(page), path], capture_output=True, text=True).stdout
    m = re.search(r"Page\s+\d+ size:\s+([\d.]+) x ([\d.]+)", info)
    return float(m.group(1)), float(m.group(2))


def page_count(path):
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info).group(1))


def show(rc_ids, dpi=150):
    rcs, spots = load_rollcalls(), hist_spots()
    for rc in rc_ids:
        r = rcs[rc]
        printed = f"data totals Y {r['n_y']} N {r['n_n']}, printed present {r['printed_p'] or '-'}"
        head = f"{rc}  {r['chamber']} {r['date']}  {r['source']} {r['status']}{'  implied ' + r['implied'] if r['implied'] else ''}"
        if r["source"] == "journal":
            print(head + "  (journal text: see `journal_text`)")
            continue
        sp = spots.get(rc)
        rel = (sp["file"] if sp else re.sub(r"^https://lrl\.texas\.gov/scanned/(House|Senate)Journals/", "", r["journal_url"]))
        path = os.path.join(JOURNALS, sp["file"]) if sp else local_path(r)
        n_pages = page_count(path)
        if r["source"] == "tally":
            pages = tally_pages(r, path, n_pages)
            where = f"tally sheet for record {r['record']}: page(s) {pages} of {n_pages}"
        elif sp and sp["page"]:
            p = int(float(sp["page"])) + 1
            pages = [p] + ([p + 1] if p < n_pages else [])
            where = f"page {p} of {n_pages}, lists start column {sp['col']} at {float(sp['y']):.0f} pt ({sp['style']})"
        else:
            p = find_page(r, path)
            pages = [p] + ([p + 1] if p and p < n_pages else []) if p else []
            where = f"page {p} of {n_pages} (found by its text)" if p else "page not found: look through the file"
        outs = [render(path, p, f"{rc}_p{p}", dpi) for p in pages]
        if sp and sp["page"]:
            # lists are set small: the list's column from its heading down, at twice the size; a list that starts
            # low on the page continues at the top of the next column (the right one, or the next page's left)
            p = int(float(sp["page"])) + 1
            w, h = page_size(path, p)
            # (wide of the middle: many scans sit off center on the page)
            x0, x1 = {"L": (0, w / 2 + 60), "R": (w / 2 - 70, w)}.get(sp["col"], (0, w))
            y0 = max(0, float(sp["y"]) - 18)
            tall = 220 if sp["style"] == "running" else h - y0
            zooms = [render(path, p, f"{rc}_zoom", 300, (x0, y0, x1 - x0, min(tall, h - y0)))]
            if sp["style"] != "running" and h - y0 < 0.6 * h:
                if sp["col"] == "L":
                    zooms.append(render(path, p, f"{rc}_zoom2", 300, (w / 2 - 8, 0, w / 2 + 8, 0.6 * h)))
                elif p < n_pages:
                    zooms.append(render(path, p + 1, f"{rc}_zoom2", 300, (0, 0, w / 2 + 8 if sp["col"] == "R" else w, 0.6 * h)))
            outs = zooms + outs
        print(f"{head}\n    {printed}\n    {path}\n    {where}\n    " + "\n    ".join(outs))


_index = None


def local_path(r):
    """The local copy of a roll call's journal file: by the download index, else from an LRL scan's URL."""
    global _index
    if _index is None:
        _index = {d.get("url"): d["file"] for d in json.load(open(os.path.join(JOURNALS, "index.json")))}
    if r["journal_url"] in _index:
        return os.path.join(JOURNALS, _index[r["journal_url"]])
    m = re.match(r"https://lrl\.texas\.gov/scanned/(House|Senate)Journals/(\d+)/(.+)$", r["journal_url"])
    ch = "H" if m.group(1) == "House" else "S"
    folder = f"{int(r['leg'])}{r['session']}"
    path = os.path.join(JOURNALS, ch, folder, m.group(3))
    if not os.path.exists(path):           # sessions are filed as 63R, 631, ...: find the file by name
        hits = [os.path.join(JOURNALS, ch, d, m.group(3)) for d in os.listdir(os.path.join(JOURNALS, ch))
                if d.startswith(str(int(r["leg"]))) and os.path.exists(os.path.join(JOURNALS, ch, d, m.group(3)))]
        path = hits[0] if hits else path
    return path


def tally_pages(r, path, n_pages):
    """A tally sheet's page: the day's sheets are its last pages, one per record number, so the sheet for record
    k of n is page n_pages - n + k; the pages either side are given too, since a day's count can be off."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from parse_journals import paragraphs_pdf, sheet_count
    n, _ = sheet_count(" ".join(paragraphs_pdf(path)), 0)
    k = int(float(r["record"]))
    p = n_pages - n + k if n else None
    return [q for q in (p - 1, p, p + 1) if p and 1 <= q <= n_pages] if p else []


def journal_text(rc):
    """The 1995-on journal text of a roll call's lists, from the cached HTML, for reading."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from parse_journals import paragraphs_html
    r = load_rollcalls()[rc]
    idx = json.load(open(os.path.join(JOURNALS, "index.json")))
    day = next(d for d in idx if d.get("url") == r["journal_url"] or r["journal_url"].endswith(os.path.basename(d["file"])))
    paras = paragraphs_html(open(os.path.join(JOURNALS, day["file"]), encoding="utf-8", errors="replace").read())
    if r["record"]:
        pat = re.compile(rf"\(Record {int(float(r['record']))}\)")
    else:
        pat = re.compile(rf"Yeas {r['n_y']}, Nays {r['n_n']}\b")
    context = set(words(" ".join(r[k] for k in ("text", "motion", "amendment") if r.get(k))))
    hits = [(len(context & set(words(" ".join(paras[max(0, i - 3):i + 1])))), i) for i, p in enumerate(paras) if pat.search(p)]
    if not hits:
        return None
    i = max(hits)[1]
    return "\n".join(paras[i:i + 6])


# ---- comparing a reading with the data

def norm(s):
    s = unicodedata.normalize("NFKD", s)                 # "Anchía" = "Anchia"
    return re.sub(r"[^a-z]", "", s.lower())


SUFFIX = {"jr", "sr", "ii", "iii", "iv"}


def name_keys(*names):
    """(surname spellings, given-name initials) for a roster name: its last one to three words, so compound
    surnames ("De La Garza", "Harris Davila") match whichever part the journal prints."""
    sur, ini = set(), set()
    for name in names:
        toks = [t for t in re.split(r"[\s,]+", re.sub(r'["“”(][^"“”)]*["“”)]', lambda m: " " + m.group(0)[1:-1] + " ", name))
                if t and norm(t) not in SUFFIX]
        toks = [t for t in toks if norm(t)]
        for k in (1, 2, 3):
            if len(toks) > k - 1:
                sur.add(norm("".join(toks[-k:])))
        ini |= {norm(t)[0] for t in toks[:-1]}
    return sur, ini


def parse_name(s):
    """A name as printed: (surname, county, initials, special)."""
    s = re.sub(r"\s+", " ", s.strip().rstrip("."))
    m = re.fullmatch(r"(?i)mr\.? (speaker|president)(\s*\(c\))?", s)
    if m:
        return None, None, "", m.group(1).lower()
    s = re.sub(r"\s*\(C\)$", "", s)
    s = re.sub(r"(?i)^(mr|mrs|miss|ms|messrs)\.? ", "", s)
    m = re.match(r"(.+?) of (.+)$", s)
    if m:
        return m.group(1), m.group(2), "", None
    m = re.match(r"(.+?),\s*([A-Z][A-Za-z.\s]*)$", s)
    if m:
        return m.group(1), None, "".join(re.findall(r"[A-Z]", m.group(2))).lower(), None
    m = re.match(r"((?:[A-Z]\.\s*)+)(.+)$", s)           # "R. Allen", "H. R. Allen"
    if m:
        return m.group(2), None, "".join(re.findall(r"[A-Z]", m.group(1))).lower(), None
    return s, None, "", None


def read_reading(rc):
    """({list: [names]}, the implied list or None, {list: [names in brackets]}). A name in brackets is printed on the
    page but is no member of the chamber that day (a misprint in the journal itself, checked against the roster):
    it's reported, not counted as the data's error."""
    lists, implied, misprints = {}, None, {}
    for line in open(os.path.join(AUDIT, os.environ.get("READ_DIR", "read"), f"{rc}.txt")):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        code, _, rest = line.partition(":")
        code = code.strip()
        if code not in LISTS:
            raise ValueError(f"{rc}: bad line {line!r}")
        if rest.strip().lower() == "implied":
            implied = code
            continue
        names = [n.strip() for n in rest.split(";") if n.strip()]
        lists[code] = [n for n in names if not n.startswith("[")]
        misprints[code] = [n.strip("[]") for n in names if n.startswith("[")]
    return lists, implied, misprints


def sample_votes(rc_ids):
    """The votes of the sampled roll calls (and any others asked for), kept in data/audit/ so a check doesn't
    read all of votes.csv.gz; rebuilt when the downloads change."""
    cache = os.path.join(ROOT, "data", "audit", "votes.csv.gz")
    want = set(rc_ids)
    sp = os.path.join(AUDIT, "sample.csv")
    if os.path.exists(sp):
        want |= {s["rc_id"] for s in csv.DictReader(open(sp))}
    src = os.path.join(DL, "votes.csv.gz")
    if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(src):
        with gzip.open(cache, "rt") as f:
            rows = list(csv.DictReader(f))
        if set(rc_ids) <= {r["rc_id"] for r in rows}:
            return rows
    with gzip.open(src, "rt") as f:
        rows = [r for r in csv.DictReader(f) if r["rc_id"] in want]
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with gzip.open(cache, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rc_id", "member_id", "vote"])
        w.writeheader()
        w.writerows(rows)
    return rows


class Data:
    def __init__(self, rc_ids):
        self.rcs = load_rollcalls()
        self.votes = collections.defaultdict(lambda: collections.defaultdict(list))
        for r in sample_votes(rc_ids):
            self.votes[r["rc_id"]][r["vote"]].append(int(r["member_id"]))
        self.people = {int(r["member_id"]): r for r in csv.DictReader(open(os.path.join(DL, "members.csv")))}
        self.keys = {m: name_keys(p["name"], p["full_name"]) for m, p in self.people.items()}
        self.terms = collections.defaultdict(list)
        self.seats = collections.defaultdict(set)
        for t in csv.DictReader(open(os.path.join(DL, "terms.csv"))):
            self.terms[(int(t["member_id"]), t["chamber"], int(t["leg"]))].append(t)
            self.seats[(t["chamber"], int(t["leg"]))].add(int(t["member_id"]))
        self.alias = collections.defaultdict(set)
        ap = os.path.join(AUDIT, "aliases.csv")
        if os.path.exists(ap):
            for a in csv.DictReader(open(ap)):
                self.alias[int(a["member_id"])].add(norm(a["printed"]))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from match_scans import presidents_pro_tem, speakers
        self.chairs = {"speaker": speakers(), "president": presidents_pro_tem()}

    def serving(self, rc):
        """The chamber's members on the roll call's day by the roster's dates (the whole legislature if none)."""
        r = self.rcs[rc]
        ms = self.seats[(r["chamber"], int(r["leg"]))]
        on = {m for m in ms if any((t["start"] or "0") <= r["date"] <= (t["end"] or "9")
                                   for t in self.terms[(m, r["chamber"], int(r["leg"]))])}
        return on or ms

    def places(self, m, rc):
        r = self.rcs[rc]
        return {norm(c) for t in self.terms[(m, r["chamber"], int(r["leg"]))]
                for c in [t["county"], t["city"]] + t["counties"].split(",") if c.strip()}

    def candidates(self, pname, rc):
        """Who a printed name can mean, found in the roster, not in the data: the chamber's members that day
        with that surname (and the county or initials the page gives), or failing an exact spelling the one
        clearly closest ("Plumley" for the roster's Plumly). Returns (members, how)."""
        from rapidfuzz import fuzz
        r = self.rcs[rc]
        sur, county, ini, special = parse_name(pname)
        if special:
            return [m for m, a, b in self.chairs[special] if a <= r["date"] <= b], "chair"
        # the roster's dates are sometimes short of the journal's (a member seen voting after his listed end):
        # failing anyone that day, the whole legislature. An exact spelling anywhere in the legislature comes
        # before a near one that day ("Woods" is Woods, off his roster dates, not Wood)
        pools = [(self.serving(rc), ""), (self.seats[(r["chamber"], int(r["leg"]))], ", off roster dates")]
        pools = [([m for m in pool if (not county or any(pl.startswith(norm(county)) for pl in self.places(m, rc)))
                   and (not ini or ini[0] in self.keys[m][1])], tag) for pool, tag in pools]
        for pool, tag in pools:
            exact = [m for m in pool if norm(sur) in self.keys[m][0] | self.alias[m]]
            if exact:
                return exact, "exact" + tag
        for pool, tag in pools:
            scored = sorted(((max(fuzz.ratio(norm(sur), k) for k in self.keys[m][0] | self.alias[m]), m) for m in pool),
                            reverse=True)
            if scored and scored[0][0] >= 75 and (len(scored) == 1 or scored[1][0] <= scored[0][0] - 8):
                return [scored[0][1]], "spelling" + tag
        return [], "unresolved"

    def compare(self, rc):
        """Each printed name resolved to a member and looked up in the data. Returns rows of (list, printed
        name or "", member id or None, verdict, how the name was resolved)."""
        lists, implied, misprints = read_reading(rc)
        data = self.votes[rc]
        vote_of = {m: c for c, ms in data.items() for m in ms}
        items = [(code, n, *self.candidates(n, rc)) for code, names in lists.items() for n in names]
        used, rows = set(), []
        # names that can mean only one member first, so a shared surname doesn't take a member another needs
        for code, n, cands, how in sorted(items, key=lambda x: len(x[2]) or 99):
            free = [m for m in cands if m not in used]
            same = [m for m in free if vote_of.get(m) == code]
            if same:
                m, verdict = same[0], "ok" if len(free) == 1 else "ok, name shared"
            elif len(free) == 1:
                m = free[0]
                verdict = f"data has {vote_of[m]}" if m in vote_of else "not in data"
            else:
                m, verdict = None, ("ambiguous, none in data" if free else "already counted" if cands else "unresolved")
            if m:
                used.add(m)
            rows.append((code, n, m, verdict, how))
        for code in lists:
            rows += [(code, "", m, "not on page", "") for m in data.get(code, []) if m not in used]
        rows += [(code, n, None, "misprint on page", "") for code, ns in misprints.items() for n in ns]
        return rows, lists, implied


def describe(d, mid):
    if mid is None:
        return ""
    p = d.people[mid]
    return f"{p['name']} ({mid})"


def check(rc_ids):
    d = Data(rc_ids)
    for rc in rc_ids:
        rows, lists, implied = d.compare(rc)
        r = d.rcs[rc]
        counts = {c: len(v) for c, v in lists.items()}
        print(f"{rc}  {r['date']}  data Y {len(d.votes[rc]['Y'])} N {len(d.votes[rc]['N'])}  page "
              + " ".join(f"{c} {n}" for c, n in counts.items()) + (f"  ({implied} implied)" if implied else ""))
        bad = [x for x in rows if x[3] != "ok" or x[4] not in ("exact", "chair", "")]
        for code, name, mid, verdict, how in sorted(bad, key=lambda x: (x[0], x[3], x[1])):
            note = f" ({how})" if how not in ("exact", "chair", "") else ""
            print(f"    {code}  {name or '-':28s} {verdict + note:40s} {describe(d, mid)}")
        if not [x for x in bad if x[3] not in ("ok", "misprint on page")]:
            print("    every name agrees")


# ---- the report

def tally_rc(rows, code_set="YN"):
    """Counts for one roll call's yea and nay lists: names read; votes wrong (another member credited with it, a vote
    the page doesn't have, yea and nay swapped) and votes lost (a member's yea or nay missing, or recorded as not
    voting). Only the wrong ones mislead the scores; a lost vote just leaves the member out of that roll call."""
    c = collections.Counter()
    for code in code_set:
        rs = [x for x in rows if x[0] == code]
        # a name not resolved to anyone, or to no one with this vote, counts as missing until it's looked at again
        miss = sum(1 for x in rs if x[3] in ("not in data", "unresolved", "ambiguous, none in data", "already counted"))
        extra = sum(1 for x in rs if x[3] == "not on page")
        swap = min(miss, extra)               # a misread name: the page's member missing, another in their place
        c["read"] += sum(1 for x in rs if x[1])
        c["wrong_member"] += swap
        c["missing"] += miss - swap
        c["extra"] += extra - swap
        c["yea_nay_swapped"] += sum(1 for x in rs if x[3] in ("data has Y", "data has N"))
        c["not_voting_in_data"] += sum(1 for x in rs if x[3] in ("data has P", "data has A", "data has E"))
    c["wrong"] = c["wrong_member"] + c["extra"] + c["yea_nay_swapped"]
    c["lost"] = c["missing"] + c["not_voting_in_data"]
    c["errors"] = c["wrong"] + c["lost"]
    return c


def report(boot=2000):
    smp = list(csv.DictReader(open(os.path.join(AUDIT, "sample.csv"))))
    done = [s for s in smp if os.path.exists(os.path.join(AUDIT, "read", f"{s['rc_id']}.txt"))]
    d = Data([s["rc_id"] for s in done])
    # a sampled roll call a later rebuild no longer verifies isn't in the published data, so it has no votes to check
    dropped = [s["rc_id"] for s in done if s["rc_id"] not in d.rcs]
    done = [s for s in done if s["rc_id"] in d.rcs]
    per, out_rows = [], []
    for s in done:
        rows, lists, implied = d.compare(s["rc_id"])
        c = tally_rc(rows)
        c["votes"] = len(d.votes[s["rc_id"]]["Y"]) + len(d.votes[s["rc_id"]]["N"])
        per.append((s, c))
        for code, name, mid, verdict, how in rows:
            out_rows.append({"rc_id": s["rc_id"], "list": code, "printed": name, "member_id": mid or "", "verdict": verdict})
    rng = np.random.default_rng(SEED)

    def rates(group):
        """Per-vote error rate, weighting each stratum by its votes; share of roll calls with an error,
        weighting by its roll calls; with a bootstrap over roll calls within strata."""
        by = collections.defaultdict(list)
        for s, c in group:
            by[(s["era"], s["chamber"], s["source"], s["group"])].append((s, c))
        def est(strata):
            vw = sum(float(v[0][0]["stratum_votes"]) for v in strata.values())
            rw = sum(float(v[0][0]["stratum_rollcalls"]) for v in strata.values())
            rate = lambda key: sum(float(v[0][0]["stratum_votes"]) * sum(c[key] for _, c in v) / max(1, sum(c["votes"] for _, c in v))
                                   for v in strata.values()) / vw
            r = sum(float(v[0][0]["stratum_rollcalls"]) * np.mean([c["errors"] > 0 for _, c in v]) for v in strata.values()) / rw
            return rate("errors"), rate("wrong"), rate("lost"), r
        e = est(by)
        bs = np.array([est({k: [v[i] for i in rng.integers(0, len(v), len(v))] for k, v in by.items()}) for _ in range(boot)])
        ci = lambda j: [float(x) for x in np.percentile(bs[:, j], [2.5, 97.5])]
        tot = sum((c for _, c in group), collections.Counter())
        return {"rollcalls": len(group), "votes_checked": tot["votes"], "names_read": tot["read"],
                **{k: tot[k] for k in ("errors", "wrong", "lost", "wrong_member", "extra", "yea_nay_swapped", "missing",
                                       "not_voting_in_data")},
                "vote_error_rate": e[0], "vote_error_ci": ci(0), "wrong_vote_rate": e[1], "wrong_vote_ci": ci(1),
                "lost_vote_rate": e[2], "lost_vote_ci": ci(2),
                "rollcalls_with_error": e[3], "rollcalls_with_error_ci": ci(3)}

    res = {"data_version": data_version(), "sample": len(smp), "done": len(done), "not_in_data": dropped, "all": rates(per),
           "by_era": {era: rates([x for x in per if x[0]["era"] == era]) for _, _, era in ERAS if any(x[0]["era"] == era for x in per)},
           "by_source": {src: rates([x for x in per if x[0]["source"] == src]) for src in ("scan", "tally", "journal")
                         if any(x[0]["source"] == src for x in per)},
           "by_check": {g: rates([x for x in per if x[0]["group"] == g]) for g in ("ok", "other") if any(x[0]["group"] == g for x in per)}}
    json.dump(res, open(os.path.join(AUDIT, "report.json"), "w"), indent=1)
    with open(os.path.join(AUDIT, "audit.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rc_id", "list", "printed", "member_id", "verdict"])
        w.writeheader()
        w.writerows(out_rows)
    a = res["all"]
    pct = lambda x, k: f"{100 * x[k]:.2f}% [{100 * x[k.replace('_rate', '_ci')][0]:.2f}, {100 * x[k.replace('_rate', '_ci')][1]:.2f}]"
    if dropped:
        print(f"{len(dropped)} read roll calls no longer in the data, left out: {' '.join(dropped)}")
    print(f"{len(done)} of {len(smp)} roll calls read: {a['names_read']:,} names; {a['wrong']} votes wrong (another member "
          f"{a['wrong_member']}, not on the page {a['extra']}, yea/nay swapped {a['yea_nay_swapped']}), {a['lost']} lost "
          f"(missing {a['missing']}, recorded as not voting {a['not_voting_in_data']})")
    print(f"  per vote, weighted: wrong {pct(a, 'wrong_vote_rate')}  lost {pct(a, 'lost_vote_rate')}"
          f"  roll calls with an error {100 * a['rollcalls_with_error']:.1f}%")
    for name, part in (("era", res["by_era"]), ("check", res["by_check"]), ("source", res["by_source"])):
        for k, x in part.items():
            print(f"  {name} {k:10s} n={x['rollcalls']:3d} names {x['names_read']:5d}  wrong {x['wrong']:2d} lost {x['lost']:3d}"
                  f"  per vote wrong {100 * x['wrong_vote_rate']:.2f}% lost {100 * x['lost_vote_rate']:.2f}%"
                  f"  roll calls {100 * x['rollcalls_with_error']:.0f}%")


def data_version():
    m = re.search(r"Data version (\S+)", open(os.path.join(DL, "README.md")).read())
    return m.group(1).rstrip(",") if m else ""


if __name__ == "__main__":
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if cmd == "sample":
        sample()
    elif cmd == "show" and args:
        dpi = int(os.environ.get("DPI", 150))
        show(args, dpi)
    elif cmd == "text" and args:
        for rc in args:
            print(f"== {rc}\n{journal_text(rc)}")
    elif cmd == "check" and args:
        check(args)
    elif cmd == "report":
        report()
    else:
        sys.exit(__doc__)

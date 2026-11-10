"""Build the site's data files (docs/data/) from the processed votes and the fit.

  index.json        legislatures, chambers, axis names, fit summary
  s/<leg><H|S>.json one legislature and chamber: members with positions, and every verified roll
                    call with its context, tallies, cutting line, and each member's vote as one
                    character per member (Y yea, N nay, P present not voting, E excused, A absent,
                    - not listed)
  about.json        the About page, with the numbers filled in
  data.json         the Data page (pipeline/data.html): the downloads, their columns, how to cite
  downloads/        CSVs for anyone who wants the data, with a README (the Data page as Markdown-free text)
trends.json, congress.json and blocs.json come from pipeline/trends.py, congress_bridge.py and blocs.py (run
them first); their numbers also go out as CSVs here.
"""
import collections, csv, gzip, json, os, re, shutil, statistics, subprocess, sys
from datetime import date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
DOCS = os.environ.get("DOCS_DIR", os.path.join(ROOT, "docs", "data"))
SESSION_NAMES = {"R": "Regular session", "A": "Adjourned session", "P": "Provisional session"}
GOOD = {"ok", "near", "ok_digit", "ok_lists", "ok_rest"}       # verified roll calls (see extract_history.py)
# the Library's party labels, spelled out where it abbreviates
PARTY_NAMES = {"PO": "Populist", "GB": "Greenback", "ID": "Independent Democrat", "RA": "Radical Republican",
               "U": "Union", "GG": "Greenback", "American Party of the United States": "American Party"}


def session_name(s):
    if s in SESSION_NAMES:
        return SESSION_NAMES[s]
    n = int(s)
    return f"{n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'} called session"


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def eras_html(axes):
    """The About page's era-by-era guide to the axes (the same wording the map pages show)."""
    from html import escape
    out = []
    for e in axes.get("eras", []):
        x, y = {**axes["x"], **e.get("x", {})}, {**axes["y"], **e.get("y", {})}
        out.append(f'<h3>{escape(e["label"])}</h3>\n<p><b>Left to right: {escape(x["name"])}.</b> {escape(x["desc"])}</p>\n'
                   f'<p><b>Up and down: {escape(y["name"])}.</b> {escape(y["desc"])}</p>')
    return "\n".join(out)


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"], 1)}
HEAD_DATE = re.compile(r"(?:(?:MON|TUES|WEDNES|THURS|FRI|SATUR|SUN)DAY[,.]?\s+)?\b(" + "|".join(MONTHS) + r")\s+([0-9Il]{1,2})[,.]\s*([0-9Il]{4})", re.I)
_heads, _heads_path = None, os.path.join(ROOT, "data", "interim", "page_dates.json")


def page_dates(rows, day_kind):
    """A journal day's PDF can run over several calendar days (the Senate recessing, not adjourning: the 68th's 2nd
    called session's 11th day is June 20-22, 1984), yet every roll call in it takes the file's date. Where the page a
    roll call is on prints a later date in its running head (within 30 days of the file's), it takes that date.
    Only files whose next file in the session starts two or more days later are read; cached in
    data/interim/page_dates.json."""
    global _heads
    import datetime as dt
    if _heads is None:
        _heads = json.load(open(_heads_path)) if os.path.exists(_heads_path) else {}
    by_file = collections.defaultdict(list)
    for r in rows:
        if day_kind.get(r["file"]) == "pdf":
            by_file[r["file"]].append(r)
    files = sorted(by_file, key=lambda f: by_file[f][0]["date"])
    n = 0
    for i, f in enumerate(files):
        d0 = by_file[f][0]["date"]
        nxt = by_file[files[i + 1]][0]["date"] if i + 1 < len(files) else None
        try:
            if nxt and (dt.date.fromisoformat(nxt) - dt.date.fromisoformat(d0)).days < 2:
                continue
        except ValueError:
            continue
        if f not in _heads:
            path = os.path.join(ROOT, "data", "raw", "journals", f)
            pages = subprocess.run(["pdftotext", "-l", "400", path, "-"], capture_output=True, text=True).stdout.split("\f")
            got = []
            for t in pages:
                m = HEAD_DATE.search(t[:300]) or HEAD_DATE.search(t[-300:])
                if m:
                    num = lambda x: int(x.translate(str.maketrans("Il", "11")))
                    try:
                        got.append(dt.date(num(m.group(3)), MONTHS[m.group(1).lower()], num(m.group(2))).isoformat())
                    except ValueError:
                        got.append(None)
                else:
                    got.append(None)
            _heads[f] = got
        heads = _heads[f]
        # a page without a head (or with a misread one) takes the last good date before it
        cur, filled = d0, []
        for h in heads:
            try:
                ok = h and 0 <= (dt.date.fromisoformat(h) - dt.date.fromisoformat(d0)).days <= 30 and h >= cur
            except ValueError:
                ok = False
            cur = h if ok else cur
            filled.append(cur)
        for r in by_file[f]:
            if r.get("page") not in (None, ""):
                pg = int(r["page"])
            else:
                fp = find_page(r, "tally" if "-HT" in r["rc_id"] else "scan")
                pg = int(fp) - 1 if fp else None
            if pg is not None and 0 <= pg < len(filled) and filled[pg] != r["date"]:
                r["date"] = filled[pg]
                n += 1
    json.dump(_heads, open(_heads_path, "w"))
    return n


def repair_dates(rows, span):
    """Dates as YYYY-MM-DD within the legislature's service dates. The modern journals' API adds a
    time ("2011-05-23 00:00:00Z"), and a few scan file names carry a wrong year ("1979" among
    1879-80 pages, "968-"), which fix_dates in extract_history doesn't catch. A date outside the
    legislature gets the year that puts it inside its session's run of dates."""
    lo, hi = span
    for r in rows:
        r["date"] = r["date"][:10]
    good = lambda d: len(d) == 10 and d[:4].isdigit() and lo <= d <= hi
    by_sess = collections.defaultdict(list)
    for r in rows:
        if good(r["date"]):
            by_sess[r["session"]].append(r["date"])
    fixed = 0
    for r in rows:
        if good(r["date"]):
            continue
        ds = sorted(by_sess.get(r["session"]) or [lo, hi])
        md = r["date"][-5:]
        cands = [f"{y}-{md}" for y in range(int(lo[:4]), int(hi[:4]) + 1) if good(f"{y}-{md}")]
        if cands:
            mid = ds[len(ds) // 2]
            r["date"] = min(cands, key=lambda c: (not ds[0] <= c <= ds[-1], abs(int(c[:4]) - int(mid[:4]))))
            fixed += 1
    return fixed


def party_code(p):
    return "R" if p.startswith("Republican") or p in ("RA", "Radical Republican") else "D" if p.startswith("Democrat") else "O"


def party_name(p):
    """The party as written out, for members outside the two major parties ("" if not known)."""
    return "" if p in ("", "Unknown", "O") else PARTY_NAMES.get(p, p)


def html_to_md(h):
    """The Data page as Markdown, for the downloads folder's README: just the few tags pipeline/data.html uses."""
    import re
    from html import unescape
    h = re.sub(r'<div class="stat">.*?</div>\s*</div>', "", h, flags=re.S)        # the tiles repeat the first paragraph
    h = re.sub(r'href="data/downloads/', 'href="', h)
    h = re.sub(r'href="#/', 'href="https://luke-thomas-more.github.io/texas-nominate/#/', h)

    def table(m):
        rows = re.findall(r"<tr>(.*?)</tr>", m.group(0), re.S)
        cells = [[re.sub(r"\s+", " ", c).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)] for r in rows]
        out = ["| " + " | ".join(cells[0]) + " |", "|" + "---|" * len(cells[0])] + ["| " + " | ".join(c) + " |" for c in cells[1:]]
        return "\n" + "\n".join(out) + "\n"
    h = re.sub(r'<div class="tablewrap"><table>.*?</table></div>', table, h, flags=re.S)
    for tag, md in (("h1", "# "), ("h2", "## "), ("h3", "### ")):
        h = re.sub(rf"<{tag}>(.*?)</{tag}>", lambda m: f"\n{md}{m.group(1)}\n", h, flags=re.S)
    h = re.sub(r"<li>(.*?)</li>", lambda m: "- " + re.sub(r"\s+", " ", m.group(1)).strip(), h, flags=re.S)
    h = re.sub(r"</?ul>", "", h)
    h = re.sub(r"<p[^>]*>(.*?)</p>", lambda m: "\n" + re.sub(r"\s+", " ", m.group(1)).strip() + "\n", h, flags=re.S)
    h = re.sub(r'<a href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", h)
    h = re.sub(r"</?code>", "`", h)
    h = re.sub(r"</?b>", "**", h)
    h = re.sub(r"</?i>", "*", h)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"\n{3,}", "\n\n", unescape(h).replace("\u00a0", " ")).strip() + "\n"


_pages, _pages_path = None, os.path.join(ROOT, "data", "interim", "journal_pages.json")


def find_page(r, source):
    """The PDF page of a roll call the modern parser read from a scan (it reads a day's text whole): a tally
    sheet's page from the day's sheet count, else the page where its record number or totals appear
    (audit.find_page). Cached in data/interim/journal_pages.json; "" when not found."""
    global _pages
    if _pages is None:
        _pages = json.load(open(_pages_path)) if os.path.exists(_pages_path) else {}
    if r["rc_id"] not in _pages:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import audit
        path = os.path.join(ROOT, "data", "raw", "journals", r["file"])
        try:
            if source == "tally":
                p = tally_page(r, path)
            else:
                p = audit.find_page({**r, "status": r.get("status", "ok")}, path)
        except Exception:
            p = None
        _pages[r["rc_id"]] = p or ""
        if len(_pages) % 500 == 0:
            json.dump(_pages, open(_pages_path, "w"))
    return _pages[r["rc_id"]]


def legiscan_html():
    """A sentence on the check against LegiScan (validate_legiscan.py), if it has been run."""
    path = os.path.join(PROC, "validation_legiscan.json")
    if not os.path.exists(path):
        return ""
    v = json.load(open(path))["totals"]
    return (f"<p>From 2013 on, the votes were also checked against <a href=\"https://legiscan.com\">LegiScan</a>'s independent "
            f"reading of the same journals (CC BY 4.0): of {v['votes_compared']:,} individual votes on {v['matched_rollcalls']:,} "
            f"roll calls found in both, {100 * v['votes_agree'] / v['votes_compared']:.2f}% agree.</p>\n")


def audit_html():
    """The Data page's account of the accuracy audit, from audit/report_fresh.json (a sample no fix has drawn on) and
    audit/report.json (the first sample, since used to find and fix errors); both from pipeline/audit.py report."""
    path = os.path.join(ROOT, "audit", "report.json")
    if not os.path.exists(path):
        return "<p>The accuracy audit isn't done yet.</p>"
    first = json.load(open(path))
    fpath = os.path.join(ROOT, "audit", "report_fresh.json")
    fresh = json.load(open(fpath)) if os.path.exists(fpath) else None
    version = json.load(open(os.path.join(ROOT, "pipeline", "dataset.json")))["version"].split()[0]
    pct = lambda x: f"{100 * x:.2f}%"
    ci = lambda x: f"{100 * x[0]:.2f}–{100 * x[1]:.2f}%"
    which = lambda r: "this release" if r["data_version"] == version else "the release before this one"
    rates = lambda r: (f"an estimated {pct(r['all']['wrong_vote_rate'])} of yeas and nays are wrong (95% interval "
                       f"{ci(r['all']['wrong_vote_ci'])}) and {pct(r['all']['lost_vote_rate'])} are lost ({ci(r['all']['lost_vote_ci'])})")

    def by_check(r):
        ok, other = r.get("by_check", {}).get("ok", {}), r.get("by_check", {}).get("other", {})
        if not (ok and other):
            return ""
        return (f"Roll calls whose counts match the printed totals exactly were almost always exactly right ({ok.get('wrong', 0)} wrong "
                f"and {ok.get('lost', 0)} lost votes in {ok.get('rollcalls', 0)} of them); the errors gather in the roll calls that "
                f"passed a looser check ({other.get('wrong', 0)} wrong and {other.get('lost', 0)} lost in {other.get('rollcalls', 0)}). ")

    kinds = ("Two kinds of error count. A <b>wrong</b> vote is credited to the wrong member, isn't on the page, or has yea and nay "
             "swapped; a <b>lost</b> vote is on the page but missing from the data or recorded as not voting. Wrong votes mislead "
             "the scores; a lost one only leaves the member out of that roll call.")
    drawn = ("drawn from every era, both chambers and each way of verifying a roll call (the looser checks oversampled), was read "
             "name by name off the journal pages and compared with the data")
    main = fresh if fresh and fresh["data_version"] == version else first
    if main is fresh:
        text = (f"<p>To measure the data's accuracy, a fresh random sample of {fresh['done']} roll calls, none of them used to find or "
                f"fix an error, {drawn} (version {fresh['data_version']}, this release): {fresh['all']['names_read']:,} names. {kinds}</p>\n"
                f"<p>Weighting each part of the sample by its share of the data, {rates(fresh)}. {by_check(fresh)}"
                f"An earlier sample of {first['done']} roll calls ({first['all']['names_read']:,} names) was read for versions 1.0 and "
                f"1.1 and then used to find and fix errors (listed in the repository's <code>audit/findings.md</code>), so it "
                f"flatters the data it helped correct: against {which(first)} it shows {pct(first['all']['wrong_vote_rate'])} wrong and "
                f"{pct(first['all']['lost_vote_rate'])} lost. The errors the fresh sample found are listed in the same file and are "
                f"still in the data. The pages read and every name are in <code>audit_fresh.csv</code> and <code>audit.csv</code>; "
                f"the readings themselves are in the repository's <code>audit/</code> folder.</p>\n")
    else:
        text = (f"<p>To measure the data's accuracy, a random sample of {first['done']} roll calls of data version {first['data_version']} "
                f"({which(first)}; the fixes it led to are listed in the repository's <code>audit/findings.md</code>), {drawn}: "
                f"{first['all']['names_read']:,} names. {kinds}</p>\n"
                f"<p>Weighting each part of the sample by its share of the data, {rates(first)}. {by_check(first)}"
                "The pages read and every name are in <code>audit.csv</code>; the readings themselves are in the repository's "
                "<code>audit/</code> folder.</p>\n")
    rows = "\n".join(f'    <tr><td>{era}</td><td class="num">{x["rollcalls"]}</td><td class="num">{x["names_read"]:,}</td>'
                     f'<td class="num">{x["wrong"]}</td><td class="num">{x["lost"]}</td></tr>' for era, x in main["by_era"].items())
    return (text + legiscan_html() +
            '<div class="tablewrap"><table>\n  <thead><tr><th>Era</th><th class="num">Roll calls read</th><th class="num">Names</th>'
            '<th class="num">Wrong</th><th class="num">Lost</th></tr></thead>\n  <tbody>\n' + rows + "\n  </tbody>\n</table></div>")


_sheets = {}


def tally_page(r, path):
    """A tally sheet's PDF page: the day's sheets are its last pages, one per record number (tally_sheets);
    when the pages that look like sheets number as many as the day's records, the k-th of them."""
    import tally_sheets
    from parse_journals import paragraphs_pdf, sheet_count
    if path not in _sheets:
        n, _ = sheet_count(" ".join(paragraphs_pdf(path)), 0)
        try:
            pages = tally_sheets.sheet_pages(path, n) if n else []
        except Exception:
            pages = []
        _sheets[path] = (n, pages, int(__import__("audit").page_count(path)))
    n, pages, n_pages = _sheets[path]
    k = int(float(r["record"]))
    if not (n and 1 <= k <= n):
        return None
    guess = pages[k - 1] + 1 if len(pages) == n else n_pages - n + k
    # the day's count of sheets can be one off: of the guess and its neighbours, the page that prints this
    # record's number (the printouts' "REC.NO. 0026") or its totals (the grids' typed summary, "051 077")
    def says(q):
        import subprocess, re
        t = subprocess.run(["pdftotext", "-f", str(q), "-l", str(q), path, "-"], capture_output=True, text=True).stdout
        hits = 0
        if re.search(rf"\b0*{k:d}\b", t) and re.search(rf"\b{k:04d}\b", t):
            hits += 2
        # the grids' number after "RECORD NO.", written by hand and read by the scan's OCR ("11", "I 2-")
        m = re.search(r"R\W?E\W?C\W?O?\W?R?\W?D\W*N\W?O\W*(.{0,60})", t, re.I | re.S)
        if m:
            run = []
            for tok in m.group(1).split():
                if re.fullmatch(r"[0-9IlO|]{1,3}\W?", tok):
                    run.append(re.sub(r"\W", "", tok).translate(str.maketrans("IlO|", "1101")))
                elif run:
                    break
            if run and "".join(run) == str(k):
                hits += 2
        for key in ("printed_y", "printed_n"):
            v = r.get(key)
            if v not in (None, "") and re.search(rf"\b{int(float(v)):03d}\b", t):
                hits += 1
        # the grids' typed caption names the bill ("HOUSE CONCURRED IN SENATE AMENDMENTS TO HB 2182")
        bill = re.match(r"([A-Z]+)\s*(\d+)", r.get("bill") or "")
        if bill and re.search(rf"\b{bill.group(1)}\s*{bill.group(2)}\b", t):
            hits += 2
        return hits
    cands = [q for q in range(guess - 3, guess + 4) if 1 <= q <= n_pages]
    best = max(cands, key=lambda q: (says(q), -abs(q - guess)))
    return best


def wnominate_fill(wn):
    """The About page's W-NOMINATE numbers (validate_wnominate.py, wnominate_trace.py), split at 1961 (the 57th
    Legislature): before it most sessions' main divide lies nearer the map's second axis."""
    keys = ("WN_N", "WN_W1_NEW", "WN_W2_NEW", "WN_N_OLD", "WN_W1_OLD", "WN_W2_OLD", "WN_AX2_OLD", "WN_HALF2")
    if not wn:
        return dict.fromkeys(keys, "–")
    new = [v for k, v in wn["by_legislature"].items() if int(k[:-1]) >= 57]
    old = [v for k, v in wn["by_legislature"].items() if int(k[:-1]) < 57]
    med = lambda vs, c: f"{statistics.median(v[c] for v in vs):.2f}"
    half = "–"
    path = os.path.join(PROC, "wnominate_trace.json")
    if os.path.exists(path):
        h = [v["halves"]["full2"] for v in json.load(open(path))["by_session"].values() if v.get("halves")]
        half = f"{statistics.median(h):.2f}" if h else half
    return dict(zip(keys, (str(len(new)), med(new, "R_w1"), med(new, "R_w2"), str(len(old)), med(old, "R_w1"), med(old, "R_w2"),
                           f"{100 * statistics.mean(45 < v['w1_angle'] < 135 for v in old):.0f}%", half)))


def sensitivity(fit_name):
    """The sensitivity refits against the map (data/processed/sensitivity_<v>.txt, from stability.py compare):
    {refit: [(era, turn in degrees, the lower of the two axes' r)]}."""
    path = os.path.join(PROC, f"sensitivity_{fit_name.replace('fit_', '')}.txt")
    if not os.path.exists(path):
        return {}
    out, cur = {}, None
    for line in open(path):
        m = re.match(r"\S+ -> fit_sens_(\w+):", line)
        if m:
            cur = out.setdefault(m.group(1), [])
            continue
        m = re.match(r"\s+(\S+)\s+n=\s*\d+\s+turn\s+([-+][\d.]+) deg\s+r dim1 ([\d.]+)\s+r dim2 ([\d.]+)", line)
        if m and cur is not None:
            cur.append((m.group(1), float(m.group(2)), min(float(m.group(3)), float(m.group(4)))))
    return out


def main():
    os.makedirs(os.path.join(DOCS, "s"), exist_ok=True)
    axes = json.load(open(os.path.join(ROOT, "pipeline", "axes.json")))
    fit = json.load(open(os.path.join(FIT, "summary.json")))
    # fits with other numbers of dimensions (same model and data, warm-started from this fit, in
    # <fit>_k1/ and <fit>_k3/), for the About page's comparison table
    for k in (1, 2, 3):
        other = os.path.join(f"{FIT}_k{k}", "summary.json")
        if os.path.exists(other):
            fit["by_dims"].update(json.load(open(other))["by_dims"])
    people = {int(r["member_id"]): r for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    terms = {(int(t["member_id"]), int(t["leg"]), t["chamber"]): t for t in csv.DictReader(open(os.path.join(PROC, "terms.csv")))}
    scores = {(int(r["member_id"]), int(r["leg"]), r["chamber"]): r for r in csv.DictReader(open(os.path.join(FIT, "member_scores.csv")))}
    params = {r["rc_id"]: r for r in csv.DictReader(open(os.path.join(FIT, "rollcall_params.csv")))}
    days_index = json.load(open(os.path.join(ROOT, "data", "raw", "journals", "index.json")))
    day_url = {d["file"]: d["url"] for d in days_index}
    day_kind = {d["file"]: d.get("kind", "pdf") for d in days_index}

    # roll calls from both pipelines: the record-vote format (1973 on) and the older scanned journals
    rcs = collections.defaultdict(list)
    excluded = collections.Counter()
    tally = collections.Counter()            # tally-sheet roll calls verified (True) or not
    votes = collections.defaultdict(dict)
    for rc_file, v_file in (("rollcalls.csv", "votes.csv.gz"), ("rollcalls_hist.csv", "votes_hist.csv.gz")):
        if not os.path.exists(os.path.join(PROC, rc_file)):
            continue
        for r in csv.DictReader(open(os.path.join(PROC, rc_file))):
            if "-HT" in r["rc_id"]:              # the House's 1973-1982 tally sheets
                tally[r["status"] in GOOD] += 1
            if r["status"] in GOOD:
                rcs[(int(r["leg"]), r["chamber"])].append(r)
            elif r["status"] != "attendance":         # (a call of the roll for attendance isn't a vote at all)
                excluded[(int(r["leg"]), r["chamber"])] += 1
        with gzip.open(os.path.join(PROC, v_file), "rt") as f:
            rd = csv.reader(f); next(rd)
            for rc_id, mid, v in rd:
                votes[rc_id][int(mid)] = "E" if v == "M" else v

    # each legislature's span, from its members' service dates
    span = {}
    for t in terms.values():
        if t.get("start") and t.get("end"):
            a, b = span.get(int(t["leg"]), (t["start"], t["end"]))
            span[int(t["leg"])] = (min(a, t["start"]), max(b, t["end"]))
    fixed = sum(repair_dates(rcs[k], span[k[0]]) for k in rcs if k[0] in span)
    redated = sum(page_dates(rcs[k], day_kind) for k in rcs)
    print(f"dated by their page's running head: {redated} roll calls")
    print(f"dates repaired: {fixed}")

    session_caps = json.load(open(os.path.join(ROOT, "data", "interim", "captions.json")))
    legislatures = []
    for (leg, ch) in sorted(rcs, key=lambda k: (-k[0], k[1])):
        rows = sorted(rcs[(leg, ch)], key=lambda r: (r["date"], r["session"] != "R", r["session"], int(r["record"] or 0), r["rc_id"]))
        # a vote that names its bill but missed the caption borrows it from another vote on the same bill
        cap_by_bill = {(r["session"], r["bill"]): r["caption"] for r in rows if r["bill"] and r["caption"]}
        for r in rows:
            if r["bill"] and not r["caption"]:
                r["caption"] = cap_by_bill.get((r["session"], r["bill"])) or \
                    session_caps.get(f"{ch}|{leg}|{r['session']}", {}).get(r["bill"], "")
        mids = sorted({m for r in rows for m in votes[r["rc_id"]]},
                      key=lambda m: people[m]["name"].split()[-1].lower())
        pos = {m: i for i, m in enumerate(mids)}
        members = []
        for m in mids:
            t = terms.get((m, leg, ch), {})
            sc = scores.get((m, leg, ch))
            members.append({"id": m, "n": people[m]["name"], "p": party_code(t.get("party", "")),
                            **({"pn": party_name(t.get("party", ""))} if party_code(t.get("party", "")) == "O" and
                               party_name(t.get("party", "")) else {}),
                            "d": t.get("district", ""),
                            "x": round(float(sc["dim1"]), 3) if sc else None,
                            "y": round(float(sc["dim2"]), 3) if sc and "dim2" in sc else None,
                            "v": int(sc["scaled_votes"]) if sc else 0})
        caps, cap_idx, days, day_idx = [], {}, [], {}
        out_rc = []
        for r in rows:
            v = ["-"] * len(mids)
            for m, code in votes[r["rc_id"]].items():
                v[pos[m]] = code
            if r["caption"] and r["caption"] not in cap_idx:
                cap_idx[r["caption"]] = len(caps); caps.append(r["caption"])
            url = day_url.get(r["file"], "")
            if url not in day_idx:
                day_idx[url] = len(days); days.append(url)
            item = {"id": r["rc_id"], "s": r["session"], "dt": r["date"], "r": int(r["record"]) if r["record"] else None,
                    "b": r["bill"], "c": cap_idx.get(r["caption"]), "t": r["text"][:300], "m": r["motion"][:300],
                    "a": r["amendment"][:240], "y": int(r["n_y"]), "n": int(r["n_n"]), "p": int(r["printed_p"]),
                    "f": day_idx[url], "v": "".join(v)}
            if r.get("page") not in (None, ""):
                item["pg"] = int(r["page"]) + 1          # page within the day's PDF, for a #page= link
            elif day_kind.get(r["file"]) == "pdf":
                pg = find_page(r, "tally" if "-HT" in r["rc_id"] else "scan")
                if pg:
                    item["pg"] = int(pg)
            p = params.get(r["rc_id"])
            if p:
                item["mid"] = [round(float(p["mid1"]), 4), round(float(p["mid2"]), 4)]
                item["spr"] = [round(float(p["spread1"]), 4), round(float(p["spread2"]), 4)]
                item["err"] = int(p["errors"])
            out_rc.append(item)
        years = sorted({r["date"][:4] for r in rows})
        label = f"{years[0]}–{str(int(years[0]) + 1)[2:]}"
        sessions = {s: session_name(s) for s in sorted({r["session"] for r in rows}, key=lambda s: (s != "R", s))}
        data = {"leg": leg, "ch": ch, "years": label, "sessions": sessions, "days": days, "caps": caps,
                "members": members, "rc": out_rc}
        with open(os.path.join(DOCS, "s", f"{leg}{ch}.json"), "w") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        entry = next((l for l in legislatures if l["leg"] == leg), None)
        if not entry:
            entry = {"leg": leg, "years": label, "chambers": {}}
            legislatures.append(entry)
        entry["chambers"][ch] = {"members": len(members), "rollcalls": len(out_rc),
                                 "scaled": sum(1 for x in out_rc if "mid" in x), "excluded": excluded[(leg, ch)]}
        print(f"{leg}{ch}: {len(members)} members, {len(out_rc)} roll calls ({entry['chambers'][ch]['scaled']} scaled)", flush=True)

    index = {"built": date.today().isoformat(), "dims": fit["chosen_dims"], "w": fit["w"], "beta": fit["beta"],
             "axes": axes, "legislatures": legislatures,
             "fit": {k: fit["by_dims"][str(k)] if str(k) in fit["by_dims"] else fit["by_dims"][k] for k in fit["by_dims"]},
             "totals": {"rollcalls": sum(c["rollcalls"] for l in legislatures for c in l["chambers"].values()),
                        "scaled": fit["roll_calls_scaled"], "members": fit["members"],
                        "both_chambers": fit["members_both_chambers"], "votes": fit["votes"],
                        "excluded": sum(excluded.values())}}
    json.dump(index, open(os.path.join(DOCS, "index.json"), "w"), indent=1, ensure_ascii=False)

    # about page with numbers filled in
    vv = json.load(open(os.path.join(PROC, "validation_voteview.json"))) if os.path.exists(os.path.join(PROC, "validation_voteview.json")) else None
    vt = json.load(open(os.path.join(PROC, "validation_texas.json"))) if os.path.exists(os.path.join(PROC, "validation_texas.json")) else None
    wn = json.load(open(os.path.join(PROC, "validation_wnominate.json"))) if os.path.exists(os.path.join(PROC, "validation_wnominate.json")) else None
    sens = sensitivity(os.path.basename(FIT))
    tmpl = open(os.path.join(ROOT, "pipeline", "about.html")).read()
    by = index["fit"]
    fill = {
        "ROLLCALLS": f"{index['totals']['rollcalls']:,}", "SCALED": f"{index['totals']['scaled']:,}",
        "MEMBERS": f"{index['totals']['members']:,}", "BOTH": f"{index['totals']['both_chambers']}",
        "VOTES": f"{index['totals']['votes']:,}", "EXCLUDED": f"{index['totals']['excluded']:,}",
        "FIRST_ORD": ordinal(min(l["leg"] for l in legislatures)), "LAST_ORD": ordinal(max(l["leg"] for l in legislatures)),
        "YEAR0": min(l["years"][:4] for l in legislatures), "YEAR1": str(max(int(l["years"][:4]) for l in legislatures)),
        "AXES_ERAS": eras_html(axes),
        "CORRECT1": f"{100 * by['1']['correct']:.1f}" if "1" in by else "–", "APRE1": f"{by['1']['apre']:.2f}" if "1" in by else "–",
        "CORRECT2": f"{100 * by['2']['correct']:.1f}" if "2" in by else "–", "APRE2": f"{by['2']['apre']:.2f}" if "2" in by else "–",
        "CORRECT3": f"{100 * by['3']['correct']:.1f}" if "3" in by else "–", "APRE3": f"{by['3']['apre']:.2f}" if "3" in by else "–",
        "W2": f"{fit['w'][1]:.2f}" if len(fit["w"]) > 1 else "–", "BETA": f"{fit['beta']:.1f}",
        "VV_R1": f"{vv['k1']['corr_dim1']:.3f}" if vv and "k1" in vv else "–",
        "VV_RW": f"{min(vv['k1']['corr_dim1_within_party'].values()):.2f}" if vv and "k1" in vv else "–",
        "VV_R21": f"{vv['k2']['corr_dim1']:.3f}" if vv and "k2" in vv else "–",
        "VV_R2": f"{vv['k2']['corr_dim2']:.2f}" if vv and "k2" in vv else "–",
        "VV_ROT": f"{abs(vv['k2']['rotation_deg']):.0f}" if vv and "k2" in vv else "–",
        "VV_CONG": f"{vv['congresses'][0]}th–{vv['congresses'][1]}th" if vv else "–",
        "SM_N": str(vt["matched_legislators"]) if vt else "–", "SM_R": f"{vt['corr_overall']:.2f}" if vt else "–",
        "SM_RD": f"{vt['corr_within_D']:.2f}" if vt else "–", "SM_RR": f"{vt['corr_within_R']:.2f}" if vt else "–",
        "BUILT": index["built"],
        "TALLY_SHARE": f"{round(10 * tally[True] / (tally[True] + tally[False]))} in 10" if tally else "–",
        **wnominate_fill(wn),
        "SENS_R": f"{min(r for s in ('ok_only', 'no_implied') for _, _, r in sens.get(s, [])):.2f}" if sens else "–",
        "SENS_T": f"{max(abs(t) for s in ('ok_only', 'no_implied') for _, t, _ in sens.get(s, [])):.0f}" if sens else "–",
        "SENS_TALLY_R": f"{min(r for e, _, r in sens.get('no_tally', []) if e >= '1973'):.2f}" if sens else "–",
        "SENS_TALLY_T": f"{max(abs(t) for e, t, _ in sens.get('no_tally', []) if e >= '1973'):.0f}" if sens else "–",
        "SENS_TALLY_T0": f"{max(abs(t) for e, t, _ in sens.get('no_tally', []) if e < '1973'):.0f}" if sens else "–",
    }
    for k, v in fill.items():
        tmpl = tmpl.replace("{{" + k + "}}", v)
    for k, v in axes.items():
        if not isinstance(v, dict):          # "eras": the per-era wording, used by the app
            continue
        for kk, vv2 in v.items():
            tmpl = tmpl.replace("{{AX_" + k.upper() + "_" + kk.upper() + "}}", vv2)
    json.dump({"html": tmpl}, open(os.path.join(DOCS, "about.json"), "w"), ensure_ascii=False)

    # downloads
    dl = os.path.join(DOCS, "downloads")
    os.makedirs(dl, exist_ok=True)
    # each member's place, with the standard errors of the fit (member_se.csv, written by fit_texas) where it has them
    se_path = os.path.join(FIT, "member_se.csv")
    se = {r["member_id"]: r for r in csv.DictReader(open(se_path))} if os.path.exists(se_path) else {}
    with open(os.path.join(FIT, "member_scores.csv")) as fin, open(os.path.join(dl, "member_scores.csv"), "w", newline="") as fout:
        rd = csv.DictReader(fin)
        se_cols = [c for c in next(iter(se.values()), {}) if c.startswith("se")]
        w = csv.DictWriter(fout, rd.fieldnames + se_cols)
        w.writeheader()
        for r in rd:
            w.writerow({**r, **{c: se.get(r["member_id"], {}).get(c, "") for c in se_cols}})
    if os.path.exists(os.path.join(ROOT, "audit", "audit.csv")):
        shutil.copy(os.path.join(ROOT, "audit", "audit.csv"), os.path.join(dl, "audit.csv"))
    if os.path.exists(os.path.join(ROOT, "audit", "audit_fresh.csv")):
        shutil.copy(os.path.join(ROOT, "audit", "audit_fresh.csv"), os.path.join(dl, "audit_fresh.csv"))
    # how much of each legislature's record the data holds: roll calls verified and kept, and those the scans held
    # that failed the checks and were left out
    with open(os.path.join(dl, "coverage.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["leg", "chamber", "first_year", "last_year", "rollcalls_kept", "rollcalls_left_out", "share_kept"])
        for leg, ch in sorted(set(rcs) | set(excluded)):
            a, b = span.get(leg, ("", ""))
            kept, out = len(rcs.get((leg, ch), [])), excluded.get((leg, ch), 0)
            w.writerow([leg, ch, a[:4], b[:4], kept, out, f"{kept / (kept + out):.3f}" if kept + out else ""])
    voters = {m for vs in votes.values() for m in vs}
    with open(os.path.join(dl, "members.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["member_id", "name", "full_name", "lrl_url"])
        for m in sorted(people):
            w.writerow([m, people[m]["name"], people[m]["full_name"], f"https://lrl.texas.gov/legeLeaders/members/memberDisplay.cfm?memberID={m}"])
    with open(os.path.join(dl, "terms.csv"), "w", newline="") as f:
        cols = ["member_id", "chamber", "leg", "district", "start", "end", "party", "city", "county", "counties", "note"]
        w = csv.writer(f); w.writerow(cols)
        for key in sorted(terms):
            w.writerow([terms[key].get(c, "") for c in cols])
    status = collections.Counter()
    with gzip.open(os.path.join(dl, "rollcalls.csv.gz"), "wt", newline="") as f:
        cols = ["rc_id", "chamber", "leg", "session", "date", "record", "bill", "caption", "text", "motion", "amendment", "n_y", "n_n", "printed_p", "status", "implied"]
        w = csv.writer(f); w.writerow(cols + ["source", "journal_url", "journal_page", "mid1", "mid2", "spread1", "spread2"])
        for key in sorted(rcs):
            for r in rcs[key]:
                p = params.get(r["rc_id"], {})
                status[r["status"]] += 1
                source = "tally" if "-HT" in r["rc_id"] else "journal" if day_kind.get(r["file"]) == "html" else "scan"
                page = int(r["page"]) + 1 if r.get("page") not in (None, "") else \
                    find_page(r, source) if day_kind.get(r["file"]) == "pdf" else ""
                w.writerow([r.get(c, "") for c in cols] + [source, day_url.get(r["file"], ""), page,
                                                          p.get("mid1", ""), p.get("mid2", ""), p.get("spread1", ""), p.get("spread2", "")])
    if _pages is not None:
        json.dump(_pages, open(_pages_path, "w"))
    n_votes = 0
    with gzip.open(os.path.join(dl, "votes.csv.gz"), "wt", newline="") as f:
        w = csv.writer(f); w.writerow(["rc_id", "member_id", "vote"])
        ok = {r["rc_id"] for v in rcs.values() for r in v}
        for rc_id, vs in votes.items():
            if rc_id in ok:
                for m, code in vs.items():
                    w.writerow([rc_id, m, code]); n_votes += 1
    # the analyses' numbers, as CSVs
    extra = {}
    for name in ("trends", "congress", "blocs"):
        path = os.path.join(DOCS, f"{name}.json")
        extra[name] = json.load(open(path)) if os.path.exists(path) else None
    if extra["trends"]:
        with open(os.path.join(dl, "trends.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["leg", "chamber", "year", "democrats", "republicans", "others", "mean_dim1_D", "mean_dim1_R", "gap_sd", "overlap",
                        "contested_votes", "party_line_share", "party_loyalty_D", "party_loyalty_R"])
            for r in extra["trends"]["texas"]:
                w.writerow([r["leg"], r["ch"], r["year"], r["n"].get("D", 0), r["n"].get("R", 0), r["n"].get("O", 0),
                            r["mean"].get("D", [""])[0], r["mean"].get("R", [""])[0], r.get("gap", ""), r.get("overlap", ""),
                            r["contested"], r.get("unity_votes", ""), r.get("unity_D", ""), r.get("unity_R", "")])
    if extra["congress"]:
        with open(os.path.join(dl, "congress_bridge.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["member_id", "name", "tx_first_year", "tx_last_year", "tx_dim1", "tx_dim2", "voteview_name", "congress_first_year",
                        "congress_last_year", "party_in_congress", "voteview_dim1", "voteview_dim2"])
            for q in extra["congress"]["pairs"]:
                w.writerow([q["id"], q["n"], *q["tx_years"], *q["tx"], q["vv_name"], *q["vv_years"], q["party"], *q["vv"]])
    if extra["blocs"]:
        with open(os.path.join(dl, "blocs.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["leg", "chamber", "member_id", "bloc", "blocs_in_chamber", "bloc_size", "bloc_cohesion"])
            for key, b in sorted(extra["blocs"]["sessions"].items(), key=lambda kv: (int(kv[0][:-1]), kv[0][-1])):
                for i, bl in enumerate(b["blocs"]):
                    for m in bl["members"]:
                        w.writerow([key[:-1], key[-1], m, "ABC"[i], b["k"], len(bl["members"]), bl["cohesion"]])

    # the Data page
    # the dataset's author, license and DOI (pipeline/dataset.json); until they're set the page says so, and no
    # CITATION.cff is written
    meta = json.load(open(os.path.join(ROOT, "pipeline", "dataset.json")))
    meta["author"] = meta.get("author") or "TX-NOMINATE"
    meta["license_html"] = meta.get("license_html") or "Not yet set."
    labels = {"rollcalls.csv.gz": "every verified roll call: date, bill, the journal's wording, totals, how it was verified, its journal page",
              "votes.csv.gz": "every member's vote on every one of them", "members.csv": "the legislators, with their Library ids",
              "terms.csv": "who served when: chamber, legislature, district, party, home county",
              "member_scores.csv": "each member's place on the map, by legislature", "trends.csv": "party gaps and party-line votes by legislature",
              "congress_bridge.csv": "members who also served in Congress, with their Voteview scores",
              "blocs.csv": "each member's voting bloc, by legislature and chamber",
              "coverage.csv": "by legislature and chamber: roll calls kept, and those the scans held that failed the checks",
              "audit.csv": "the accuracy audit: every name read off the journal pages for a random sample of roll calls, and what the data has",
              "audit_fresh.csv": "the same for the fresh sample read for version 1.2, which no fix has drawn on"}
    size = lambda n: f"{os.path.getsize(os.path.join(dl, n)) / 1e6:.1f}&nbsp;MB" if os.path.getsize(os.path.join(dl, n)) >= 1e5 else f"{os.path.getsize(os.path.join(dl, n)) / 1e3:.0f}&nbsp;KB"
    files = "\n".join(f'    <tr><td><a href="data/downloads/{n}">{n}</a></td><td>{labels[n]}</td><td class="num">{size(n)}</td></tr>'
                      for n in labels if os.path.exists(os.path.join(dl, n)))
    y0, y1 = int(fill["YEAR0"]), int(fill["YEAR1"])
    cite = (f'{meta["author"]} ({index["built"][:4]}). <i>TX-NOMINATE: Texas Legislature roll-call votes and ideal points, {y0}–{y1}</i> '
            f'(data version {meta["version"]}). https://luke-thomas-more.github.io/texas-nominate/' + (f' doi:{meta["doi"]}' if meta.get("doi") else ""))
    dfill = {"ROLLCALLS": fill["ROLLCALLS"], "VOTES": f"{n_votes:,}", "MEMBERS_ALL": f"{len(voters):,}", "YEARS": str(y1 - y0 + 1),
             "YEAR0": str(y0), "YEAR1": str(y1), "FILES": files, "VERSION": meta["version"], "BUILT": index["built"], "EXCLUDED": fill["EXCLUDED"],
             "N_OK": f"{status['ok']:,}", "N_NEAR": f"{status['near']:,}", "N_DIGIT": f"{status['ok_digit']:,}", "N_LISTS": f"{status['ok_lists']:,}",
             "N_REST": f"{status['ok_rest']:,}", "PAIRS": str(len(extra["congress"]["pairs"])) if extra["congress"] else "–",
             "CITATION": cite, "DOI_NOTE": f'DOI: <a href="https://doi.org/{meta["doi"]}">{meta["doi"]}</a>.' if meta.get("doi") else "",
             "LICENSE": meta["license_html"], "AUDIT": audit_html()}
    dt = open(os.path.join(ROOT, "pipeline", "data.html")).read()
    for k, v in dfill.items():
        dt = dt.replace("{{" + k + "}}", v)
    json.dump({"html": dt}, open(os.path.join(DOCS, "data.json"), "w"), ensure_ascii=False)
    open(os.path.join(dl, "README.md"), "w").write(html_to_md(dt))
    if meta.get("license_spdx") and meta["author"] != "TX-NOMINATE":
        cff = [f'cff-version: 1.2.0', f'message: "If you use these data, please cite them as below."', f'type: dataset',
               f'title: "TX-NOMINATE: Texas Legislature roll-call votes and ideal points, {y0}-{y1}"',
               f'authors:', f'  - name: "{meta["author"]}"', f'version: "{meta["version"]}"', f'date-released: {index["built"]}',
               f'license: {meta["license_spdx"]}', f'url: "https://luke-thomas-more.github.io/texas-nominate/"',
               f'repository-code: "https://github.com/luke-thomas-more/texas-nominate"'] + ([f'doi: {meta["doi"]}'] if meta.get("doi") else [])
        if DOCS == os.path.join(ROOT, "docs", "data"):           # (not for a preview build)
            open(os.path.join(ROOT, "CITATION.cff"), "w").write("\n".join(cff) + "\n")
    print("site data written")


if __name__ == "__main__":
    main()

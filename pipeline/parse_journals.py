"""Extract every record vote from the downloaded journals (modern era).

Each journal day becomes a list of paragraphs (from HTML, or from the PDF text layer where no
HTML exists), then a single pass walks the paragraphs tracking the bill under consideration, the
latest amendment and motion, and picks out each record vote with its printed totals and the
names in each category. Names stay raw here; pipeline/match_votes.py resolves them to members.

Writes data/interim/rollcalls_raw.jsonl and data/interim/statements_raw.jsonl.
"""
import html, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tally_sheets

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "journals")
OUT = os.environ.get("PARSE_OUT", os.path.join(ROOT, "data", "interim"))   # PARSE_OUT: a trial run elsewhere

DASH = r"\s*[—–-]+\s*"
# House list labels, longest first so "Absent, Excused, Committee Meeting" wins over "Absent"
HOUSE_LISTS = [("Absent, Excused, Committee Meeting", "M"), ("Absent, Excused", "E"),
               ("Present, not voting", "P"), ("Yeas", "Y"), ("Nays", "N"), ("Yea", "Y"), ("Nay", "N"), ("Absent", "A")]
SENATE_LISTS = [("Present-not voting", "P"), ("Present - not voting", "P"), ("Present, not voting", "P"),
                ("Absent-excused", "E"), ("Absent - excused", "E"), ("Absent, excused", "E"),
                ("Yeas", "Y"), ("Nays", "N"), ("Yea", "Y"), ("Nay", "N"), ("Absent", "A")]
HOUSE_LIST_RE = re.compile(r"^(" + "|".join(re.escape(l).replace(r"\ ", r"\s*") for l, _ in HOUSE_LISTS) + ")" + DASH + r"(.*)$", re.I)
SENATE_LIST_RE = re.compile(r"(?:^|(?<=\.)\s+)(" + "|".join(re.escape(l).replace(r"\ ", r"\s*").replace(r"\-", r"\s*-\s*") for l, _ in SENATE_LISTS) + r")\s*:\s*", re.I)

HOUSE_VOTE_RE = re.compile(r"\(Record\s+(?:No\.\s*)?(\d+)\)\s*:\s*(\d+)\s+Yeas?\s*,\s*(\d+)\s+Nays?"
                           r"(?:\s*,\s*(\d+)\s+Present\s*,?\s*not\s+voting)?", re.I)
# The scanned years (before 1995) as their text layer reads them: "(Record 34 ); 13 7 Yeas. I Nay, J Present,
# not voting.", "(Record Ill): 27 Yeas, 117 N<Jys", "Ycas - Agnich; ...", "Present. not voting- Mr. Speaker(C)".
OCR_NUM = r"[\dIlJ|Oo]{1,3}(?:\s[\dIlJ|Oo]{1,2})?"
HOUSE_VOTE_OCR_RE = re.compile(r"\(\s*Rec?c?ord\s*(?:No\.?\s*)?(" + OCR_NUM + r")\s*\)\s*[:;.,]?\s*(" + OCR_NUM + r")?\s*(Y\s?[ec]\s?as?)\b\s*[,.;:]?\s*"
                               r"(" + OCR_NUM + r")?\s*(N\S{0,2}ys?)\b(?:\s*[,.;:~]?\s*(" + OCR_NUM + r")?\s*Present\W{0,2}\s*not\s+voting)?")
HOUSE_LIST_OCR = [("M", r"Absent\W{0,2}\s*Excused\W{0,2}\s*Committee\s*Meeting"), ("E", r"Absent\W{0,2}\s*Excused"),
                  ("P", r"Present\w?\W{0,2}\s*not\s*voting"), ("Y", r"Y\s?[ec]\s?as?"), ("N", r"Nays?"), ("A", r"Absent")]
HOUSE_LIST_OCR_RE = re.compile(r"^(?:" + "|".join(f"(?P<{c}>{p})" for c, p in HOUSE_LIST_OCR) + r")(?:\s*[—–~-]+\s*|\s+(?=[A-Z]))(.*)$")


def ocr_count(s, word=""):
    """An OCR'd count ("I 06" = 106, "Ill" = 111, "J" = 1); a missing one before a singular "Nay" is 1."""
    if not s:
        return 1 if re.fullmatch(r"\S*a[yv]|Y\s?[ec]\s?a", word) else None
    s = re.sub(r"\s", "", s).translate(str.maketrans("IlJ|Oo", "111100"))
    return int(s) if s.isdigit() else None


SENATE_VOTE_RE = re.compile(r"(?:by\s+the\s+following\s+(?:record\s+)?vote|vote\s+was\s+as\s+follows)\s*:?\s*"
                            r"Yeas\s+(\d+)\s*,\s*Nays\s+(\d+)(?:\s*,\s*Present\s*[-,]?\s*not\s+voting\s+(\d+))?", re.I)
SAME_AS_RE = re.compile(r"same as previous roll call", re.I)

BILL_RE = re.compile(r"\b(C\.?\s?S\.?\s?)?(H|S)\.?\s?(B|J\.?\s?R|C\.?\s?R|R)\.?\s*(?:No\.\s*)?(\d+)\b")
LONG_BILL_RE = re.compile(r"\b(COMMITTEE SUBSTITUTE (?:FOR )?)?(HOUSE|SENATE)\s+(BILL|JOINT RESOLUTION|CONCURRENT RESOLUTION|RESOLUTION)\s+(?:NO\.\s*)?(\d+)\b", re.I)
KIND = {"B": "B", "JR": "JR", "CR": "CR", "R": "R", "BILL": "B", "JOINT RESOLUTION": "JR",
        "CONCURRENT RESOLUTION": "CR", "RESOLUTION": "R"}
HEADER_RE = re.compile(r"^(?:\w+day,?\s+)?\w+\s+\d{1,2},?\s+\d{4}\s+(?:HOUSE|SENATE)\s+JOURNAL(?:\s*[—–-]+\s*\d+\w*\s+Day)?\s*\d*$"
                       r"|^\d*\s*\d+(?:st|nd|rd|th)\s+LEGISLATURE\s*[—–-]+\s*.*SESSION\s*\d*$"
                       r"|^(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY),?\s+\w+\s+\d{1,2},?\s+\d{4}\s+\d+$"
                       r"|^\d+\s+SENATE JOURNAL\s*[—–-]+\s*.*SESSION$"
                       r"|^\d*\s*\d+(?:st|nd|rd|th)\s+Legislature\s*[—–-]+\s*.*Session(?:\s+\d+(?:st|nd|rd|th)\s+Day)?\s*\d*$", re.I)


def bill_ref(text):
    """First bill mentioned, normalized: 'CSHB 327' -> 'HB 327', 'SENATE BILL 1903' -> 'SB 1903'."""
    m = BILL_RE.search(text)
    if m:
        kind = re.sub(r"[\s.]", "", m.group(3)).upper()
        return f"{m.group(2).upper()}{KIND.get(kind, kind)} {int(m.group(4))}"
    m = LONG_BILL_RE.search(text)
    if m:
        return f"{m.group(2)[0].upper()}{KIND[m.group(3).upper()]} {int(m.group(4))}"
    return None


def paragraphs_html(raw):
    s = re.sub(r"(?s)<!--.*?-->", " ", raw)
    # the 2003-09 Senate journals pad text with invisible white "i"s: <FONT color=White>ii</FONT>
    s = re.sub(r"(?is)<font[^>]*color\s*=\s*[\"']?white[\"']?[^>]*>.*?</font>", " ", s)
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br[^>]*>", " ", s)
    s = re.sub(r"(?i)</?(p|div|tr|td|table|hr|h\d|li|ul|center)\b[^>]*>", " ", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    out = []
    for p in s.split(" "):
        p = re.sub(r"\s+", " ", p.replace("\xa0", " ")).strip()
        if p and not HEADER_RE.match(p):
            out.append(p)
    return out


def paragraphs_pdf(path):
    txt = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True).stdout
    paras, cur = [], ""
    for line in txt.splitlines():
        if not line.strip() or HEADER_RE.match(line.strip()):
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent >= 3 and cur:
            paras.append(cur)
            cur = body
        elif cur.endswith("-") and re.search(r"[A-Za-z]-$", cur):
            cur = cur[:-1] + body if body[:1].islower() else cur + body
        else:
            cur = (cur + " " + body).strip()
    if cur:
        paras.append(cur)
    return [re.sub(r"\s+", " ", p) for p in paras]


WHOLE_NAME = re.compile(r"(?:Mr\.\s+Speaker|Mr\.\s+President|[A-Z][\w'’\- ]*?(?:,\s*(?:[A-Z]\.\s?){1,3})?(?:\(C\))?)\.?")


def trim_last(elem):
    """The last name in a list can run into the next heading ('Thompson, S. Amendment No. 2'):
    keep the shortest prefix ending at a period that is a complete name."""
    for m in re.finditer(r"\.\s+", elem):
        head = elem[:m.start() + 1].strip()
        if re.fullmatch(r"(?:[A-Z]\.?\s*){1,3}", elem[m.end():].strip()):
            continue                       # "Solis, J. F.": more initials, not the end of the list
        if head in ("Mr.", "Mrs.", "Ms.") or re.fullmatch(r"(?:.*,\s*)?[A-Z]\.", head) and not re.search(r",\s*(?:[A-Z]\.\s?)+$", head):
            continue
        if WHOLE_NAME.fullmatch(head):
            return head
    return elem


def split_names(chamber, s):
    s = s.strip()
    if not s or s.lower() in ("none", "none."):
        return []
    # OCR in the scanned years: ";" read as ":" ("Agnich: Aikin"), "," as "." ("Cuellar. R",
    # "West. Whitmire"), "(C)" as "( C)"
    s = re.sub(r"\(\s*C\s*\)", "(C)", s)
    if chamber == "H":
        parts = []
        for p in re.split(r"[;:]", s):
            p = re.sub(r"^(\s*[A-Z][\w'’ -]*[a-z])\.\s*([A-Z])\.?\s*$", r"\1, \2.", p)    # "Cuellar. R" = "Cuellar, R."
            t = trim_last(p)
            parts.append(t.strip())
            if t.strip() != p.strip().rstrip(".").strip() and t.strip() != p.strip():
                break                      # the list ended inside this piece ("Wilson. Floor Packet Page No. 3: ...")
    else:
        parts = re.split(r",|(?<=[a-z]{2})\.\s+(?=[A-Z])", s)
        if parts:
            parts[-1] = trim_last(parts[-1])
    return [p.strip().rstrip(".").strip() for p in parts if p.strip().rstrip(".").strip() and p.strip().lower() not in ("none", "none.")]


def senate_lists(text):
    """'Yeas: A, B. Nays: C.' -> {'Y': [...], 'N': [...]}; labels may share one paragraph."""
    found = {}
    marks = list(SENATE_LIST_RE.finditer(text))
    for i, m in enumerate(marks):
        label = re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", m.group(1)).lower())
        code = next((c for l, c in SENATE_LISTS if re.sub(r"\s*-\s*", "-", l.lower()) == label), None)
        if code is None:                     # odd spacing or case: go by the words
            code = ("P" if "present" in label else "E" if "excused" in label else "Y" if label.startswith("yea")
                    else "N" if label.startswith("nay") else "A")
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        found.setdefault(code, []).extend(split_names("S", text[m.end():end]))
    return found


def is_heading(p):
    """A standalone heading: short, capitalized (sponsor notes in parentheses aside), not bill text."""
    head = p.split("(")[0].strip()
    if re.match(r"^(?:C\.?S\.?)?[HS]\.?\s?(?:B|J\.?R|C\.?R|R)\.?\s*\d+\s*[-–—]\s*", head) and len(p) < 160:
        return True                                   # "HR 2377 - ADOPTED", "HB 1 - (consideration continued)"
    return (3 < len(head) < 110 and head.upper() == head and re.search(r"[A-Z]{3}", head)
            and not head.endswith(".") and not re.match(r"^(SECTION|SECTIONS|ARTICLE|SUBCHAPTER|CHAPTER|PART|SUBSECTION)\b", head))


def parse_day(day, paras, captions):
    """captions: bill -> caption, shared across the days of a session so third-reading votes get them."""
    chamber = day["chamber"]
    scanned = day["leg"] < 74          # OCR'd text layer: digits, labels and punctuation come garbled
    votes, statements = [], []
    ctx = {"bill": None, "heading": None, "caption": None, "amendment": None, "motion": None}
    prev_named = None   # last Senate vote with names, for "same as previous roll call"
    i = 0
    while i < len(paras):
        p = paras[i]
        # --- context tracking: a heading starts a new item of business
        if is_heading(p):
            bill = bill_ref(p.split("(")[0])
            if bill is None and ctx["bill"] and len(p.split("(")[0].split()) <= 2:
                # a subheading ("ADOPTION", "PASSAGE") within the same item of business
                ctx.update({"amendment": None, "motion": None})
            else:
                ctx = {"bill": bill, "heading": p[:160], "caption": captions.get(bill), "amendment": None, "motion": None}
        elif re.match(r"^(?:C\.?S\.?)?[HS]\.?\s?[BJCR]", p) and bill_ref(p[:25]) and \
                re.match(r"^[^,]{2,30},\s*(A bill to be entitled|A (joint |concurrent )?[Rr]esolution|[A-Z][a-z]+ing\b)", p):
            # "SB 40, A bill to be entitled ...", "HR 2377, Impeaching Warren Kenneth Paxton ..."
            bill = bill_ref(p[:25])
            cap = re.split(r",\s*", p, maxsplit=1)[1][:400]
            captions[bill] = cap
            if ctx["bill"] in (None, bill):
                ctx["bill"], ctx["caption"] = bill, cap
        if ctx["bill"] and not ctx["caption"]:
            # "...called up the privileged resolution HR 2377, impeaching Warren Kenneth Paxton, ..."
            pat = re.escape(ctx["bill"]).replace("\\ ", r"\s*")
            m = re.search(r"\b" + pat + r"\s*,\s*([a-z]+ing\b[^.;]{10,300})", p)
            if m:
                ctx["caption"] = m.group(1)[0].upper() + m.group(1)[1:]
                captions.setdefault(ctx["bill"], ctx["caption"])
        if re.search(r"\boffered the following amendment", p, re.I):
            ctx["amendment"] = p[:300]
            ctx["motion"] = None
        if re.search(r"\b(moved|moves)\b", p) and len(p) < 600:
            ctx["motion"] = p[:400]

        # --- statements of vote (House)
        if chamber == "H":
            sm = re.match(r"^When Record No\.\s*(\d+) was taken,(.*)$", p, re.I)
            if sm and i + 1 < len(paras):
                statements.append({"chamber": "H", "leg": day["leg"], "session": day["session"], "date": day["date"],
                                   "record": int(sm.group(1)), "text": p[:500], "name": paras[i + 1][:80]})

        # --- record votes
        if chamber == "H":
            m = HOUSE_VOTE_RE.search(p)
            if m:
                record, printed = int(m.group(1)), {"Y": int(m.group(2)), "N": int(m.group(3)), "P": int(m.group(4) or 0)}
            elif scanned:
                m = HOUSE_VOTE_OCR_RE.search(p)
                if m:
                    record = ocr_count(m.group(1))
                    printed = {"Y": ocr_count(m.group(2), m.group(3)), "N": ocr_count(m.group(4), m.group(5)), "P": ocr_count(m.group(6)) or 0}
                    m = m if record else None
            if m:
                rv = {"chamber": "H", "leg": day["leg"], "session": day["session"], "date": day["date"],
                      "file": day["file"], "record": record, "printed": printed,
                      "text": p[:m.start()].strip(" ,:(").strip()[:500], **ctx, "lists": {}}
                rv["text"] = re.sub(r"\s*by\s*$", "", rv["text"])
                j, last, text = i + 1, None, {}
                while j < len(paras):
                    q = paras[j]
                    lm = HOUSE_LIST_RE.match(q)
                    om = None if lm or not scanned else HOUSE_LIST_OCR_RE.match(q)
                    if lm or om:
                        if lm:
                            label = re.sub(r"\s+", " ", lm.group(1)).lower()
                            code, names = next(c for l, c in HOUSE_LISTS if l.lower() == label), lm.group(2)
                        else:
                            code, names = next(c for c, _ in HOUSE_LIST_OCR if om.group(c)), om.groups()[-1]
                        text[code] = (text.get(code, "") + "; " + names) if code in text else names
                        last = code
                        j += 1
                        continue
                    # a list broken by a page boundary continues in a paragraph of bare names,
                    # sometimes mid-name ("Smith," | "W.; Solomons; ...")
                    if last and re.match(r"^[A-Z][A-Za-zÀ-ÿ'\-. ]*(?:,\s*(?:[A-Z]\.\s*){1,3})?(?:\(C\))?[;.]", q) and not HOUSE_VOTE_RE.search(q) \
                            and not (scanned and HOUSE_VOTE_OCR_RE.search(q)):
                        text[last] = text[last] + " " + q
                        j += 1
                        continue
                    break
                rv["lists"] = {code: split_names("H", t) for code, t in text.items()}
                votes.append(rv)
                i = j
                continue
            tm = TALLY_RE.search(p) if day["leg"] <= 67 else None
            if tm:
                # 1979-1982: "... was lost by: Record No. 2 (Appendix, March 21)", the totals after it ("36 Yeas",
                # "96 Nays", "18 Not Voting"), the names on the day's tally sheet (filled in by main())
                tail = " ".join([p[tm.end():]] + paras[i + 1:i + 4])
                y = re.match(r"\W*(?:\w+\W+){0,4}?(" + OCR_NUM + r")\s*(Y\s?[ec]\s?as?)\b\W*(" + OCR_NUM + r")?\s*(N\S{0,2}ys?)\b", tail)
                if y and ocr_count(tm.group(1)):
                    rv = {"chamber": "H", "leg": day["leg"], "session": day["session"], "date": day["date"],
                          "file": day["file"], "record": ocr_count(tm.group(1)),
                          "printed": {"Y": ocr_count(y.group(1), y.group(2)), "N": ocr_count(y.group(3), y.group(4)), "P": 0},
                          "text": re.sub(r"\s*by\s*:?\s*$", "", p[:tm.start()].strip(" ,:(").strip())[:500], **ctx,
                          "lists": {}, "tally": True}
                    votes.append(rv)
        else:
            m = SENATE_VOTE_RE.search(p)
            if m:
                rv = {"chamber": "S", "leg": day["leg"], "session": day["session"], "date": day["date"],
                      "file": day["file"], "record": None,
                      "printed": {"Y": int(m.group(1)), "N": int(m.group(2)), "P": int(m.group(3) or 0)},
                      "text": p[:m.start()].strip(" ,:").strip()[:500], **ctx, "lists": {}}
                tail = p[m.end():]
                rv["lists"] = senate_lists(tail)
                same = bool(SAME_AS_RE.search(tail))
                j = i + 1
                last = list(rv["lists"])[-1] if rv["lists"] and not tail.rstrip().endswith(".") else None
                while j < len(paras):
                    q = paras[j]
                    if SENATE_LIST_RE.match(q) or SAME_AS_RE.search(q[:60]):
                        if SAME_AS_RE.search(q[:60]):
                            same = True
                        found = senate_lists(q)
                        for code, names in found.items():
                            rv["lists"].setdefault(code, []).extend(names)
                        last = list(found)[-1] if found and not q.rstrip().endswith(".") else None
                    elif last and re.fullmatch(r"[A-Z][\w'’. -]*(?:,\s*[A-Z][\w'’. -]*)*\.?", q) and len(q) < 400:
                        # a list cut by a page break continues with bare names
                        rv["lists"][last].extend(split_names("S", q))
                        last = None if q.rstrip().endswith(".") else last
                    else:
                        break
                    j += 1
                rv["seq"] = len(votes) + 1
                if same:
                    rv["same_as"] = prev_named
                votes.append(rv)
                if any(rv["lists"].values()) or same:
                    prev_named = rv["seq"]
                i = j
                continue
        i += 1
    return votes, statements


MODERN_HOUSE_RE = re.compile(r"\(Record\s+(?:No\.\s*)?\d+\)\s*:")
# 1973-1982: the text gives each record vote's totals and points to a tally sheet printed at the back
# of the day ("Record No. 2 (Appendix, March 29)", in 1973 "Record No.2 (page 1896)"); tally_sheets.py
# reads the 1979-1982 sheets
TALLY_RE = re.compile(r"Re?[ce]{1,2}[o0]rd\s*No\W{0,2}\s*([\dIlO]{1,3}(?:\s[\dIlO]{1,2})?)\s*[,(;j]?\s*\(?\s*(?:Appendix|page|p\s?age)\b", re.I)   # "Reeord No. I I"
MODERN_SENATE_RE = re.compile(r"Yeas\s+\d+\s*,\s*Nays\s+\d+")
SENATE_COLON_LIST_RE = re.compile(r"\b(?:Yeas|Nays|Absent-excused|Present-not voting)\s*:\s*[A-Z]")
_FORMAT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "interim", "modern_format.json")


_format = None


def modern_format(day):
    """Does this journal day use the modern record-vote format ("(Record 81): 87 Yeas, 57 Nays" in
    the House, "Yeas 30, Nays 0." in the Senate)? True for every day from 1995 on; for scanned
    days it's read from the text layer (the switch came sometime between 1973 and 1987)."""
    global _format
    if day["leg"] >= 74:
        return True
    if _format is None:
        try:
            _format = json.load(open(_FORMAT_CACHE))
        except (OSError, ValueError):
            _format = {}
    if day["file"] not in _format:
        path = os.path.join(RAW, day["file"])
        txt = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True).stdout if os.path.exists(path) else ""
        if day["chamber"] == "H":
            _format[day["file"]] = bool(MODERN_HOUSE_RE.search(txt) or TALLY_RE.search(txt))
        else:   # not just "the vote was announced Yeas 59, Nays 58": the lists must be the colon kind too
            _format[day["file"]] = bool(MODERN_SENATE_RE.search(txt) and SENATE_COLON_LIST_RE.search(txt))
        if len(_format) % 200 == 0:
            save_format_cache()
    return _format[day["file"]]


def save_format_cache():
    if _format is not None:
        os.makedirs(os.path.dirname(_FORMAT_CACHE), exist_ok=True)
        tmp = f"{_FORMAT_CACHE}.{os.getpid()}.tmp"
        json.dump(_format, open(tmp, "w"))
        os.replace(tmp, _FORMAT_CACHE)


def main():
    index = json.load(open(os.path.join(RAW, "index.json")))
    # scanned days count only from the first legislature that used the modern format (checked per day)
    first_modern = int(os.environ.get("MODERN_FROM", 60))
    index = [d for d in index if d["leg"] >= 74 or (d["leg"] >= first_modern and modern_format(d))]
    if os.environ.get("LEGS"):              # e.g. LEGS=63-67 CHAMBERS=H, for trial runs
        a, b = map(int, os.environ["LEGS"].split("-"))
        index = [d for d in index if a <= d["leg"] <= b and d["chamber"] in os.environ.get("CHAMBERS", "HS")]
    save_format_cache()
    os.makedirs(OUT, exist_ok=True)
    rc_out = open(os.path.join(OUT, "rollcalls_raw.jsonl"), "w")
    st_out = open(os.path.join(OUT, "statements_raw.jsonl"), "w")
    n, captions, session = 0, {}, None
    all_captions = {}
    tally = []          # the session's tally-sheet record votes and their sheets' rows, written at its end
    for day in sorted(index, key=lambda d: (d["chamber"], d["leg"], d["session"], d["date"], d["file"])):
        path = os.path.join(RAW, day["file"])
        if not os.path.exists(path):
            continue
        if (day["chamber"], day["leg"], day["session"]) != session:
            n += write_tally(tally, rc_out)
            tally = []
            session, captions = (day["chamber"], day["leg"], day["session"]), {}
            all_captions["|".join(map(str, session))] = captions
        if day["kind"] == "html":
            paras = paragraphs_html(open(path, encoding="utf-8", errors="replace").read())
        else:
            paras = paragraphs_pdf(path)
        votes, statements = parse_day(day, paras, captions)
        held = [v for v in votes if v.pop("tally", False)]
        if held:
            # the day's highest record number counts its sheets, quorum calls (no totals) included
            n_sheets = max(ocr_count(m.group(1)) or 0 for m in TALLY_RE.finditer(" ".join(paras)))
            sheets = tally_sheets.read_day(os.path.abspath(path), n_sheets,
                                           {v["record"]: (v["printed"]["Y"], v["printed"]["N"]) for v in held})
            tally += [(v, sheets.get(v["record"], [])) for v in held]
        for v in votes:
            if not any(v is h for h in held):
                rc_out.write(json.dumps(v) + "\n")
        for s in statements:
            st_out.write(json.dumps(s) + "\n")
        n += len(votes) - len(held)
    n += write_tally(tally, rc_out)
    json.dump(all_captions, open(os.path.join(OUT, "captions.json"), "w"))
    print("record votes:", n)


_terms = None


def write_tally(tally, out):
    """Write a session's tally-sheet record votes, their sheets' rows resolved to members together
    (tally_sheets.resolve): "members" holds them for match_votes, "lists" the names as read."""
    global _terms
    if not tally:
        return 0
    from match_scans import speakers
    from match_votes import load_roster
    if _terms is None:
        _terms = load_roster()[1]
    first = tally[0][0]
    chair = [m for m, a, b in speakers() if any(a <= v["date"] <= b for v, _ in tally)]
    members = tally_sheets.resolve({k: rows for k, (v, rows) in enumerate(tally) if rows},
                                   _terms.get(("H", first["leg"]), []), chair, ordered=first["leg"] <= 65)
    for k, (v, rows) in enumerate(tally):
        v["lists"] = {}
        for pos, name, code in rows:
            if name and code:
                v["lists"].setdefault(code, []).append(name)
        v["members"] = {str(m): code for m, code in members.get(k, {}).items()}
        out.write(json.dumps(v) + "\n")
    return len(tally)


if __name__ == "__main__":
    main()

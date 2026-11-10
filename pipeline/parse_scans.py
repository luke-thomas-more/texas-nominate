"""Extract record votes from the scanned journals (1846-1993), using scan_layout's reading order.

Two layouts occur:
  running text (19th century): "Yeas—Messrs. Adams, Bourland, ... and Willie—16."
  column lists (later):        a "Yeas—140" heading over columns of names, then "Nays—8", ...
Each roll call keeps its printed totals (the checksum), the raw names in each list, and the
sentences before it (what was being voted on). Names stay raw; matching happens later, against
the session roster, with OCR errors in mind.

Writes data/interim/scan_rollcalls_raw.jsonl (same shape as rollcalls_raw.jsonl).
Usage: python pipeline/parse_scans.py [legs, e.g. 1,12,24 or 1-73]
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_layout
from parse_journals import bill_ref

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "journals")
OUT = os.path.join(ROOT, "data", "interim")

RUNNING_HEAD = re.compile(r"^\W{0,3}(\d{1,4}\W{0,3})?(HOUSE|SENATE|H0USE|SENATR)\s*J0?OURNAL\b.{0,40}$|^.{0,30}\b(HOUSE|SENATE)\s+JOURNAL\W{0,3}\d{0,4}\W{0,3}$", re.I)
DASH = r"\s*[-—–=~_:.,]{1,3}\s*"
# OCR-tolerant list labels: Yeas, Nays, Absent, Present (not voting), Excused, Paired
LABELS = [
    ("Y", r"Y[a-z]{1,2}[s8z5&]|Yea|Ayes"),         # OCR: "Yuas", "Yeaa", "Ycas", "Yea&"
    ("N", r"N[a-z]{1,2}[s8z5&]|Nay|Noes"),
    ("P", r"Present[\s,—–-]*(?:and\s+)?not\s+voting|Present"),
    ("E", r"Absent[\s,—–-]*(?:and\s+)?excused|Excused|Absent\s+on\s+leave"),
    # 1881: "NOT VOTING—6." (present or not, the journal doesn't say); 1885: "Absent, not voting"
    ("A", r"Absent[\s,—–-]*(?:and\s+)?not\s+voting|Absent|Not\s+voting"),
    ("R", r"Paired"),
]
LABEL_RE = re.compile(r"^(" + "|".join(f"(?P<{c}>{p})" for c, p in LABELS) + r")(?:" + DASH + r"(?P<n>[\dIlOoSsZz&]{1,3}))?\W{0,3}$", re.I)
# running text: "Yeas—Messrs. Adams, Bourland, ... and Willie—16." (1863: "Yeas: ... Way. Total-70." / "Making 44.")
# (its total may come apart in the scan: "Zoeller--4 7.", "Worsham-6 l."). A list doesn't run on into the
# next roll call's yeas (1852: a nay list whose total the scan lost, "...and White u'", took the next vote's
# yeas and their total for its own): a capital Y word and a dash ("YEAs-", not "Hayes," OCR'd "IIo.yes,")
NEXT_YEAS = r"\b(?-i:Y[a-zA-Z]{1,2}[s8z5&S]|Ayes|AYES)\s*[-—–=~_:]"
RUN_RE = re.compile(r"\b(?P<label>" + "|".join(p for _, p in LABELS) + r")" + DASH +
                    r"[^A-Za-z]{0,4}(?:Messrs\W{0,3}|Mr\.?|M\W{1,3}|Senators?)?[^A-Za-z]{0,4}(?![a-z]?n[o0]n[ec]\b)"
                    r"(?P<names>[A-Z](?:(?!" + NEXT_YEAS + r")[^—]){0,3000}?)" + r"\s*(?:[-—–~]{1,3}|[.,;:]?\s*(?:T[o0]ta[l1I]|Making|In\s+all)\W{0,3})\s*(?:[YN][a-zA-Z]{2,4}\.?\s+)?(?P<n>(?=[0-9IlOoS]{0,2}\d)(?:[0-9IlOoS]\s[0-9IlOoS](?![A-Za-z0-9])|[0-9IlOoS]{1,3}))\b", re.I | re.S)   # "...Whitmore—Yeas 21."
# a nay heading OCR'd past the label patterns ("NA vs-", "Na.ya-", "NA-XS-", "N[1ys-", "1;t:~AYS-"), looked for
# only straight after a yea list's total: a short run of anything, a dash, then "Messrs." or "Senators"
GARBLED_NAY_RE = re.compile(r"[^A-Za-z0-9]{0,6}?(?P<label>[^\s—–]{1,9}?(?:\s[^\s—–]{1,4}){0,2}?)" + DASH +
                            r"[^A-Za-z]{0,4}(?:\S{1,6}rs\b\W{0,3}|Senators?\b\W{0,3})[^A-Za-z]{0,4}(?P<names>[A-Z][^—]{0,3000}?)"
                            r"\s*(?:[-—–~]{1,3}|[.,;:]?\s*(?:T[o0]ta[l1I]|Making|In\s+all)\W{0,3})\s*"
                            r"(?P<n>(?=[0-9IlOoS]{0,2}\d)(?:[0-9IlOoS]\s[0-9IlOoS](?![A-Za-z0-9])|[0-9IlOoS]{1,3}))\b", re.S)
# a nay list straight after a yea total, its own total lost ("...Winkler and "\V" oocl-3n. On motion, ..."): its
# names up to where prose starts or the next yeas, and no total. Left out, the vote read as unanimous and passed
# (1873 House: 30-35 as 30-0); with its names and no total it fails, and another reading's total may check it
# (1853 Senate: "Nay—Mr. Taylor. A bill authorizing ..." is one nay, not 69; 1875 Senate: "Nays—wNone. Senator
# Ireland introduced ..."); not at a name with its county after an OCR'd stop ("Allen. Smith of Rusk", where
# "Amendments of the House" is prose)
PROSE_STARTS = r"(?-i:\.\s+(?:(?:[A-Z][a-z]+|A)\s+(?!o[ft]\s+[A-Z])[a-z]|(?:Senator|Mr\.?)\s+[A-Z][a-z]+\s+[a-z]|(?:The|On|So|In)\b))"
OTHER_HEADS = r"\b(?:" + "|".join(p for c, p in LABELS if c not in "YN") + r")\s*[-—–=~_:]"     # "Absent--Bowers"
NAY_NO_TOTAL_RE = re.compile(r"[^A-Za-z0-9]{0,6}?\b(?P<label>" + LABELS[1][1] + r")" + DASH +
                             r"[^A-Za-z]{0,4}(?:Messrs\W{0,3}|Mr\.?\s*|Senators?\W{0,3})?[^A-Za-z]{0,4}(?![a-z]?n[o0]n[ec]\b)"
                             r"(?P<names>[A-Z](?:(?!" + NEXT_YEAS + "|" + OTHER_HEADS + "|" + PROSE_STARTS + r")[^—]){0,1500})(?P<n>)", re.I | re.S)
# so too a list straight after a yea total whose heading and title are both garbled ("lf.AYs-Messrs.", "NA
# YS-l\:fessrs.", "NA Ys--Mesf!r:'."; 1846-66): after the yeas it's the nays, unless the heading is more like an
# absentee or pairs heading; with no title (the 1870s Senate's "Na.ys---Broughton, ...-12."), only a heading like
# "nays". Its total is taken when one closes it, else none (1858 House: 37-31 read as 37-0)
NAY_TITLED_RE = re.compile(r"[^A-Za-z]{0,10}?(?P<label>(?=[^\s—–]{0,8}[A-Za-z])[^\s—–]{1,9}?(?:\s[^\s—–]{1,4}){0,2}?)" + DASH +
                           r"[^A-Za-z]{0,4}(?P<title>\S{0,5}(?:ssr|sr[s:]|esf|Mes)\S{0,4}(?=[\s.,:;])|Senators?\b)?\W{0,3}[^A-Za-z]{0,4}(?![a-z]?n[o0]n[ec]\b)"
                           r"(?!\S{1,9}\s*[-—–=~_:]{1,3}\s*(?:\S{0,5}(?:ssr|sr[s:]|esf|Mes)|Senators?))"     # not the heading itself
                           r"(?P<names>(?-i:[A-Z])(?:(?!" + NEXT_YEAS + "|" + OTHER_HEADS + "|" + PROSE_STARTS + r")[^—]){0,1500}?)"
                           r"(?:\s*[-—–~]{1,3}\s*(?P<n>(?=[0-9IlOoS]{0,2}\d)[0-9IlOoS]{1,3})\b|(?=" + NEXT_YEAS + "|" + OTHER_HEADS + "|" + PROSE_STARTS + r"|—|$))", re.I | re.S)
# and one whose heading has no dash after it at all ("Nay 5 NTGRRES, Allen, Armstrong, ... Whi taker—I18,"; 1853
# Senate: 12-18 read as 12-0): a word plainly "nays", up to two words of a garbled title (no small letters in
# them), then a list, two names and their commas first; not "None", nor a list under another heading run on to
# its first name ("Navs-None. Ab;ent-Bowers, Clark, Gaines, Latimer-4"); its total after a dash or, in 1846, a
# semicolon ("Nays; Senators, Bagby, ... and Wood; 14."). With its total misread too, the roll call fails, and
# another reading may check it
NONE = r"(?!(?i:[a-z]?n[o0]n[ec])\b)"
NAY_LOOSE_RE = re.compile(r"[^A-Za-z0-9]{0,6}?(?P<label>(?-i:N)[A-Za-z]{1,4})\b[^A-Za-z]{0,4}"
                          r"(?:(?-i:M)\S{0,6}rs\b\W{0,3}|Mr\.?\s+|Senators?\b\W{0,3})?(?:" + NONE + r"[^a-z\s]{1,9}\s+){0,2}?"
                          + NONE + r"(?!\S{1,9}?[-—–~=_:]{1,3}\s*[A-Z])(?=(?:(?-i:[A-Z])[^\s,—]{1,15}(?:\s+\S{1,10})?,\s*){2})"
                          r"(?P<names>(?-i:[A-Z])(?:(?!" + NEXT_YEAS + "|" + OTHER_HEADS + "|" + PROSE_STARTS + r")[^—]){0,1500}?)"
                          r"(?:\s*(?:[-—–~]{1,3}|;)\s*(?P<n>(?=[0-9IlOoS]{0,2}\d)[0-9IlOoS]{1,3})\b|(?=" + NEXT_YEAS + "|" + OTHER_HEADS + "|" + PROSE_STARTS + r"|—|$))", re.S)
# 1879: the total printed after the list's last name ("Wurzbach--84."), or alone in a cell ("--84.")
TAIL_COUNT = re.compile(r"^(?P<name>.*?[A-Za-z][.,)]?)?\s*[-—–~=]{1,3}(?:\s[-—–~=]{1,2})?\s*(?P<n>(?=[0-9IlOoSsZz&]{0,2}\d)[0-9IlOoSsZz&]{1,3})\W{0,2}$")
OCR_DIGITS = str.maketrans({"I": "1", "l": "1", "O": "0", "o": "0", "S": "5", "s": "5", "&": "8", "Z": "2", "z": "2"})
# ordinary words that give prose away (no one- or two-letter words: OCR leaves fragments like that in names)
FUNCTION_WORDS = {"the", "was", "were", "that", "which", "for", "this", "with", "from", "bill", "motion", "vote",
                  "following", "moved", "not", "have", "has", "and", "been", "said", "upon", "when", "would"}


def label_code(m):
    for c, _ in LABELS:
        if m.group(c):
            return c


LABEL_WORDS = [("Y", "yeas"), ("Y", "yea"), ("Y", "ayes"), ("N", "nays"), ("N", "nay"), ("N", "noes"),
               ("P", "present not voting"), ("P", "present"), ("P", "in the chair"),
               ("E", "absent excused"), ("E", "excused"), ("E", "absent on leave"),
               ("A", "absent not excused"), ("A", "absent not voting"), ("A", "absent and not voting"), ("A", "absent"),
               ("A", "not voting"), ("R", "paired")]


def nay_like(label):
    """Is a heading no label pattern took ("NA vs", "Na.ya", "N[1ys", "1;t:~AYS") a nay heading? Nearer "nays"
    than any heading but the yeas' (it follows a yea list, and "fays" is as near "ayes")."""
    from rapidfuzz import fuzz
    t = re.sub(r"[^a-z]", "", label.lower())
    if not 2 <= len(t) <= 6:
        return False
    nay = max(fuzz.ratio(t, w) for w in ("nays", "nay", "noes"))
    if t[0] == "n" and len(t) <= 5:
        return True                         # "N.us-Messrs.": no other heading is a short word in N
    return nay >= 70 and nay > max(fuzz.ratio(t, w.replace(" ", "")) for c, w in LABEL_WORDS if c not in "YN")


def nays_word(label):
    """Is a heading with no dash after it plainly "nays" ("Nay", "Nars", "Navys")? With neither a dash nor a title
    to go on, stricter than nay_like ("Not voting" is a short word in N too)."""
    from rapidfuzz import fuzz
    return bool(re.fullmatch(LABELS[1][1], label, re.I)) or max(fuzz.ratio(label.lower(), w) for w in ("nays", "nay")) >= 75


def other_heading(label):
    """Is a garbled heading after a yea list another roll call's yeas, or more like an absentee, excused or
    pairs heading than the nays?"""
    from rapidfuzz import fuzz
    t = re.sub(r"[^a-z]", "", label.lower())
    if re.match(r"y[eao]|ayes?$", t):
        return True
    if len(t) < 3:
        return False
    other = max(fuzz.ratio(t, w.replace(" ", "")) for c, w in LABEL_WORDS if c not in "YN")
    return other >= 60 and other > max(fuzz.ratio(t, w) for w in ("nays", "nay", "noes"))


def cell_label(text):
    """('Y', 140) for "Yeas—140", ('A', None) for "Absent, not exeuseu.", else None."""
    from rapidfuzz import fuzz
    t = re.sub(r"[^A-Za-z0-9&]+", " ", text).strip()
    t = re.sub(r"(?<=[A-Za-z])1(?=[A-Za-z]|\s)", "l", t)          # "Nay1l-26" -> "Nayll 26"
    t = re.sub(r"(?<=[A-Za-z])&(?=\s|$)", "s", t)                  # "Yea&-21" -> "Yeas 21" (1% of headings)
    # "Nays—z2."; or a letter glued to the total after the dash, a misread digit or a stray mark ("Nays—T7", 1959
    # House: 7; "Yeas—i18." in a Senate of 31): the count's readings (match_scans.readings) try it both ways; or,
    # straight after the dash, a total read all in letters ("Nays—IO.", "Nays—lz.", "Nays-I.")
    m = re.match(r"^([A-Za-z ]+?)\s*(?:((?=[0-9IlOoSsZz&]{0,2}\d)[0-9IlOoSsZz&]{1,3})|(?<=\s)([A-Za-z]\d{1,3})"
                 r"|(?<=\s)((?=[IliOoSsZz]?[Ili])[IliOoSsZz]{1,2}))?$", t)
    if not m or len(m.group(1).split()) > 4:
        return None
    if m.group(4) and not re.search(r"[A-Za-z][.,]?[-—–=~]{1,3}\s*" + m.group(4) + r"\W{0,3}$", text.strip()):    # not "NAYl:l."
        return None
    raw_n = m.group(2) or m.group(3) or m.group(4)
    n = count(m.group(2)) if m.group(2) else int(m.group(3)[1:]) if m.group(3) else \
        count(m.group(4).replace("i", "1")) if m.group(4) else None
    words = m.group(1).lower().strip()
    none = re.fullmatch(r"([a-z ]+?)\s+n[o0]ne", words)
    if none and not raw_n:
        # "Nays—none." (1879): an empty nay list, its total zero (only nays: "~AYs-none" is as close
        # to "ayes" as to "nays", and no vote here ever had no yeas)
        best = max(fuzz.ratio(none.group(1).replace(" ", ""), w) for w in ("nays", "nay", "noes"))   # "N .A. Ys"
        return ("N", 0, "none") if best >= 75 else None
    if raw_n:
        # badly OCR'd one-word headings with a count: "iays-14", "Ycas—87", "Yeaa—12"; "Neys—28", "WAYS—14", "~Tn,ys-55."
        # (fuzzily only 75 from "nays", so their totals were lost), and after a stray mark: "i KAYS-5."
        for word in dict.fromkeys([words, re.sub(r"^[a-z]\s+(?=[a-z]{3})", "", words)]):    # ("y eaa--27" as it is)
            if re.fullmatch(r"(?:[nilh1x]\s?a|n\s?[en]|[kw]\s?a|t\s?n)\s?[yv]\s?(?:[s5z8abe]|ll|li|il|ii)", word):  # "Nayll-54", "Naya—16", "Xays"
                return "N", n, raw_n
            if re.fullmatch(r"[yv]\s?[eco]\s?[ao]\s?(?:[s5az8be]|ll|li|il|ii)", word):  # "Ycas—87", "Yeaa—12", "Yeab—9"
                return "Y", n, raw_n
    best = max(LABEL_WORDS, key=lambda lw: fuzz.ratio(words, lw[1]))
    if fuzz.ratio(words, best[1]) >= 82:
        if best[0] in ("Y", "N") and n is None:
            return None                     # yea/nay headings carry a count; '"aye"' alone is prose
        return best[0], n, raw_n
    return None


def normalize_ocr(text):
    """Systematic OCR artifacts: a capital M read as "l\\I", "l\\l", "\\I" or "\\f" ("l\\Ioore" -> "Moore",
    "l\\lills", 1893), a W as "\\V" or "\\v" ('"\\Valker', "0\\vens"), an O as a zero ("0wens")."""
    text = re.sub(r"[l;.:\"'\u2018\u2019]*\\[Ifl]", "M", text)
    text = re.sub(r"[\"'\u2018\u2019]*\\V", "W", text).replace("\\v", "w")
    return re.sub(r"(?<!\w)0(?=[a-z]{2})", "O", text)


def clean_cell(text):
    """Drop OCR junk around a name: ".Smith of Colorado." -> "Smith of Colorado"."""
    return re.sub(r"^[^A-Za-z(]+|[^A-Za-z.)]+$", "", normalize_ocr(text)).strip()


def count(s):
    try:
        return int(re.sub(r"\s", "", s or "").translate(OCR_DIGITS))
    except ValueError:
        return None


def is_name_cell(text):
    """Could this cell be a name (however mangled)? Prose gives itself away by length or by
    ordinary words ("the", "moved", "bill"); anything else inside a list is kept for matching."""
    text = clean_cell(text)
    words = re.findall(r"[A-Za-z][A-Za-z'’.-]*", text)
    if not words:
        return True                                   # a stray mark ("·") inside a list
    fw = sum(1 for w in words if w.lower().strip(".") in FUNCTION_WORDS)
    if fw >= 2 or (fw and len(words) >= 3):
        return False
    if len(words) > 6:
        # names run together across tight columns (1885: "Bell, Jones of Panola, Pendleton of Bell"),
        # every comma-separated piece of it a short name
        pieces = [p for p in text.split(",") if p.strip()]
        return len(pieces) >= 2 and all(len(re.findall(r"[A-Za-z][A-Za-z'’.-]*", p)) <= 4 for p in pieces)
    return True


def line_label(ln):
    """A list heading, if this line has one and nothing else on it reads as prose."""
    found = None
    for c in ln["cells"]:
        lab = cell_label(c["text"])
        if lab and not found:
            found = lab
        elif not is_name_cell(c["text"]):
            return None
    return found


def caps_heading(ln):
    """("Y", None, None) for a yea or nay heading set alone in capitals with no count: the total
    then follows the list's last name instead (1879: "YEAS." or "AYES." over columns of names
    ending "Tilson—27.")."""
    from rapidfuzz import fuzz
    cells = [c["text"] for c in ln["cells"] if len(re.findall(r"[A-Za-z]", c["text"])) >= 3]    # not stray marks
    if len(cells) != 1:
        return None
    t = re.sub(r"[^A-Za-z]", "", cells[0])
    if not 3 <= len(t) <= 6 or not t[0].isupper():
        return None
    u = re.sub(r"^X", "N", t.upper())                  # "XAYS": N read as X
    # ties go to the word with the same first letter ("NAYS" over "AYES" for "NAYS" read "NAYB")
    code, word = max((("Y", "YEAS"), ("Y", "AYES"), ("N", "NAYS"), ("N", "NOES")),
                     key=lambda cw: (fuzz.ratio(u, cw[1]), cw[1][0] == u[0]))
    # small capitals come back in mixed case ("NaYs."); a yea heading needs at least two capitals,
    # since one in ordinary type ("Yeas.") may sit over a list in running text (a nay heading
    # only ever continues a roll call already begun)
    if code == "Y" and sum(ch.isupper() for ch in t) < 2:
        return None
    return (code, None, None) if fuzz.ratio(u, word) >= 75 else None


def is_name_line(ln):
    # prose that OCR split into one-word cells ("Mr." | "Smith" | "offered" | "the" | "following")
    # passes cell by cell, so ordinary words are also counted across the whole line
    words = re.findall(r"[A-Za-z][A-Za-z'’.-]*", " ".join(c["text"] for c in ln["cells"]))
    if sum(1 for w in words if w.lower().strip(".") in FUNCTION_WORDS) >= 2:
        return False
    return all(is_name_cell(c["text"]) for c in ln["cells"]) and not line_label(ln)


def at(ln):
    """Where a line sits on its page (column: L, R or F for full width; height in points): two readings
    of one roll call put its heading in the same spot, two roll calls never do."""
    return ln["col"], round(ln["y"])


def context_before(lines, i, limit=6):
    """The sentences just before line i: what was being voted on."""
    prev = []
    j = i - 1
    while j >= 0 and len(prev) < limit:
        t = lines[j]["text"]
        if is_name_line(lines[j]) and len(lines[j]["cells"]) > 1:
            break
        prev.insert(0, t)
        j -= 1
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", " ".join(prev))
    text = re.sub(r"\s+", " ", text).strip()
    sents = re.split(r"(?<=[.:;])\s+(?=[A-Z])", text)
    return sents[-3:]


def join_of(cells):
    """Put a county back with its "of" when OCR split them into two cells ("of" | "McCulloch.")."""
    out = []
    for c in cells:
        if out and re.search(r"(?:^|\s)(?:of|or|ot|nf)$", clean_cell(out[-1]["text"])):
            out[-1] = dict(out[-1], text=out[-1]["text"] + " " + c["text"], x1=c["x1"])
        else:
            out.append(c)
    return out


BROKEN = "\u2016"      # between a name broken at a line's end and what may be the rest of it ("Barbee of Free‖stone")


def unglue_hyphens(cells):
    """Split a cell that runs a hyphenated line end into the next sub-column's name (1893: "Barbee of
    Free- Moody,", "Henderson of Mi- Simmons of Gray-"); the second piece's left edge is estimated."""
    out = []
    for c in cells:
        t = c["text"]
        cuts = sorted({m.end() for m in re.finditer(r"(?<=[a-z]-)\s+(?=[A-Z])", t)} |
                      {m.end() for m in re.finditer(r"\bo[ft]\s+[A-Z][a-z]{0,12}-(?=[A-Z][a-z])", t)})   # "of La-Tankersley"
        if not cuts:
            out.append(c)
            continue
        per = (c["x1"] - c["x0"]) / max(len(t), 1)
        start = 0
        for k in cuts + [len(t)]:
            piece = t[start:k].strip()
            if piece:
                out.append(dict(c, text=piece, x0=c["x0"] + per * start, x1=c["x0"] + per * k))
            start = k
    return out


def column_lists(lines):
    """Column-list roll calls: a label line ("Yeas—140") followed by lines of names."""
    votes, i = [], 0
    while i < len(lines):
        lab = line_label(lines[i]) or caps_heading(lines[i])
        if not lab or lab[0] != "Y":
            i += 1
            continue
        start = i
        rv = {"printed": {}, "lists": {}, "page": lines[i]["page"], "at": at(lines[i]), "labels": [],
              "context": context_before(lines, i)}
        code, above = None, []
        punctuated = [0, 0]                # of the names so far, how many end in a comma or period
        while i < len(lines):
            lab = line_label(lines[i]) or caps_heading(lines[i])
            if lab:
                if lab[0] == "Y" and "Y" in rv["lists"] and "N" not in rv["lists"]:
                    lab = ("N",) + tuple(lab[1:])          # a nay heading misread as yeas: a new roll call opens with prose
                if lab[0] in rv["lists"] and lab[0] in ("Y", "N"):
                    break                                  # the next roll call's list
                code = lab[0]
                rv["lists"].setdefault(code, [])
                rv["labels"].append((code, lines[i]["page"], *at(lines[i])))
                if lab[1] is not None:
                    rv["printed"][code] = lab[1]
                    rv.setdefault("printed_raw", {})[code] = lab[2]
                # names sharing the label's line (other cells) belong to the list above or this one
                for c in lines[i]["cells"]:
                    if not cell_label(c["text"]) and not caps_heading({"cells": [c]}) and clean_cell(c["text"]) \
                            and is_name_cell(c["text"]):
                        rv["lists"][code].append(clean_cell(c["text"]))
                i += 1
                continue
            if code and is_name_line(lines[i]):
                placed = []
                for c in unglue_hyphens(join_of(lines[i]["cells"])):
                    tail = TAIL_COUNT.match(c["text"].strip()) if code not in rv["printed"] else None
                    if tail:                                   # a list headed without its total
                        rv["printed"][code] = count(tail.group("n"))
                        rv.setdefault("printed_raw", {})[code] = tail.group("n")
                        c = dict(c, text=tail.group("name") or "")
                    raw = c["text"].strip()
                    txt = clean_cell(raw)
                    if not txt:
                        continue
                    open_above = [a for a in above if a[2]]
                    of = re.match(r"^(?:of|or|ot|nf|o f)\b|^of(?=[A-Z])", txt)
                    if re.match(r"^[a-z]{2,}", txt) and not of and open_above:
                        # "Barbee of Free-" / "stone,": the rest of a word broken at the line's end, indented
                        # under it in the same sub-column. Marked, not joined: the matcher keeps the join only
                        # when it reads as a member ("Moore-" may be a stray mark and "ge" junk)
                        a = min(open_above, key=lambda a: abs(a[0] - c["x0"]))
                        lst, k = a[1]
                        rv["lists"][lst][k] += BROKEN + txt
                        above = [b for b in above if b is not a]
                        placed.append((a[0], (lst, k), raw.endswith("-"), raw, c["x1"]))
                        continue
                    if of and raw.endswith("-") and placed and punctuated[1] >= 6 and punctuated[0] >= 0.7 * punctuated[1] \
                            and not re.search(r"[,.;:]$", placed[-1][3]) \
                            and c["x0"] - placed[-1][4] < min([abs(a[0] - c["x0"]) for a in above] or [1e9]):
                        # "Jackson" | "of Bur-": the name's own county, set off by a wide space on the same
                        # line and broken at its end: an unfinished name (where names end in a comma or
                        # period) just to its left, nearer than the start of the sub-column above
                        # ("Graves" | "of Angelina." is "Renfro" / "of Angelina." in the next sub-column)
                        lst, k = placed[-1][1]
                        rv["lists"][lst][k] += " " + re.sub(r"^(?:of|or|ot|nf|o f)\s*", "of ", txt)
                        placed[-1] = (placed[-1][0], (lst, k), raw.endswith("-"), raw, c["x1"])
                        continue
                    if of and above:
                        # "Hughes" / "of Grayson": continues the name directly above, in the same sub-column
                        j = min(above, key=lambda a: abs(a[0] - c["x0"]))[1]
                        lst, k = j
                        rv["lists"][lst][k] += " " + re.sub(r"^(?:of|or|ot|nf|o f)\s*", "of ", txt)
                    else:
                        rv["lists"][code].append(txt)
                        placed.append((c["x0"], (code, len(rv["lists"][code]) - 1), raw.endswith("-"), raw, c["x1"]))
                        punctuated[0] += bool(re.search(r"[,.;:]$", raw)); punctuated[1] += 1
                above = placed or above
                i += 1
                continue
            break
        if "Y" in rv["lists"] and "Y" in rv["printed"]:
            rv["style"] = "columns"
            votes.append(rv)
        if i == start:
            i += 1
    return votes


TWO_WORD_COUNTY = {"red", "san", "van", "jim", "live", "palo", "el", "la", "de", "fort", "deaf", "las", "jeff", "tom"}


def split_spaced(chunk):
    """Names the printer ran together without commas (1859: "Anderson Armstrong Davis of B Dennis
    Harrison of V Z Hubbard") -> one name each; a county after "of" keeps its initials or second word."""
    toks = chunk.split()
    if len(toks) < 4:
        return [chunk]
    names, i = [], 0
    while i < len(toks):
        name = []
        while i < len(toks) and re.fullmatch(r"[A-Z]\.?", toks[i]) and i + 1 < len(toks):
            name.append(toks[i]); i += 1                     # a member's own initials: "J. W. Smith"
        name.append(toks[i]); i += 1
        if i < len(toks) and re.fullmatch(r"(?:of|or|ot|nf)\.?", toks[i]) and i + 1 < len(toks):
            name += toks[i:i + 2]; i += 2                    # "of" + county
            if name[-1].lower().strip(".") in TWO_WORD_COUNTY and i < len(toks):
                name.append(toks[i]); i += 1
            while i < len(toks) and re.fullmatch(r"[A-Z]\.?", toks[i]):
                name.append(toks[i]); i += 1                 # "of V Z": an abbreviated county
        names.append(" ".join(name))
    return names


def running_lists(lines):
    """Running-text roll calls: "Yeas—Messrs. A, B, and C—16. Nays—Messrs. ...—4." """
    text_lines = [ln["text"] for ln in lines]
    pages = [ln["page"] for ln in lines]
    spots = [at(ln) for ln in lines]
    joined, line_of = "", []
    for k, t in enumerate(text_lines):
        joined += (t + "\n")
        line_of += [k] * (len(t) + 1)
    # rejoin hyphenated line breaks; every character keeps its line, so a list's heading is placed on
    # its own line (offsets taken before the rejoin drifted a line or more by the end of a day, by a
    # different amount in each reading, and two readings of one roll call no longer met)
    keep = [True] * len(joined)
    for m in re.finditer(r"(?<=\w)-\n(?=\w)", joined):
        keep[m.start()] = keep[m.start() + 1] = False
    line_of = [k for k, kept in zip(line_of, keep) if kept]
    joined = "".join(c for c, kept in zip(joined, keep) if kept).replace("\n", " ")
    found = [(m, label_code(re.match("(" + "|".join(f"(?P<{c}>{p})" for c, p in LABELS) + ")", m.group("label"), re.I)))
             for m in RUN_RE.finditer(joined)]
    # a yea list followed by one whose heading no label pattern reads: a nay list when the heading is
    # more like "nays" than anything else (1858: "NA-XS-Messrs. Burroughs, ... -17." left the vote unanimous)
    n_found = len(found)
    for i, (m, code) in enumerate(found[:n_found]):
        if code == "Y":
            stop = found[i + 1][0].start() if i + 1 < n_found else len(joined)
            g = GARBLED_NAY_RE.match(joined, m.end(), stop)
            if g and nay_like(g.group("label")):
                found.append((g, "N"))
            elif not (i + 1 < n_found and found[i + 1][1] == "N"):
                # a nay list whose total the scan lost, or whose heading and title it garbled
                h = NAY_NO_TOTAL_RE.match(joined, m.end(), stop)
                if not h:
                    h = NAY_TITLED_RE.match(joined, m.end(), stop)
                    if h and not (re.search(r"[A-Za-z]", h.group("label")) and not other_heading(h.group("label"))
                                  and (h.group("title") or nay_like(h.group("label")))):
                        h = None
                if not h:
                    h = NAY_LOOSE_RE.match(joined, m.end(), stop)
                    if h and not nays_word(h.group("label")):
                        h = None
                if h:
                    found.append((h, "N"))
    found.sort(key=lambda x: x[0].start())
    votes = []
    for m, code in found:
        names = re.sub(r"\band\s+", ", ", m.group("names"))
        names = [n.strip(" .;:") for n in re.split(r"[,;]\s*", names) if n.strip(" .;:")]
        names = [x for n in names for x in split_spaced(n)]
        # "Wilson, of L." with a stray comma: the county belongs to the name before it
        merged = []
        for n in names:
            if re.match(r"^(?:of|or|ot|nf)\b", n) and merged:
                merged[-1] += " " + n
            else:
                merged.append(n)
        names = merged
        line_no = line_of[m.start()]
        if code == "Y" and votes and not votes[-1].get("closed") and "Y" in votes[-1]["lists"] \
                and "N" not in votes[-1]["lists"] and m.start() - votes[-1]["end"] < 8:
            code = "N"                  # a nay label misread as yeas, right after the yea list with no prose between
        if code == "Y" or not votes or votes[-1].get("closed"):
            ctx = context_before(lines, line_no)
            votes.append({"printed": {}, "lists": {}, "page": pages[line_no], "at": spots[line_no], "labels": [], "context": ctx,
                          "style": "running", "end": m.end()})
        rv = votes[-1]
        if code in rv["lists"] or m.start() - rv["end"] > 400:
            rv["closed"] = True
            votes.append({"printed": {}, "lists": {}, "page": pages[line_no], "at": spots[line_no], "labels": [], "context": context_before(lines, line_no),
                          "style": "running", "end": m.end()})
            rv = votes[-1]
        rv["lists"][code] = names
        rv["labels"].append((code, pages[line_no], *spots[line_no]))
        rv["printed"][code] = count(m.group("n"))
        rv["end"] = m.end()
    for rv in votes:
        rv.pop("end", None); rv.pop("closed", None)
    return [rv for rv in votes if "Y" in rv["lists"]]


def parse_day(day, path, ocr=None, pages=None):
    lines = [ln for ln in scan_layout.document_lines(path, ocr=ocr, pages=pages) if not RUNNING_HEAD.match(ln["text"].strip())]
    # a list doesn't run on across a page left unread: each run of pages read together is parsed apart (1934 Senate:
    # with page 10 not re-read, a 14-14 vote at the foot of page 9 took in the 13-14 one heading page 11)
    runs = []
    for ln in lines:
        if not runs or pages is not None and any(q not in pages for q in range(runs[-1][-1]["page"] + 1, ln["page"])):
            runs.append([])
        runs[-1].append(ln)
    # a journal uses one style per era: column lists if the day has any, else running text (a lone "Yeas—20." line
    # over no names isn't one: 1873 Senate, where it hid the day's four running-text votes)
    cols = [column_lists(r) for r in runs]
    per_run = cols if any(rv["lists"]["Y"] for rvs in cols for rv in rvs) else [running_lists(r) for r in runs]
    # the last roll call of a run with the next page unread may run on there: "cut", it's read again with that page
    # rather than taken for a piece of a roll call beside it (1957 House: a 138-4 vote's first 38 yeas, page 42 unread)
    for r, rvs in zip(runs, per_run):
        if rvs and pages is not None and r[-1]["page"] + 1 not in pages:
            rvs[-1]["cut"] = True
    votes = [rv for rvs in per_run for rv in rvs]
    out = []
    for seq, rv in enumerate(sorted(votes, key=lambda v: v["page"]), 1):
        ctx = rv.pop("context")
        text = ctx[-1] if ctx else ""
        motion = next((s for s in reversed(ctx) if re.search(r"\bmoved?\b", s)), "")
        bill = bill_ref(text) or bill_ref(motion) or bill_ref(" ".join(ctx)) or ""
        out.append({"chamber": day["chamber"], "leg": day["leg"], "session": day["session"], "date": day["date"],
                    "file": day["file"], "record": None, "seq": seq, "bill": bill, "caption": None,
                    "heading": "", "text": text[:500], "motion": motion[:400], "amendment": "",
                    "printed": {"Y": rv["printed"].get("Y"), "N": rv["printed"].get("N"), "P": rv["printed"].get("P") or 0},
                    "printed_raw": rv.get("printed_raw", {}),
                    "lists": rv["lists"], "style": rv["style"], "page_in_day": rv["page"], "at": rv["at"],
                    "labels": rv["labels"], **({"cut": True} if rv.get("cut") else {})})
    return out


def legs_arg(arg):
    if "-" in arg:
        a, b = arg.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in arg.split(",")]


def main():
    legs = set(legs_arg(sys.argv[1])) if len(sys.argv) > 1 else set(range(1, 74))
    index = json.load(open(os.path.join(RAW, "index.json")))
    os.makedirs(OUT, exist_ok=True)
    out = open(os.path.join(OUT, "scan_rollcalls_raw.jsonl"), "a" if os.environ.get("APPEND") else "w")
    n = 0
    for day in sorted(index, key=lambda d: (d["leg"], d["chamber"], d["session"], d["date"], int(d.get("pages") or 0))):
        if day["leg"] not in legs or day["leg"] >= 74:
            continue
        path = os.path.join(RAW, day["file"])
        if not os.path.exists(path):
            continue
        for rv in parse_day(day, path):
            out.write(json.dumps(rv) + "\n")
            n += 1
    print("record votes:", n)


if __name__ == "__main__":
    main()

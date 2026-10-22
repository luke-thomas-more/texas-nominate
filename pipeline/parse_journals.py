"""Extract every record vote from the downloaded journals (modern era).

Each journal day becomes a list of paragraphs (from HTML, or from the PDF text layer where no
HTML exists), then a single pass walks the paragraphs tracking the bill under consideration, the
latest amendment and motion, and picks out each record vote with its printed totals and the
names in each category. Names stay raw here; pipeline/match_votes.py resolves them to members.

Writes data/interim/rollcalls_raw.jsonl and data/interim/statements_raw.jsonl.
"""
import html, json, os, re, subprocess, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "journals")
OUT = os.path.join(ROOT, "data", "interim")

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
        if head in ("Mr.", "Mrs.", "Ms.") or re.fullmatch(r"(?:.*,\s*)?[A-Z]\.", head) and not re.search(r",\s*(?:[A-Z]\.\s?)+$", head):
            continue
        if WHOLE_NAME.fullmatch(head):
            return head
    return elem


def split_names(chamber, s):
    s = s.strip()
    if not s or s.lower() in ("none", "none."):
        return []
    # Senate OCR (1995-97) sometimes reads a comma as a period: "West. Whitmire"
    parts = s.split(";") if chamber == "H" else re.split(r",|(?<=[a-z]{2})\.\s+(?=[A-Z])", s)
    if parts:
        parts[-1] = trim_last(parts[-1])
    return [p.strip().rstrip(".").strip() for p in parts if p.strip().rstrip(".").strip() and p.strip().lower() not in ("none", "none.")]


def senate_lists(text):
    """'Yeas: A, B. Nays: C.' -> {'Y': [...], 'N': [...]}; labels may share one paragraph."""
    found = {}
    marks = list(SENATE_LIST_RE.finditer(text))
    for i, m in enumerate(marks):
        label = re.sub(r"\s+", " ", m.group(1)).lower().replace(" - ", "-")
        code = next(c for l, c in SENATE_LISTS if l.lower() == label or l.lower().replace(" - ", "-") == label)
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
                rv = {"chamber": "H", "leg": day["leg"], "session": day["session"], "date": day["date"],
                      "file": day["file"], "record": int(m.group(1)),
                      "printed": {"Y": int(m.group(2)), "N": int(m.group(3)), "P": int(m.group(4) or 0)},
                      "text": p[:m.start()].strip(" ,:(").strip()[:500], **ctx, "lists": {}}
                rv["text"] = re.sub(r"\s*by\s*$", "", rv["text"])
                j, last, text = i + 1, None, {}
                while j < len(paras):
                    q = paras[j]
                    lm = HOUSE_LIST_RE.match(q)
                    if lm:
                        label = re.sub(r"\s+", " ", lm.group(1)).lower()
                        code = next(c for l, c in HOUSE_LISTS if l.lower() == label)
                        text[code] = (text.get(code, "") + "; " + lm.group(2)) if code in text else lm.group(2)
                        last = code
                        j += 1
                        continue
                    # a list broken by a page boundary continues in a paragraph of bare names,
                    # sometimes mid-name ("Smith," | "W.; Solomons; ...")
                    if last and re.match(r"^[A-Z][A-Za-zÀ-ÿ'\-. ]*(?:,\s*(?:[A-Z]\.\s*){1,3})?(?:\(C\))?[;.]", q) and not HOUSE_VOTE_RE.search(q):
                        text[last] = text[last] + " " + q
                        j += 1
                        continue
                    break
                rv["lists"] = {code: split_names("H", t) for code, t in text.items()}
                votes.append(rv)
                i = j
                continue
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


def main():
    index = json.load(open(os.path.join(RAW, "index.json")))
    os.makedirs(OUT, exist_ok=True)
    rc_out = open(os.path.join(OUT, "rollcalls_raw.jsonl"), "w")
    st_out = open(os.path.join(OUT, "statements_raw.jsonl"), "w")
    n, captions, session = 0, {}, None
    all_captions = {}
    for day in sorted(index, key=lambda d: (d["chamber"], d["leg"], d["session"], d["date"], d["file"])):
        path = os.path.join(RAW, day["file"])
        if not os.path.exists(path):
            continue
        if (day["chamber"], day["leg"], day["session"]) != session:
            session, captions = (day["chamber"], day["leg"], day["session"]), {}
            all_captions["|".join(map(str, session))] = captions
        if day["kind"] == "html":
            paras = paragraphs_html(open(path, encoding="utf-8", errors="replace").read())
        else:
            paras = paragraphs_pdf(path)
        votes, statements = parse_day(day, paras, captions)
        for v in votes:
            rc_out.write(json.dumps(v) + "\n")
        for s in statements:
            st_out.write(json.dumps(s) + "\n")
        n += len(votes)
    json.dump(all_captions, open(os.path.join(OUT, "captions.json"), "w"))
    print("record votes:", n)


if __name__ == "__main__":
    main()

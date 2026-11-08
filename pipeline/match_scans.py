"""Match names from the scanned journals to members, tolerating OCR damage, and check totals.

For each roll call:
  1. Names that OCR glued together ("Johnson of Dallas Stewart") are split where both halves
     look like members.
  2. Every raw name is scored against every member of that session: surname similarity (after
     folding common OCR confusions like 1/l, 0/o, rn/m), plus the county in "Allen of Colorado"
     and initials in "J. F. McKee" where given.
  3. Names are assigned to members one-to-one (a member appears once per roll call), maximizing
     total similarity; weak pairs are left unmatched.
  4. The roll call is "ok" only if the matched yeas and nays equal the printed totals.

Writes data/processed/rollcalls_hist.csv, votes_hist.csv.gz and match_hist_report.txt.
"""
import collections, csv, gzip, json, os, re, sys
import numpy as np
from rapidfuzz import fuzz, process
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from match_votes import norm, load_roster, serving, initial_seqs, initial_score
from parse_scans import normalize_ocr, BROKEN

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INTERIM = os.path.join(ROOT, "data", "interim")
OUT = os.path.join(ROOT, "data", "processed")
MATCH = 68          # confident name -> member pairing
MATCH2 = 56         # second pass, among members not already placed in the roll call
TITLES = r"(?:Mrs|Miss|Mr|Messrs|Mitis|Mlas|Mi55|Mis|Mn|Mra|Mre|Mr8)"       # and OCR's "Mn." (Still, Mrs., 1949)
# words that begin a surname, so a name never splits right after one ("De La Garza", "Van de Putte")
PARTICLES = {"de", "la", "le", "du", "van", "von", "der", "den", "del", "di", "da", "mc", "mac", "st", "san", "ten", "ter", "des", "el"}


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
# words of the journal's own text, not of members' names: a heading, date line or sentence caught in a list
PROSE = set("""the by and was were then his on to at as be is it in for with that this said messrs mr mrs hon
    senator senators speaker president chaplain journal prayer page house senate chamber yeas nays yea nay present
    absent excused voting not pairs paired recorded message resolution question amendment motion bill reading
    third second final passage adjournment recess committee report read lost carried table vote votes following
    members member called roll call ordered engrossed concurrent leave withdrew substitute offered moved
    adopted adoption adopt stood section sections enacted concur concurred provided further additional insert
    inserting strike striking line lines words amend amended consent unanimous asked rule rules suspend
    suspended sustained overruled appeal laid reconsider reconsidered explanation printed upon being having
    made shall relating providing creating texas""".split())


# a list heading run into the name after it ("Alford. Absent-Bowers", 1871): that name is in another list
HEADING = re.compile(r"^\W*(?:absent|present|excused|paired|pairs|nays?|yeas?|ayes?|noes?|not\s+voting)\b", re.I)


class CommaPiece(str):
    """What follows a comma in "Townsend, Angelina" (his county) or "Allen, John" (1971, beside
    "Allen, Joe": his given name); scored as whichever fits the member."""


class Leftover(str):
    """A name match() left unplaced, with the list it was printed in (.code)."""
    def __new__(cls, raw, code):
        s = super().__new__(cls, raw)
        s.code = code
        return s

    def __getnewargs__(self):
        return str(self), self.code


def fold(s):
    """Fold common OCR confusions so 'A1leu' and 'allen' compare as near-equal."""
    s = s.lower()
    for a, b in (("1", "l"), ("0", "o"), ("5", "s"), ("8", "b"), ("6", "b"), ("rn", "m"), ("in", "m"), ("ni", "m"),
                 ("vv", "w"), ("ii", "u")):                    # "Cam" for Cain, "Dame1" for Daniel
        s = s.replace(a, b)
    return re.sub(r"[^a-z ]", "", s)


def speakers():
    """[(member_id, start, end)] from the Legislative Reference Library's list of Speakers."""
    from datetime import datetime
    path = os.path.join(ROOT, "data", "raw", "roster", "speakers.html")
    if not os.path.exists(path):
        return []
    out = []
    page = open(path, encoding="utf-8", errors="replace").read()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        mid = re.search(r"memberID=(\d+)", row)
        if not mid:
            continue
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row))
        for a, b in re.findall(r"(\w{3}\.? \d{1,2}, \d{4}) - (\w{3}\.? \d{1,2}, \d{4})", text):
            iso = [datetime.strptime(x.replace(".", ""), "%b %d, %Y").date().isoformat() for x in (a, b)]
            out.append((int(mid.group(1)), iso[0], iso[1]))
    return out


def presidents_pro_tem():
    """[(member_id, elected, session_end)] from the Library's list of Senate Presidents pro tempore.
    When a senator presides, the journal lists him as "Mr. President" and he votes."""
    from datetime import datetime
    path = os.path.join(ROOT, "data", "raw", "roster", "presprotem.html")
    if not os.path.exists(path):
        return []
    out = []
    page = open(path, encoding="utf-8", errors="replace").read()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        mid = re.search(r"memberID=(\d+)", row)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row))
        span = re.search(r"\((\w{3,9}\.? \d{1,2}, \d{4}) - (\w{3,9}\.? \d{1,2}, \d{4})\)", text)
        elected = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
        if not (mid and span):
            continue
        dates = []
        for part in span.groups():
            for fmt in ("%b %d, %Y", "%B %d, %Y"):
                try:
                    dates.append(datetime.strptime(part.replace(".", ""), fmt).date().isoformat())
                    break
                except ValueError:
                    pass
        if len(dates) != 2:
            continue
        if elected:
            m, d, y = elected.groups()
            start = f"{y}-{int(m):02d}-{int(d):02d}"
        else:
            start = dates[0]          # "This session opened with Senator X already presiding"
        out.append((int(mid.group(1)), start, dates[1]))
    return out


def chair_on(chairs, date):
    """The presidents pro tem elected on or before date, within their session, most recent first.
    The most recent usually presides, but not always: 1870 lists "Mr. President" and Parsons, the
    pro tem elected that May, in one roll call, so Campbell, elected in April, was in the chair."""
    live = sorted((c for c in chairs if c[1] <= date <= c[2]), key=lambda c: c[1], reverse=True)
    return list(dict.fromkeys(c[0] for c in live))


def parse_raw(raw):
    """OCR'd journal name -> (surname, initials, county, special)."""
    s = normalize_ocr(raw)
    s = re.sub(r"\s*\(\s*[CG]\s*\)?\W*$", "", s)                     # "Hall(C)": presiding, and voting
    s = re.sub(r"(?<![A-Za-z])[1lI|]-[IiljJf](?=[a-z]{2})", "H", s)     # an H read as "1-I": "Jones of 1-Iarris"
    s = re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", s)                    # "Townsen, Lampasas-.", "Moore, Fort Bend:,.."
    s = re.sub(r"^M\S{0,5}rs\b\W*(?=[A-Z])", "", s)                # "Messrs.", "Méssrs.", "Messrs~" before a name (not "Mears.")
    s = re.sub(r"(\w)\s+sof\s+", r"\1s of ", s)                       # "Rhode sof Van Zandt" -> "Rhodes of ..."
    s = re.sub(r"\bFt\.?\s*(?=[A-Z][a-z])", "Fort ", s)                # "Parker, Ft. Bend" (1889)
    if len(s) < 25 and re.search(r"\bS[pr]e[ae]k|\bS\W?p\W?[ec]\W?a\W?k\W?[ec]\W?r", s, re.I) \
            or re.search(r",\s*S[pr]e[ae]k[ec]r$", s):                       # "Spcaker"; "Hon. J. S. Sherrill, Speaker"
        return "", None, None, "SPEAKER"
    last = re.sub(r"[^a-z]", "", s.lower().split(".")[-1])
    if len(s) < 25 and re.search(r"pr[ec]s|pres", last) and fuzz.ratio(last, "president") >= 75:   # not "Pridgen"
        return "", None, None, "PRESIDENT"
    s = re.sub(r"^\W*M?[eoa]ss?r?s\W+", "", s)                      # "Messrs." (or "fessrs.") before a name
    s = re.sub(r"^(?:Hon|Mrs?|Miss)\.?\s+(?=[A-Z])", "", s)            # "Hon. J. S. Sherrill", not initial M
    s = re.sub(r",?\s*\b" + TITLES + r"\.?$", "", s).strip(" ,.")
    county = None
    m = re.match(r"^(.*?\S)\s*,?\s*\b(?:[o0][flrt]|nf)\.?\s+\W*([A-Za-z].*)$", s) or re.match(r"^(.*?[a-z])\s*of(?=[A-Z])(.+)$", s) \
        or re.match(r"^(.*?[a-z]{3})of\s+([A-Z].+)$", s)                  # "Rhodesof VanZandt"
    if m:
        # OCR noise inside the county is dropped, not made a word break: "H3.rris" is Harris, not "h rris"
        s, county = m.group(1), fold(norm(re.sub(r"(?<=[A-Za-z])[.,:;2-47-9]+(?=[a-z])", "", m.group(2))))
    else:
        # "Townsend, Angelina", "Blount, S. Augustine", "Blount, Van Zandt" (1885), "Baker, DeWitt" (1892)
        m = re.match(r"^([A-Za-z][^\s,.]+),\s*((?:[A-Z]\.\s?|[A-Z][a-z]+\s)?(?:Mc|De|La)?[A-Z][a-z]{2,}(?:\s[A-Z][a-z]+)?)$", s)
        if m:
            s, county = m.group(1), CommaPiece(fold(norm(m.group(2))))
    initials = None
    m = re.match(r"^((?:[A-Z][a-z]?\.\s?){0,2}[A-Z][a-z]?\.)\s+(\S.*)$", s)     # "J. F. McKee", "Jo. Abbott"
    if m:
        initials = "".join(w[0].lower() for w in re.findall(r"[A-Z][a-z]?\.", m.group(1)))
        s = m.group(2)
    m = re.match(r"^(.*?),\s*((?:[A-Z]\.?\s*){1,3})$", s)                   # "Davis, Y."
    if m:
        s, initials = m.group(1), re.sub(r"[^a-z]", "", m.group(2).lower())
    m = re.match(r"^([A-Za-z][^\s,.]+),\s*(?!(?:Jr|Sr)\b)([A-Z][a-z]+\.?(?:\s*[A-Z]\.?)*)$", s)    # "Davis, John E. of Dallas" (1921)
    if m and not initials:
        s, initials = m.group(1), "".join(c.lower() for c in re.findall(r"[A-Z]", m.group(2)))
    return fold(norm(s)), initials, county, None


class SessionMatcher:
    def __init__(self, roster, speaker_ids=()):
        self.members = sorted({t["member_id"] for t in roster})
        self.terms = {t["member_id"]: t for t in roster}
        self.idx = {m: i for i, m in enumerate(self.members)}
        keys, owner, weak = [], [], []
        for t in roster:
            for k in t["surn"] | {x for x in t["weak"] if len(x) >= 4}:
                keys.append(fold(k)); owner.append(self.idx[t["member_id"]]); weak.append(k not in t["surn"])
        self.keys, self.owner, self.weak = keys, np.array(owner), np.array(weak, dtype=bool)
        self.speaker = [self.idx[m] for m in speaker_ids if m in self.idx]
        self.cache = {}
        self.active = np.zeros(len(self.members))
        self.offday, self.offday_cache = np.zeros(len(self.members)), {}
        # places each member represented, grouped by word count, for vectorized county scoring
        by_len = collections.defaultdict(lambda: ([], []))
        for m in self.members:
            for p in self.terms[m]["places"]:
                f = fold(p)
                if f:
                    by_len[len(f.split())][0].append(self.idx[m]); by_len[len(f.split())][1].append(f)
        self.places_by_len = {n: (np.array(o), names) for n, (o, names) in by_len.items()}
        self.has_places = np.array([bool(self.terms[m]["places"]) for m in self.members])
        self.county_cache = {}
        self.junk_cache = {}
        # each member's given names and nicknames, for "Allen, John" beside "Allen, Joe"
        gown, gnames = [], []
        for m in self.members:
            t = self.terms[m]
            for g in {w for toks in t["toks"] for w in toks[:-1] if len(w) >= 2} | set(t["nicks"]):
                if fold(g):
                    gown.append(self.idx[m]); gnames.append(fold(g))
        self.given = (np.array(gown, dtype=int), gnames)

    def given_bonus(self, piece):
        """Per member: up to +10 when a word after the comma is one of the member's given names or
        nicknames, down to -20 when it is nothing like any; cached per piece."""
        if ("given", piece) in self.county_cache:
            return self.county_cache[("given", piece)]
        owners, names = self.given
        bonus = np.full(len(self.members), -20.0)
        words = [w for w in piece.split() if len(w) >= 2]
        if words and names:
            sim = process.cdist(words, names, scorer=fuzz.ratio).max(axis=0)
            np.maximum.at(bonus, owners, np.clip((sim - 70) / 2, -20, 10))
        self.county_cache[("given", piece)] = bonus
        return bonus

    def county_bonus(self, county):
        """Per member: up to +10 when the journal's county matches one the member represented,
        down to -20 when none does, less 10 per extra word ("Dallas Stewart": a second name glued
        on). Vectorized over every (member, place) pair; cached per county string."""
        if county in self.county_cache:
            return self.county_cache[county]
        cw = county.split()
        # a printed county is binding: it's how the journal tells same-surname members apart (1879: "Smith of
        # Harris" went to Smith of Titus, 1887: "Newton of C." to Newton of Tarrant when it was only a hint)
        bonus = np.where(self.has_places, -40.0, 0.0)
        if len(county.replace(" ", "")) <= 3:
            # an abbreviated county ("Williams of L."): a prefix of a county the member represented
            pre = county.replace(" ", "")
            for n, (owners, names) in self.places_by_len.items():
                hit = np.array([nm.replace(" ", "").startswith(pre) for nm in names])
                np.maximum.at(bonus, owners, np.where(hit, 6.0, -30.0))
            self.county_cache[county] = bonus
            return bonus
        for n, (owners, names) in self.places_by_len.items():
            head = " ".join(cw[:n])
            sim = process.cdist([head], names, scorer=fuzz.ratio)[0]
            val = np.clip((sim - 70) / 2, -40, 10) - 10 * max(len(cw) - n, 0)
            np.maximum.at(bonus, owners, val)
        self.county_cache[county] = bonus
        return bonus

    def is_county(self, piece):
        """Is this (a piece of a name, after a comma) a place some member of the session represented?"""
        f = re.sub(r"^ft\b", "fort", fold(norm(piece.strip(" .,:"))))        # "Ft. Bend"
        if not f:
            return False
        names = self.places_by_len.get(len(f.split()), (None, []))[1]
        return bool(names) and process.extractOne(f, names, scorer=fuzz.ratio)[1] >= 88

    def set_speaker(self, member_ids):
        self.speaker = [self.idx[m] for m in member_ids if m in self.idx]

    def set_date(self, date):
        """Members out of office that day by the roster's dates score 10 lower, so a tie between two
        members of one name goes to the one serving (1849-50: "Taylor" is Marion DeKalb Taylor; Joseph
        Taylor took his seat in November 1850). A name only an out-of-office member fits still goes to
        him: the roster's dates are sometimes a day or two off."""
        if date not in self.offday_cache:
            self.offday_cache[date] = np.array([0.0 if serving(self.terms[m], date) else -10.0 for m in self.members])
        self.offday = self.offday_cache[date]

    def learn_activity(self, results):
        """results: [{member_id: code}] from a first pass over the session."""
        seen = collections.Counter(m for r in results for m in r)
        n = max(len(results), 1)
        self.active = np.array([4.0 * min(1.0, 3 * seen[m] / n) for m in self.members])

    def scores(self, raw):
        """Similarity of one raw name to every member (0-100, higher is better), plus a small
        nudge toward members who actually turn up in this session's roll calls (set after a
        first pass), which settles ties with a same-named member who wasn't serving then."""
        if raw in self.cache:
            return self.cache[raw] + self.active + self.offday
        sn, ini, county, special = parse_raw(raw)
        S = np.zeros(len(self.members))
        if special in ("SPEAKER", "PRESIDENT"):     # whoever held the chair that day: not cached
            for k, i in enumerate(self.speaker):    # the likeliest first; another when he's listed by name
                S[i] = 100.0 - k
            return S + self.active + self.offday
        elif sn and self.keys:
            r1 = process.cdist([sn], self.keys, scorer=fuzz.ratio)[0]
            r2 = process.cdist([sn.replace(" ", "")], [k.replace(" ", "") for k in self.keys], scorer=fuzz.ratio)[0]
            r = np.maximum(r1, r2)
            # a middle name ("Neave" for Victoria Neave Criado) counts only as written, and below a surname,
            # even one out of office that day: "Robertson" isn't Silas Robert Crawford, nor "Butler" George
            # Butler Terrell beside a Butler
            r = np.where(self.weak, np.where(r >= 90, r - 12, r - 25), r)
            np.maximum.at(S, self.owner, r)
            if county:
                b = self.county_bonus(county)
                S += np.maximum(b, self.given_bonus(county)) if isinstance(county, CommaPiece) else b
            elif not ini and S.max() < 95 and self.is_county(raw):
                S -= 25     # a county alone, its member's name a line up ("Williamson.", 1903), isn't "Williams"
            if ini:
                for i, m in enumerate(self.members):
                    t = self.terms[m]
                    sc = initial_score(ini, initial_seqs(t["toks"], t["nicks"], norm(sn) or sn))
                    S[i] += 6 * sc if sc > 0 else (-25 if sc < 0 else 0)
        self.cache[raw] = S
        return S + self.active + self.offday

    def split(self, raw):
        """'Johnson of Dallas Stewart' -> ['Johnson of Dallas', 'Stewart'] when that fits better.
        Names run together across a tight column ("Blount, S.AugustineHendry") are opened up first."""
        raw = re.sub(r",\s*[.'\"‘’“”`]+(?=[A-Z])", ", ", raw)                # "Allen, .Joe" (1972)
        raw = re.sub(r",(?=[A-Za-z])", ", ", raw)
        raw = re.sub(r"(?<=[a-z]{2})\.(?=[A-Z][a-z])", ". ", raw)          # "Buchanan of Scurry.Johnson of Hall."
        raw = re.sub(r"(?<!\bMc)(?<!\bMac)(?<!\bDe)(?<!\bLa)(?<!\bLe)(?<!\bDu)(?<!\bVan)(?<=[a-z]{2})(?=[A-Z][a-z])", " ", raw)
        whole = self.best(raw).max(initial=0)
        parts = re.split(r"(?<=[a-z])\.\s+(?=[A-Z])", raw)
        if len(parts) > 1:
            # each a fair match ("Rayner. Renfror Robertson", 1848, three names); or the first a member's
            # name the rest doesn't help, the rest a name of its own garbled past matching ("Edwards.
            # Ket>uan"): apart, so that name isn't lost inside a good one
            sc = [self.best(p).max(initial=0) for p in parts]
            apart = not any(re.search(r"\b(?:of|nf|[o0][flrt])$", p, re.I) for p in parts[:-1])     # "Buchanan or. Bell"
            if apart and (min(sc) >= 80 or sc[0] >= 85 and whole <= sc[0] and all(
                    len(re.sub(r"[^A-Za-z]", "", p)) >= 3 and not re.match(HEADING, p) for p in parts[1:])):
                return [x for p in parts for x in self.split(p)]
        # names a comma apart in one cell, read straight across tight sub-columns ("Martin of Wise,
        # Temptet.a", "Hood of Fannin, Marchison"): each piece a fair match, and better apart than as one
        # name ("Townsend, Angelina" is a member and his county; "Davis, Y." his initial)
        pieces = [p for p in re.split(r",\s*(?=[^\w\s]?[A-Z][a-z])", raw) if p.strip()]
        parts = pieces[:1]
        for p in pieces[1:]:
            # "Baker, Tom Green": a member and his county, when the county makes the better match or
            # could be no member's name alone; a name run on after the county ("Baker, Tom Green
            # Martin of Wise") is the next entry
            words = p.split()
            k = next((k for k in range(min(3, len(words)), 0, -1) if self.is_county(" ".join(words[:k]))), 0)
            county = " ".join(words[:k])
            if k and (self.best(parts[-1] + ", " + county).max(initial=0) >= self.best(parts[-1]).max(initial=0) + 3
                      or self.best(county).max(initial=0) < MATCH):
                parts[-1] += ", " + county
                parts += [" ".join(words[k:])] if words[k:] else []
            else:
                parts.append(p)
        if len(parts) > 1:
            sc = [self.best(p).max(initial=0) for p in parts]
            if min(sc) >= 70 and min(sc) >= whole - 8:
                return [x for p in parts for x in self.split(p)]
        words = raw.split()
        best, best_split = None, [raw]
        top = int(self.best(raw).argmax()) if self.members else 0      # the member the whole reads as
        for k in range(1, len(words)):
            a, b = " ".join(words[:k]), " ".join(words[k:])
            if self.is_county(b) and self.best(a + ", " + b).max(initial=0) >= max(whole, self.best(a).max(initial=0)) + 3:
                return [a + ", " + b]           # "Baker Tcm Green" (1892): a member and his county, the comma lost
            if re.match(r"^(of|nf|[o0][flrt])\b", b, re.I) or words[k - 1].lower().rstrip(".") in PARTICLES:
                continue        # "of" + county stays together; so does a surname's particle ("De La | Garza")
            sa, sb = self.best(a).max(initial=0), self.best(b).max(initial=0)
            # two names glued together: both halves match members about as well as the whole does, or
            # the whole only as given name + surname with no such given name ("Renfror Robertson",
            # 1848: Renfro and Robertson; but "Henry Moore" is Moore)
            given = len(words) == 2 and all(re.fullmatch(r"[A-Z][a-z]+\.?", w) for w in words)
            if sa >= 85 and sb >= 85 and (min(sa, sb) >= whole - 3 or given and self.given_bonus(fold(norm(a)))[top] < 0) \
                    and (best is None or min(sa, sb) > best):
                best, best_split = min(sa, sb), [a, b]
        return best_split if len(best_split) == 1 else [x for part in best_split for x in self.split(part)]   # three run together

    def rejoin(self, names):
        """A two-column list read straight across glues the left column's "Davidson of" to the right
        column's name and leaves the county alone on the next line ("Davidson of Paulus.", "DeWitt."):
        give the "of" the loose county just after it, and the name its own entry, when that county
        fits a member the name could be far better than either reading did."""
        out = [[n] for n in names]
        for i, n in enumerate(names):
            m = re.match(r"^(.+?\b(?:of|nf|o[flrt])\.?)(?:\s+(\S.*))?$", n, re.I)
            if not m or out[i] != [n] or (m.group(2) and self.best(m.group(2)).max(initial=0) < 85):
                continue
            head, tail = m.groups()
            base = self.best(n).max(initial=0)
            for j in range(i + 1, min(i + 4, len(names))):
                w = names[j]
                if out[j] != [w] or len(w.split()) > 2:
                    continue
                if self.best(head + " " + w).max(initial=0) >= max(base, self.best(w).max(initial=0)) + 5:
                    out[i], out[j] = [head + " " + w] + ([tail] if tail else []), []
                    break
        # the same a row further on: the left column's "Henderson of" glued to the county the right
        # column's "Terrell of" ran on to, with Henderson's own county loose on the next line ("Terrell
        # of", "Henderson of Cherokee.", "Lamar.", 1901): each "of" gets its county, when both read better
        names = [x for xs in out for x in xs]
        best = lambda s: self.best(s).max(initial=0)
        for i in range(len(names) - 2):
            h = re.match(r"^(.+?\b(?:of|nf|o[flrt]))\.?$", names[i], re.I)
            j = next((j for j in range(i + 1, min(i + 3, len(names)))
                      if re.match(r"^.+?\b(?:of|nf|o[flrt])\.?\s+\S", names[j], re.I)), None) if h else None
            g = j is not None and re.match(r"^(.+?\b(?:of|nf|o[flrt]))\.?\s+(\S.*)$", names[j], re.I)
            if not g or not self.is_county(g.group(2)):
                continue
            k = next((k for k in range(j + 1, min(j + 3, len(names)))
                      if self.is_county(names[k]) and best(names[k]) < MATCH), None)
            if k is not None:
                a, b = h.group(1) + " " + g.group(2), g.group(1) + " " + names[k]
                if best(a) + best(b) >= best(names[i]) + best(names[j]) + 10:
                    names[i], names[j], names[k] = a, b, ""
        return [n for n in names if n]

    def unbreak(self, raw):
        """"Barbee of Free‖stone" (parse_scans marks a name broken at a line's end and the fragment under
        it): one name when the whole reads as a member clearly better than its first part alone, else
        the two pieces apart."""
        if BROKEN not in raw:
            return [raw]
        head, tail = raw.split(BROKEN, 1)
        whole = head + tail.replace(BROKEN, "")
        if self.best(whole).max(initial=0) >= max(self.best(head).max(initial=0), 60) + 3:
            return [whole]
        return [head] + self.unbreak(tail)

    def match(self, lists):
        """lists: {code: [raw names]} -> ({member_id: code}, [raw names left unmatched that could be a
        member's: neither junk nor a repeat of a name placed from the same list], as Leftovers)"""
        entries = [(code, part) for code, names in lists.items()
                   for part in self.rejoin([p for raw in names for piece in self.unbreak(raw)
                                            for p in self.split(piece) if p.strip()])
                   if any(parse_raw(part)[::3])]          # not a title left alone ("Mr" | "Speaker.", 1926)
        if not entries or not self.members:
            return {}, [Leftover(r, code) for code, r in entries]
        S = np.vstack([self.best(r) for _, r in entries])
        # one name in two lists goes to the earlier: a list runs on into the next roll call's names when
        # that heading is missed, so the later list is the suspect (1889: the nays again among the absent)
        order = list(dict.fromkeys(["Y", "N"] + [code for code, _ in entries]))
        S = S + np.array([0.01 * (len(order) - order.index(code)) for code, _ in entries])[:, None]
        votes, used, taken = {}, set(), set()
        # tier 1: confident pairs; tier 2: what's left, among members not yet placed in this roll call.
        # An entry may go unplaced, at a cost just over the poorest pairing allowed: one more name placed
        # mustn't push others to their second choices (1901: "Speaker." took Prince from "Hon. R. E.
        # Prince", who took Wells from "Wells of Grayson", and so on through five members)
        for floor in (MATCH, MATCH2):
            todo = [r for r in range(len(entries)) if r not in used]
            free = [c for c in range(len(self.members)) if c not in taken]
            if not todo or not free:
                break
            sub = S[np.ix_(todo, free)]
            cost = np.hstack([np.where(sub >= floor, 100.0 - sub, 1e6), np.full((len(todo), len(todo)), 1e6)])
            cost[np.arange(len(todo)), len(free) + np.arange(len(todo))] = 101.0 - floor
            rows, cols = linear_sum_assignment(cost)
            for r, c in zip(rows, cols):
                if c < len(free) and cost[r, c] < 1e6:
                    votes[self.members[free[c]]] = entries[todo[r]][0]
                    used.add(todo[r]); taken.add(free[c])
        # left over but no member's name: what isn't a name at all (junk), or a name printed twice in one
        # list ("Davis of Harris" twice among the yeas, 1969) whose member that list placed already
        placed = {self.idx[m]: c for m, c in votes.items()}
        dup = lambda r: S[r].max() >= MATCH and placed.get(int(S[r].argmax())) == entries[r][0]
        return votes, [Leftover(entries[r][1], entries[r][0]) for r in range(len(entries))
                       if r not in used and not dup(r) and not self.junk(entries[r][1])]

    def junk(self, raw):
        """Not a name at all, and no member's: an OCR speck ("|", "‘I", "Mr" alone), a date, heading or
        sentence caught in a list ("July", "JOURNAL", "Present-Not Voting", "Senator Homan then withdrew"),
        or a county left over from a name broken across lines. Anything a member's name could be read as
        stays ("May", "Page", "Mays" for Mayes, 1889)."""
        if raw not in self.junk_cache:
            letters = re.sub(r"[^A-Za-z]", "", raw)
            words = re.findall(r"[A-Za-z]+", raw)
            low = [w.lower() for w in words if w.lower() not in PARTICLES]
            best = lambda: self.best(raw).max(initial=0)
            if parse_raw(raw)[3]:
                j = False                                   # "Mr. Speaker", whoever is in the chair that day
            elif len(letters) < 3:
                j = True
            elif (re.fullmatch(r"\W*(?:" + "|".join(MONTHS) + r")\b\W*\d*\W*", raw, re.I)
                  or letters.isupper() and len(letters) >= 4 or any(w in PROSE for w in low)):
                j = best() < 80
            elif (sum(1 for w in words if w.islower() and len(w) >= 3 and w not in PARTICLES and w != "of") >= 2
                  or any(len(w) >= 6 and process.extractOne(w, PROSE, scorer=fuzz.ratio)[1] >= 80 for w in low)):
                j = best() < 75                             # a sentence; "Abeent-hauaed" (Absent-Excused)
            elif self.is_county(re.sub(r"^\W*(?:[a-z]{1,2}\s+)?(?:of\s+)?", "", raw)):
                j = best() < MATCH                          # a county its member's name didn't take ("o Dallas.")
            else:
                j = False
            self.junk_cache[raw] = j
        return self.junk_cache[raw]

    def best(self, raw):
        """Score as written, or as given name(s) + surname ("Henry Moore" -> Moore, initial h), or with
        a capital U or H inside a word read as the "ll" OCR took it for ("StUl" Still, "CaHan" Callan)."""
        S = self.scores(raw)
        words = raw.split()
        if len(words) >= 2 and re.match(r"^[A-Z][a-z]{2,}$", words[0]) and not re.search(r"\b(?:of|or|ot)\b", raw):
            S = np.maximum(S, self.scores(words[0][0] + ". " + " ".join(words[1:])) - 2)
        ll = re.sub(r"(?<![MD]c)(?<!Mac)(?<![DL][ae])(?<!Du)(?<=[a-z])[UH](?=[a-z])", "ll", raw)
        if ll != raw:
            S = np.maximum(S, self.scores(ll) - 2)
        return S


CONFUSABLE = {"&": "68", "S": "58", "s": "5", "B": "8", "O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1",
              "i": "1", "Z": "2", "z": "2", "G": "6", "g": "9", "q": "9", "T": "7", "A": "4"}
# a letter heading a total, glued to it after the dash ("Yeas—f2." over 62 names, 1895 House): how often each
# stands for which digit, from the column lists whose totals fit their lists one way (a stray mark is tried too)
LEAD = dict(CONFUSABLE, i="15", o="05", O="05", J="1", L="1", t="1", a="35", d="5", h="5", b="56", f="6")


def readings(raw, value):
    """Every number an OCR'd count could be: "&" -> {6, 8}, "1O" -> {10}; a leading letter may
    also be a stray mark (the dash's tail): "z2" -> {22, 2}, "l53" -> {153, 53}."""
    if not raw or raw == "none":
        return {value} if value is not None else set()
    variants = {raw}
    if len(raw) > 1 and not raw[0].isdigit() and re.search(r"\d", raw[1:]):
        variants.add(raw[1:])
    out = {value} if value is not None else set()
    for v in variants:
        opts = [""]
        for k, ch in enumerate(v):
            alts = ch if ch.isdigit() else (LEAD if k == 0 else CONFUSABLE).get(ch, "")
            opts = [o + a for o in opts for a in alts] if alts else opts
        out |= {int(o) for o in opts if o}
    return out


def check_parts(rv, votes):
    """(yeas, nays, yea total checks, nay total checks) against every reading of the printed totals."""
    ny = sum(1 for v in votes.values() if v == "Y")
    nn = sum(1 for v in votes.values() if v == "N")
    raw = rv.get("printed_raw", {})
    alts = rv.get("alt_printed", [])                 # the other OCRs' readings of the same totals
    alts = [alts] if isinstance(alts, dict) else alts
    alt_y = {a["Y"] for a in alts if a.get("Y") is not None}
    alt_n = {a["N"] for a in alts if a.get("N") is not None}
    y_ok = ny in readings(raw.get("Y"), rv["printed"].get("Y")) | alt_y
    n_ok = nn in ((readings(raw.get("N"), rv["printed"].get("N")) | alt_n) or {0})
    return ny, nn, y_ok, n_ok


def check(rv, votes):
    ny, nn, y_ok, n_ok = check_parts(rv, votes)
    return ny, nn, y_ok and n_ok


def main():
    people, terms, aliases = load_roster()
    raw = [json.loads(l) for l in open(os.path.join(INTERIM, "scan_rollcalls_raw.jsonl"))]
    sessions = collections.defaultdict(list)
    for rv in raw:
        sessions[(rv["chamber"], rv["leg"], rv["session"])].append(rv)
    rc_rows, vote_rows, report = [], [], []
    status = collections.Counter()
    for (chamber, leg, sess), rvs in sorted(sessions.items(), key=lambda x: (x[0][1], x[0][0], x[0][2])):
        roster = terms.get((chamber, leg), [])
        speaker = [t["member_id"] for t in roster if re.match(r"^Speaker\b(?!\s+pro)", t["note"], re.I)]
        sm = SessionMatcher(roster, speaker)
        rvs.sort(key=lambda r: (r["date"], r["file"], r["seq"]))
        sm.learn_activity([sm.match(rv["lists"])[0] for rv in rvs])
        sm.cache = {}
        for n, rv in enumerate(rvs, 1):
            votes, unmatched = sm.match(rv["lists"])
            ny, nn, ok = check(rv, votes)
            py, pn = rv["printed"].get("Y"), rv["printed"].get("N") or 0
            st = "ok" if ok else "count_mismatch"
            status[(chamber, leg // 10 * 10, st)] += 1
            rc_id = f"{leg}{sess}-{chamber}S{n:04d}" if False else f"{leg}{sess}-{chamber}{n:04d}"
            if st != "ok":
                report.append(f"{rc_id} {rv['date']} printed Y{py} N{pn} got Y{ny} N{nn} unmatched: {'; '.join(unmatched[:6])}")
            rc_rows.append({"rc_id": rc_id, "chamber": chamber, "leg": leg, "session": sess, "date": rv["date"],
                            "record": "", "bill": rv.get("bill") or "", "caption": "", "heading": "",
                            "text": rv.get("text") or "", "motion": rv.get("motion") or "", "amendment": "",
                            "printed_y": py if py is not None else "", "printed_n": pn, "printed_p": rv["printed"].get("P") or 0,
                            "n_y": ny, "n_n": nn, "implied": "", "status": st, "file": rv["file"],
                            "page": rv.get("page_in_day", ""), "style": rv.get("style", "")})
            for mid, code in votes.items():
                vote_rows.append((rc_id, mid, code))
    if rc_rows:
        with open(os.path.join(OUT, "rollcalls_hist.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, list(rc_rows[0].keys()))
            w.writeheader(); w.writerows(rc_rows)
        with gzip.open(os.path.join(OUT, "votes_hist.csv.gz"), "wt", newline="") as f:
            w = csv.writer(f); w.writerow(["rc_id", "member_id", "vote"]); w.writerows(vote_rows)
    with open(os.path.join(OUT, "match_hist_report.txt"), "w") as f:
        f.write("\n".join(f"{k}: {v}" for k, v in sorted(status.items())) + "\n\n" + "\n".join(report) + "\n")
    print("\n".join(f"{k}: {v}" for k, v in sorted(status.items())))


if __name__ == "__main__":
    main()

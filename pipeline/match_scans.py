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
from parse_scans import normalize_ocr

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INTERIM = os.path.join(ROOT, "data", "interim")
OUT = os.path.join(ROOT, "data", "processed")
MATCH = 68          # confident name -> member pairing
MATCH2 = 56         # second pass, among members not already placed in the roll call
TITLES = r"(?:Mrs|Miss|Mr|Messrs|Mitis|Mlas|Mi55|Mis)"
# words that begin a surname, so a name never splits right after one ("De La Garza", "Van de Putte")
PARTICLES = {"de", "la", "le", "du", "van", "von", "der", "den", "del", "di", "da", "mc", "mac", "st", "san", "ten", "ter", "des", "el"}


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
    s = re.sub(r"(?<![A-Za-z])[1lI|]-[IiljJf](?=[a-z]{2})", "H", s)     # an H read as "1-I": "Jones of 1-Iarris"
    s = re.sub(r"^[^A-Za-z]+|[^A-Za-z.]+$", "", s).strip(" .")
    s = re.sub(r"^M\S{0,5}rs\b\W*(?=[A-Z])", "", s)                # "Messrs.", "Méssrs.", "Messrs~" before a name (not "Mears.")
    s = re.sub(r"(\w)\s+sof\s+", r"\1s of ", s)                       # "Rhode sof Van Zandt" -> "Rhodes of ..."
    if len(s) < 25 and re.search(r"\bS[pr]e[ae]k", s, re.I):
        return "", None, None, "SPEAKER"
    last = re.sub(r"[^a-z]", "", s.lower().split(".")[-1])
    if len(s) < 25 and re.search(r"pr[ec]s|pres", last) and fuzz.ratio(last, "president") >= 75:   # not "Pridgen"
        return "", None, None, "PRESIDENT"
    s = re.sub(r"^\W*M?[eoa]ss?r?s\W+", "", s)                      # "Messrs." (or "fessrs.") before a name
    s = re.sub(r",?\s*\b" + TITLES + r"\.?$", "", s).strip(" ,.")
    county = None
    m = re.match(r"^(.*?\S)\s*,?\s*\b(?:[o0][flrt]|nf)\.?\s+\W*([A-Za-z].*)$", s) or re.match(r"^(.*?[a-z])\s*of(?=[A-Z])(.+)$", s) \
        or re.match(r"^(.*?[a-z]{3})of\s+([A-Z].+)$", s)                  # "Rhodesof VanZandt"
    if m:
        # OCR noise inside the county is dropped, not made a word break: "H3.rris" is Harris, not "h rris"
        s, county = m.group(1), fold(norm(re.sub(r"(?<=[A-Za-z])[.,:;2-47-9]+(?=[a-z])", "", m.group(2))))
    else:
        # "Townsend, Angelina", "Blount, S. Augustine", "Blount, Van Zandt" (1885)
        m = re.match(r"^([A-Za-z][\w'’\\-]+),\s*((?:[A-Z]\.\s?|[A-Z][a-z]+\s)?[A-Z][a-z]{2,}(?:\s[A-Z][a-z]+)?)$", s)
        if m:
            s, county = m.group(1), fold(norm(m.group(2)))
    initials = None
    m = re.match(r"^((?:[A-Z][a-z]?\.\s?){0,2}[A-Z][a-z]?\.)\s+(\S.*)$", s)     # "J. F. McKee", "Jo. Abbott"
    if m:
        initials = "".join(w[0].lower() for w in re.findall(r"[A-Z][a-z]?\.", m.group(1)))
        s = m.group(2)
    m = re.match(r"^(.*?),\s*((?:[A-Z]\.?\s*){1,3})$", s)                   # "Davis, Y."
    if m:
        s, initials = m.group(1), re.sub(r"[^a-z]", "", m.group(2).lower())
    return fold(norm(s)), initials, county, None


class SessionMatcher:
    def __init__(self, roster, speaker_ids=()):
        self.members = sorted({t["member_id"] for t in roster})
        self.terms = {t["member_id"]: t for t in roster}
        self.idx = {m: i for i, m in enumerate(self.members)}
        keys, owner = [], []
        for t in roster:
            for k in t["surn"] | {x for x in t["weak"] if len(x) >= 4}:
                keys.append(fold(k)); owner.append(self.idx[t["member_id"]])
        self.keys, self.owner = keys, np.array(owner)
        self.speaker = [self.idx[m] for m in speaker_ids if m in self.idx]
        self.cache = {}
        self.active = np.zeros(len(self.members))
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

    def county_bonus(self, county):
        """Per member: up to +10 when the journal's county matches one the member represented,
        down to -20 when none does, less 10 per extra word ("Dallas Stewart": a second name glued
        on). Vectorized over every (member, place) pair; cached per county string."""
        if county in self.county_cache:
            return self.county_cache[county]
        cw = county.split()
        bonus = np.where(self.has_places, -20.0, 0.0)
        if len(county.replace(" ", "")) <= 3:
            # an abbreviated county ("Williams of L."): a prefix of a county the member represented
            pre = county.replace(" ", "")
            for n, (owners, names) in self.places_by_len.items():
                hit = np.array([nm.replace(" ", "").startswith(pre) for nm in names])
                np.maximum.at(bonus, owners, np.where(hit, 6.0, -8.0))
            self.county_cache[county] = bonus
            return bonus
        for n, (owners, names) in self.places_by_len.items():
            head = " ".join(cw[:n])
            sim = process.cdist([head], names, scorer=fuzz.ratio)[0]
            val = np.clip((sim - 70) / 2, -20, 10) - 10 * max(len(cw) - n, 0)
            np.maximum.at(bonus, owners, val)
        self.county_cache[county] = bonus
        return bonus

    def set_speaker(self, member_ids):
        self.speaker = [self.idx[m] for m in member_ids if m in self.idx]

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
            return self.cache[raw] + self.active
        sn, ini, county, special = parse_raw(raw)
        S = np.zeros(len(self.members))
        if special in ("SPEAKER", "PRESIDENT"):     # whoever held the chair that day: not cached
            for k, i in enumerate(self.speaker):    # the likeliest first; another when he's listed by name
                S[i] = 100.0 - k
            return S + self.active
        elif sn and self.keys:
            r1 = process.cdist([sn], self.keys, scorer=fuzz.ratio)[0]
            r2 = process.cdist([sn.replace(" ", "")], [k.replace(" ", "") for k in self.keys], scorer=fuzz.ratio)[0]
            r = np.maximum(r1, r2)
            np.maximum.at(S, self.owner, r)
            if county:
                S += self.county_bonus(county)
            if ini:
                for i, m in enumerate(self.members):
                    t = self.terms[m]
                    sc = initial_score(ini, initial_seqs(t["toks"], t["nicks"], norm(sn) or sn))
                    S[i] += 6 * sc if sc > 0 else (-25 if sc < 0 else 0)
        self.cache[raw] = S
        return S + self.active

    def split(self, raw):
        """'Johnson of Dallas Stewart' -> ['Johnson of Dallas', 'Stewart'] when that fits better.
        Names run together across a tight column ("Blount, S.AugustineHendry") are opened up first."""
        raw = re.sub(r",(?=[A-Za-z])", ", ", raw)
        raw = re.sub(r"(?<=[a-z]{2})\.(?=[A-Z][a-z])", ". ", raw)          # "Buchanan of Scurry.Johnson of Hall."
        raw = re.sub(r"(?<!\bMc)(?<!\bMac)(?<!\bDe)(?<!\bLa)(?<!\bLe)(?<!\bDu)(?<!\bVan)(?<=[a-z]{2})(?=[A-Z][a-z])", " ", raw)
        whole = self.best(raw).max(initial=0)
        parts = re.split(r"(?<=[a-z])\.\s+(?=[A-Z])", raw)
        if len(parts) > 1 and all(self.best(p).max(initial=0) >= 80 for p in parts):
            return parts
        words = raw.split()
        best, best_split = None, [raw]
        for k in range(1, len(words)):
            a, b = " ".join(words[:k]), " ".join(words[k:])
            if re.match(r"^(of|nf|o[flrt])\b", b, re.I) or words[k - 1].lower().rstrip(".") in PARTICLES:
                continue        # "of" + county stays together; so does a surname's particle ("De La | Garza")
            sa, sb = self.best(a).max(initial=0), self.best(b).max(initial=0)
            # two names glued together: both halves match members about as well as the whole does
            if sa >= 85 and sb >= 85 and min(sa, sb) >= whole - 3 and (best is None or min(sa, sb) > best):
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
        return [x for xs in out for x in xs]

    def match(self, lists):
        """lists: {code: [raw names]} -> ({member_id: code}, [unmatched raw names])"""
        entries = [(code, part) for code, names in lists.items()
                   for part in self.rejoin([p for raw in names for p in self.split(raw) if p.strip()])]
        if not entries or not self.members:
            return {}, [r for _, r in entries]
        S = np.vstack([self.best(r) for _, r in entries])
        votes, used, taken = {}, set(), set()
        # tier 1: confident pairs; tier 2: what's left, among members not yet placed in this roll call
        for floor in (MATCH, MATCH2):
            todo = [r for r in range(len(entries)) if r not in used]
            free = [c for c in range(len(self.members)) if c not in taken]
            if not todo or not free:
                break
            sub = S[np.ix_(todo, free)]
            cost = np.where(sub >= floor, 100.0 - sub, 1e6)
            rows, cols = linear_sum_assignment(cost)
            for r, c in zip(rows, cols):
                if cost[r, c] < 1e6:
                    votes[self.members[free[c]]] = entries[todo[r]][0]
                    used.add(todo[r]); taken.add(free[c])
        return votes, [entries[r][1] for r in range(len(entries)) if r not in used]

    def best(self, raw):
        """Score as written, or as given name(s) + surname ("Henry Moore" -> Moore, initial h)."""
        S = self.scores(raw)
        words = raw.split()
        if len(words) >= 2 and re.match(r"^[A-Z][a-z]{2,}$", words[0]) and not re.search(r"\b(?:of|or|ot)\b", raw):
            S = np.maximum(S, self.scores(words[0][0] + ". " + " ".join(words[1:])) - 2)
        return S


CONFUSABLE = {"&": "68", "S": "58", "s": "5", "B": "8", "O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1",
              "i": "1", "Z": "2", "z": "2", "G": "6", "g": "9", "q": "9", "T": "7", "A": "4"}


def readings(raw, value):
    """Every number an OCR'd count could be: "&" -> {6, 8}, "1O" -> {10}; a leading letter may
    also be a stray mark (the dash's tail): "z2" -> {22, 2}, "l53" -> {153, 53}."""
    if not raw:
        return {value} if value is not None else set()
    variants = {raw}
    if len(raw) > 1 and not raw[0].isdigit():
        variants.add(raw[1:])
    out = {value} if value is not None else set()
    for v in variants:
        opts = [""]
        for ch in v:
            alts = ch if ch.isdigit() else CONFUSABLE.get(ch, "")
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

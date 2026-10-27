"""Build the member roster from the Legislative Reference Library's "Texas Legislators: Past & Present".

For each legislature and chamber, the member search lists who served; each member's profile page
has a Terms of Service table (chamber, district, exact dates, legislature, party, city/county,
note, counties in district). Raw pages are cached in data/raw/roster/.

Writes data/processed/members.csv (one row per person) and data/processed/terms.csv (one row per
person per legislature per chamber).

Usage: python pipeline/fetch_roster.py [first_leg] [last_leg]
"""
import csv, html, os, re, sys, urllib.parse
from datetime import datetime

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "roster")
OUT = os.path.join(ROOT, "data", "processed")
BASE = "https://lrl.texas.gov/legeLeaders/members/"
UA = "Mozilla/5.0 (X11; Linux x86_64) texas-nominate research crawler"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_util import get


def fetch(url, cache, data=None):
    if os.path.exists(cache):
        return open(cache, encoding="utf-8").read()
    body = get(url, data=data).decode("utf-8", "replace")
    with open(cache + ".part", "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(cache + ".part", cache)
    return body


def text(s):
    s = re.sub(r"<br\s*/?>", "; ", s)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip(" ;")


def legislatures(cell):
    """'89th' -> [89]; '71st - 73rd' -> [71, 72, 73]; '24th, 25th' -> [24, 25]."""
    nums = [int(n) for n in re.findall(r"(\d+)(?:st|nd|rd|th)", cell)]
    if re.search(r"\d+(?:st|nd|rd|th)\s*[-–]\s*\d+(?:st|nd|rd|th)", cell) and len(nums) == 2:
        return list(range(nums[0], nums[1] + 1))
    return nums


def date(s):
    s = s.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b. %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s


def member_ids(leg, chamber):
    page = fetch(BASE + "membersearch.cfm", os.path.join(RAW, f"search_{leg}{chamber}.html"),
                 data=urllib.parse.urlencode({"leg": leg, "chamber": chamber}).encode())
    return sorted({int(m) for m in re.findall(r"memberDisplay\.cfm\?memberID=(\d+)", page)})


def member(mid):
    page = fetch(BASE + f"memberDisplay.cfm?memberID={mid}", os.path.join(RAW, "members", f"{mid}.html"))
    name = text(re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1))
    full = re.search(r"Full Name:\s*(.*?)<br", page, re.S)
    table = page[page.find('id="termTable"'):]
    table = table[:table.find("</table>")]
    terms = []
    for row in re.findall(r"<tr class=\"resultRow\d\">(.*?)</tr>", table, re.S):
        c = [text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(c) < 8:
            continue
        dates = [date(x) for x in re.split(r"\s+-\s+", c[2])] + [""]
        city, _, county = c[5].partition(" / ")
        for leg in legislatures(c[3]):
            terms.append({"member_id": mid, "chamber": c[0], "leg": leg, "district": c[1],
                          "start": dates[0], "end": dates[1], "party": c[4], "city": city.strip(),
                          "county": county.strip(), "note": c[6], "counties": c[7]})
    return {"member_id": mid, "name": name, "full_name": text(full.group(1)) if full else name}, terms


def cached_ids():
    ids = set()
    for f in os.listdir(RAW):
        if f.startswith("search_") and f.endswith(".html"):
            ids.update(int(m) for m in re.findall(r"memberDisplay\.cfm\?memberID=(\d+)", open(os.path.join(RAW, f), encoding="utf-8").read()))
    return ids


def search_rows():
    """Member rows from the cached per-legislature search pages: {member_id: (name, [term dicts])}.
    Used for members whose own page isn't downloaded yet (names, party, home and counties are there)."""
    rows = {}
    for f in os.listdir(RAW):
        m = re.match(r"search_(\d+)([HS])\.html$", f)
        if not m:
            continue
        leg, chamber = int(m.group(1)), m.group(2)
        page = open(os.path.join(RAW, f), encoding="utf-8").read()
        for r in re.findall(r'<tr>\s*<th scope="row">(.*?)</tr>', page, re.S):
            mid = int(re.search(r"memberID=(\d+)", r).group(1))
            name = html.unescape(re.search(r'alt="Headshot of ([^"]+)"', r).group(1)) if "Headshot of" in r else \
                text(re.search(r">([^<]{3,})</a>", r).group(1))
            c = [text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            sessions = [int(x) for x in re.findall(r"(\d+)(?:st|nd|rd|th)", c[4])]
            parties = [x.strip() for x in c[5].split(";")]
            chambers = [x.strip() for x in c[2].split(";")]
            party = next((parties[i] for i, sv in enumerate(sessions) if sv == leg and i < len(parties)), parties[0] if parties else "")
            party = {"D": "Democrat", "R": "Republican"}.get(party, party)
            city, _, county = c[6].partition(",")
            t = {"member_id": mid, "chamber": chamber, "leg": leg, "district": c[1], "start": "", "end": "", "party": party,
                 "city": city.strip(), "county": county.strip(), "note": "", "counties": c[7]}
            rows.setdefault(mid, (name, []))[1].append(t)
    return rows


def write():
    """Rebuild members.csv and terms.csv from every cached member page, falling back to the
    search pages' summary rows for members whose page isn't downloaded yet."""
    people, terms, missing = [], [], 0
    fallback = search_rows()
    for mid in sorted(cached_ids()):
        if not os.path.exists(os.path.join(RAW, "members", f"{mid}.html")):
            missing += 1
            if mid in fallback:
                name, ts = fallback[mid]
                people.append({"member_id": mid, "name": name, "full_name": name})
                terms += ts
            continue
        p, t = member(mid)
        people.append(p)
        terms += t
    with open(os.path.join(OUT, "members.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["member_id", "name", "full_name"])
        w.writeheader(); w.writerows(people)
    with open(os.path.join(OUT, "terms.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["member_id", "chamber", "leg", "district", "start", "end", "party",
                               "city", "county", "note", "counties"])
        w.writeheader(); w.writerows(sorted(terms, key=lambda t: (t["leg"], t["chamber"], t["member_id"])))
    print(f"{len(people)} members, {len(terms)} terms written ({missing} from search-page summaries)", flush=True)


def main():
    """fetch_roster.py FIRST LAST            searches + member pages for those legislatures, then write
       fetch_roster.py searches FIRST LAST   only the per-legislature member lists
       fetch_roster.py members FIRST LAST    member pages for members of those legislatures
       fetch_roster.py write                 rebuild the CSVs from everything cached"""
    os.makedirs(os.path.join(RAW, "members"), exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    args = sys.argv[1:]
    mode = args.pop(0) if args and not args[0].isdigit() else "all"
    if mode == "write":
        return write()
    first, last = (int(args[0]), int(args[1])) if len(args) > 1 else (1, 89)
    ids = set()
    for leg in range(first, last + 1):
        for chamber in "HS":
            found = member_ids(leg, chamber)
            ids.update(found)
            print(f"{leg} {chamber}: {len(found)} members", flush=True)
    if mode in ("members", "all"):
        for i, mid in enumerate(sorted(ids)):
            member(mid)
            if i % 200 == 0:
                print(f"  member pages: {i}/{len(ids)}", flush=True)
    if mode == "all":
        write()


if __name__ == "__main__":
    main()

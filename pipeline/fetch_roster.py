"""Build the member roster from the Legislative Reference Library's "Texas Legislators: Past & Present".

For each legislature and chamber, the member search lists who served; each member's profile page
has a Terms of Service table (chamber, district, exact dates, legislature, party, city/county,
note, counties in district). Raw pages are cached in data/raw/roster/.

Writes data/processed/members.csv (one row per person) and data/processed/terms.csv (one row per
person per legislature per chamber).

Usage: python pipeline/fetch_roster.py [first_leg] [last_leg]
"""
import csv, html, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "roster")
OUT = os.path.join(ROOT, "data", "processed")
BASE = "https://lrl.texas.gov/legeLeaders/members/"
UA = "Mozilla/5.0 (X11; Linux x86_64) texas-nominate research crawler"

opener = urllib.request.build_opener(urllib.request.ProxyHandler({
    "http": os.environ.get("HTTP_PROXY", "http://127.0.0.1:3128"),
    "https": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:3128")}))


def fetch(url, cache, data=None):
    if os.path.exists(cache):
        return open(cache, encoding="utf-8").read()
    for i in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with opener.open(req, timeout=120) as r:
                body = r.read().decode("utf-8", "replace")
            break
        except Exception as e:
            if i == 3:
                raise
            time.sleep(5 * (i + 1))
    with open(cache, "w", encoding="utf-8") as f:
        f.write(body)
    time.sleep(0.4)
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


def main():
    first, last = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (1, 89)
    os.makedirs(os.path.join(RAW, "members"), exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    ids = set()
    for leg in range(first, last + 1):
        for chamber in "HS":
            found = member_ids(leg, chamber)
            ids.update(found)
            print(f"{leg} {chamber}: {len(found)} members", flush=True)
    people, terms = [], []
    for mid in sorted(ids):
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
    print(f"{len(people)} members, {len(terms)} terms", flush=True)


if __name__ == "__main__":
    main()

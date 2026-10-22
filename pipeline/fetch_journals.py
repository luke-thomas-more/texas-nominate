"""Download the House and Senate journals for the modern era (74th Legislature, 1995, onward).

Sources:
  - The Legislature's journal API (House 74th+, Senate 76th+): one HTML file per legislative day,
    or the typeset PDF where no HTML exists (House 74th-75th).
  - The Legislative Reference Library's day PDFs for the Senate 74th-75th, which the API lacks.

Files land in data/raw/journals/<H|S>/<leg><session>/ and an index of every day (date, pages,
source URL, local file) is written to data/raw/journals/index.json. Re-runnable: existing files
are skipped.

Usage: python pipeline/fetch_journals.py [first_leg] [last_leg]
"""
import json, os, re, sys, time, urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw", "journals")
API = "https://salsajournalwebservices.legis.texas.gov/api/v1/JournalWebSite"
HOSTS = {"H": "https://journals.house.texas.gov", "S": "https://journals.senate.texas.gov"}
LRL = "https://lrl.texas.gov"
UA = "Mozilla/5.0 (X11; Linux x86_64) texas-nominate research crawler"

opener = urllib.request.build_opener(urllib.request.ProxyHandler({
    "http": os.environ.get("HTTP_PROXY", "http://127.0.0.1:3128"),
    "https": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:3128")}))


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with opener.open(req, timeout=120) as r:
                return r.read()
        except Exception as e:
            if i == tries - 1:
                raise RuntimeError(f"{url}: {e}")
            time.sleep(5 * (i + 1))


def api_days(leg, chamber):
    """Every legislative day the API knows for one legislature and chamber."""
    try:
        data = json.loads(get(f"{API}/journals-for-legislature/{leg}/{chamber}"))
    except RuntimeError:
        return []
    if not isinstance(data, list):
        return []
    days = []
    for s in data:
        sess = s["session"].upper()
        for ld in s["legislativeDates"]:
            for d in ld["legislativeDays"]:
                path = d.get("htmlPath") or d.get("pdfPath")
                if not path:
                    continue
                days.append({"chamber": chamber, "leg": leg, "session": sess, "date": ld["legislativeDate"],
                             "day": d["legislativeDay"], "pages": d.get("pageRange"),
                             "url": HOSTS[chamber] + path, "kind": "html" if d.get("htmlPath") else "pdf"})
    return days


def lrl_days(leg, chamber, index):
    """Day PDFs from the Legislative Reference Library (used where the API has nothing)."""
    days = []
    for key, v in index.items():
        m = re.search(r"chamber=([HS])&legSession=(\d+)-(\w)", key)
        if not m or m.group(1) != chamber or int(m.group(2)) != leg:
            continue
        sess = "R" if m.group(3) == "0" else m.group(3)
        for p in v["pdfs"]:
            f = re.search(r"/(\d{2})(\d{2})(\d{4})_(\d+)_(\d+)\.pdf$", p)
            if f:
                days.append({"chamber": chamber, "leg": leg, "session": sess,
                             "date": f"{f.group(3)}-{f.group(1)}-{f.group(2)}", "day": f.group(4),
                             "pages": f.group(5), "url": LRL + p, "kind": "pdf"})
    return sorted(days, key=lambda d: (d["session"], int(d["day"])))


def main():
    first, last = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (74, 89)
    lrl_index = json.load(open(os.path.join(RAW, "lrl_index.json")))
    idx_path = os.path.join(RAW, "index.json")
    index = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    seen = {d["url"] for d in index}
    for leg in range(first, last + 1):
        for chamber in "HS":
            days = api_days(leg, chamber) or lrl_days(leg, chamber, lrl_index)
            got = 0
            for d in days:
                folder = os.path.join(RAW, chamber, f"{leg}{d['session']}")
                os.makedirs(folder, exist_ok=True)
                d["file"] = os.path.relpath(os.path.join(folder, os.path.basename(d["url"])), RAW)
                full = os.path.join(RAW, d["file"])
                if not os.path.exists(full):
                    try:
                        body = get(d["url"])
                    except RuntimeError as e:
                        print("FAILED", e, flush=True)
                        continue
                    with open(full + ".part", "wb") as f:
                        f.write(body)
                    os.replace(full + ".part", full)
                    got += 1
                    time.sleep(0.4)
                if d["url"] not in seen:
                    index.append(d)
                    seen.add(d["url"])
            print(f"{leg} {chamber}: {len(days)} days ({got} downloaded)", flush=True)
            json.dump(index, open(idx_path, "w"), indent=0)


if __name__ == "__main__":
    main()

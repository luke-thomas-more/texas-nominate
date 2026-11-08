"""Keep the scans' roll call ids stable from one release to the next.

extract_history numbers a session's roll calls in page order ("13R-Sx0438" is the 13th Legislature's 438th Senate
roll call read from the scans), so a rebuild that finds or drops one shifts every id after it: in the v1.1 rebuild
5,047 of 142,757 moved. Ids are what users cite and what the audit's readings are keyed to, so after a rebuild this
maps each roll call to the released one at the same place on the same journal page (same date, file, page and
column; the nearest line within 15 points), and keeps the released id. A roll call the release didn't have takes the
id of the one before it plus a letter ("13R-Sx0437a"), which sorts in place; a released id with no roll call left is
retired, never reused. The modern-format ids (by date and record number) are already stable and aren't touched.

  python pipeline/stable_ids.py REF FILE...

REF is the released rollcalls_hist.csv (data/processed/released/rollcalls_hist_<version>.csv). Rewrites
data/processed/rollcalls_hist.csv and then every FILE (text or .gz), replacing each id as a whole word; writes the
map to data/processed/rc_id_map.csv (new id as built, released id, how). Running it again changes nothing.
"""
import collections, csv, gzip, os, re, string, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
ID = re.compile(r"\b\d+[RA1-9]-[HS]x\d{4}[a-z]*\b")


def build_map(ref, cur):
    place = lambda r: (r["date"], r["file"], r["page"], r["col"])
    released = collections.defaultdict(list)
    for r in ref:
        released[place(r)].append((float(r["y"] or 0), r["rc_id"]))
    taken, out = set(), {}
    # exact line first, so a near match can't take a released id that has its own roll call
    for exact in (True, False):
        for r in cur:
            if r["rc_id"] in out:
                continue
            y = float(r["y"] or 0)
            cands = [(abs(y0 - y), i) for y0, i in released[place(r)] if i not in taken and abs(y0 - y) <= (0.5 if exact else 15)]
            if cands:
                i = min(cands)[1]
                out[r["rc_id"]] = (i, "same line" if exact else "nearby line")
                taken.add(i)
    used = {r["rc_id"] for r in ref}
    # a new roll call follows the one before it in the session (by the build's own order)
    by_sess = collections.defaultdict(list)
    for r in cur:
        by_sess[r["rc_id"].split("x")[0]].append(r["rc_id"])
    for sess, ids in by_sess.items():
        prev = sess + "x0000"
        for i in sorted(ids):
            if i in out:
                prev = out[i][0]
                continue
            base = re.sub(r"[a-z]+$", "", prev)
            new = next(base + s for s in string.ascii_lowercase if base + s not in used)
            used.add(new)
            out[i] = (new, "new")
            prev = new
    return out


def rewrite(path, m):
    op = gzip.open if path.endswith(".gz") else open
    tmp = path + ".tmp"
    sub = lambda mo: m.get(mo.group(0), mo.group(0))
    with op(path, "rt", newline="") as fi, op(tmp, "wt", newline="") as fo:
        for line in fi:
            fo.write(ID.sub(sub, line))
    os.replace(tmp, path)


def main():
    ref = list(csv.DictReader(open(sys.argv[1])))
    hist = os.path.join(PROC, "rollcalls_hist.csv")
    cur = list(csv.DictReader(open(hist)))
    full = build_map(ref, cur)
    m = {a: b for a, (b, _) in full.items() if a != b}
    kept = {b for b, _ in full.values()}
    with open(os.path.join(PROC, "rc_id_map.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["built_id", "rc_id", "how"])
        w.writerows([a, b, how] for a, (b, how) in sorted(full.items()))
        w.writerows(["", r["rc_id"], "retired"] for r in ref if r["rc_id"] not in kept)
    for p in [hist] + sys.argv[2:]:
        rewrite(p, m)
        print("rewrote", os.path.relpath(p, ROOT))
    n = collections.Counter(how for _, how in full.values())
    print(f"{len(cur)} roll calls: {n['same line']} on the released line, {n['nearby line']} nearby, {n['new']} new; "
          f"{len(m)} ids changed back; {sum(r['rc_id'] not in kept for r in ref)} released ids retired")


if __name__ == "__main__":
    main()

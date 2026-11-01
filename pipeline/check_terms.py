"""Votes credited to members who weren't in the chamber that day: names misread as a former or future member.

The name matcher only marks down members the roster has out of office (the roster's dates are sometimes off by
whole sessions: the 16th Senate's terms all "end" in 1879, yet its senators vote through the 1880 called
session), so a garbled name that fits one of them well enough still goes to him. Such a misreading shows: the
member turns up in one or two of the day's roll calls, where a member really there turns up in most (1961 House,
August 3: three members not sworn in until months later each in 1 of the day's 19 roll calls).

This pass, run after extract_history.py, takes out every yea or nay by a member the roster has out of office that
day whom fewer than half of that day's roll calls (chamber and session) place, and re-checks the roll call: one
exact before and now one vote short is "near" (a name too garbled to read, left out), as is one still within one
of each printed total; a misprint or misread total accepted on a full roll, or a list completed from the roll, is
no longer verified, nor is anything else now further off. Also: a "roll call" with no nays whose journal sentence is a call of the roll for attendance ("The roll was called
and the following Senators were present:", "the following members answered to their names") isn't a vote; its
status becomes "attendance" (1900, 1927, 1932, 1949 and 1953 openings and quorum calls).

Rewrites rollcalls_hist.csv and votes_hist.csv.gz in place (HIST_OUT, default
data/processed), keeping the originals as *.pre_terms; writes check_terms_report.txt beside them.
Usage: HIST_OUT=data/raw/v12_stage/processed python pipeline/check_terms.py
"""
import collections, csv, gzip, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from match_votes import load_roster, serving

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.environ.get("HIST_OUT", os.path.join(ROOT, "data", "processed"))
GOOD = {"ok", "near", "ok_digit", "ok_lists", "ok_rest"}
ATTENDANCE = re.compile(r"(?i)following\s+(?:members|senators|representatives|gentlemen)\s+(?:were\s+)?(?:present|answered)"
                        r"|answered\s+to\s+(?:their|the)\s+names")


def main():
    rc_path, v_path = os.path.join(OUT, "rollcalls_hist.csv"), os.path.join(OUT, "votes_hist.csv.gz")
    people, terms, aliases = load_roster()
    term = collections.defaultdict(list)
    for (ch, leg), ts in terms.items():
        for t in ts:
            term[(ch, leg, t["member_id"])].append(t)
    rows = list(csv.DictReader(open(rc_path)))
    cols = list(rows[0].keys())
    by_id = {r["rc_id"]: r for r in rows}
    with gzip.open(v_path, "rt") as f:
        votes = list(csv.reader(f))
    head, votes = votes[0], votes[1:]
    placed = collections.defaultdict(dict)
    for rc, m, v in votes:
        placed[rc][int(m)] = v
    day = collections.defaultdict(list)
    for r in rows:
        if r["source"] != "record":
            day[(r["leg"], r["chamber"], r["session"], r["date"])].append(r["rc_id"])
    drop = set()                                   # (rc_id, member_id)
    for key, ids in day.items():
        seen = collections.Counter(m for k in ids for m in placed[k])
        for k in ids:
            r = by_id[k]
            for m, v in placed[k].items():
                ts = term.get((r["chamber"], int(r["leg"]), m), [])
                if v in ("Y", "N") and ts and not any(serving(t, r["date"]) for t in ts) and 2 * seen[m] < len(ids):
                    drop.add((k, m))
    report, status = [], collections.Counter()
    touched = collections.Counter(k for k, _ in drop)
    for k, n in sorted(touched.items()):
        r = by_id[k]
        before = r["status"]
        lost = collections.Counter(placed[k][m] for kk, m in drop if kk == k)
        ny, nn = int(r["n_y"]) - lost["Y"], int(r["n_n"]) - lost["N"]
        r["n_y"], r["n_n"] = str(ny), str(nn)
        if before in GOOD:
            # exact before, so one vote out leaves it one short of its total, however that total was read; otherwise
            # still within one of each printed total, or no longer verified (a misprint or misread total accepted
            # on a full roll, or a list completed from the roll, rested on the misread name too)
            py, pn = r["printed_y"], int(r["printed_n"] or 0)
            within = py != "" and abs(int(py) - ny) <= 1 and abs(pn - nn) <= 1
            r["status"] = ("near" if before == "ok" and n == 1 or before in ("ok", "near") and within else "count_mismatch")
        status[(before, r["status"])] += 1
        report.append(f"{k} {r['date']} {before}->{r['status']} dropped " + " ".join(f"{m}:{placed[k][m]}" for kk, m in sorted(drop) if kk == k))
    attendance = [r for r in rows if r["status"] in GOOD and int(r["n_n"]) == 0 and ATTENDANCE.search(r["text"])]
    for r in attendance:
        report.append(f"{r['rc_id']} {r['date']} {r['status']}->attendance: {r['text'][:80]}")
        r["status"] = "attendance"
    for p in (rc_path, v_path):
        shutil.copy2(p, p + ".pre_terms")
    with open(rc_path, "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(rows)
    with gzip.open(v_path, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(head)
        w.writerows(v for v in votes if (v[0], int(v[1])) not in drop)
    open(os.path.join(OUT, "check_terms_report.txt"), "w").write("\n".join(report) + "\n")
    print(f"check_terms: {len(drop)} votes by out-of-office members dropped from {len(touched)} roll calls; status changes {dict(status)}; "
          f"{len(attendance)} attendance calls set aside")


if __name__ == "__main__":
    main()

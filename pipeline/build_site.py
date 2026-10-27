"""Build the site's data files (docs/data/) from the processed votes and the fit.

  index.json        legislatures, chambers, axis names, fit summary
  s/<leg><H|S>.json one legislature and chamber: members with positions, and every verified roll
                    call with its context, tallies, cutting line, and each member's vote as one
                    character per member (Y yea, N nay, P present not voting, E excused, A absent,
                    - not listed)
  about.json        the About page, with the numbers filled in
  downloads/        CSVs for anyone who wants the data
"""
import collections, csv, gzip, json, os, shutil, sys
from datetime import date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
FIT = os.environ.get("FIT_DIR", os.path.join(PROC, "fit"))
DOCS = os.environ.get("DOCS_DIR", os.path.join(ROOT, "docs", "data"))
SESSION_NAMES = {"R": "Regular session", "A": "Adjourned session", "P": "Provisional session"}
GOOD = {"ok", "near", "ok_digit", "ok_lists"}       # verified roll calls (see extract_history.py)
# the Library's party labels, spelled out where it abbreviates
PARTY_NAMES = {"PO": "Populist", "GB": "Greenback", "ID": "Independent Democrat", "RA": "Radical Republican",
               "U": "Union", "GG": "Greenback", "American Party of the United States": "American Party"}


def session_name(s):
    if s in SESSION_NAMES:
        return SESSION_NAMES[s]
    n = int(s)
    return f"{n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'} called session"


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def eras_html(axes):
    """The About page's era-by-era guide to the axes (the same wording the map pages show)."""
    from html import escape
    out = []
    for e in axes.get("eras", []):
        x, y = {**axes["x"], **e.get("x", {})}, {**axes["y"], **e.get("y", {})}
        out.append(f'<h3>{escape(e["label"])}</h3>\n<p><b>Left to right: {escape(x["name"])}.</b> {escape(x["desc"])}</p>\n'
                   f'<p><b>Up and down: {escape(y["name"])}.</b> {escape(y["desc"])}</p>')
    return "\n".join(out)


def repair_dates(rows, span):
    """Dates as YYYY-MM-DD within the legislature's service dates. The modern journals' API adds a
    time ("2011-05-23 00:00:00Z"), and a few scan file names carry a wrong year ("1979" among
    1879-80 pages, "968-"), which fix_dates in extract_history doesn't catch. A date outside the
    legislature gets the year that puts it inside its session's run of dates."""
    lo, hi = span
    for r in rows:
        r["date"] = r["date"][:10]
    good = lambda d: len(d) == 10 and d[:4].isdigit() and lo <= d <= hi
    by_sess = collections.defaultdict(list)
    for r in rows:
        if good(r["date"]):
            by_sess[r["session"]].append(r["date"])
    fixed = 0
    for r in rows:
        if good(r["date"]):
            continue
        ds = sorted(by_sess.get(r["session"]) or [lo, hi])
        md = r["date"][-5:]
        cands = [f"{y}-{md}" for y in range(int(lo[:4]), int(hi[:4]) + 1) if good(f"{y}-{md}")]
        if cands:
            mid = ds[len(ds) // 2]
            r["date"] = min(cands, key=lambda c: (not ds[0] <= c <= ds[-1], abs(int(c[:4]) - int(mid[:4]))))
            fixed += 1
    return fixed


def party_code(p):
    return "R" if p.startswith("Republican") or p == "RA" else "D" if p.startswith("Democrat") else "O"


def party_name(p):
    """The party as written out, for members outside the two major parties ("" if not known)."""
    return "" if p in ("", "Unknown", "O") else PARTY_NAMES.get(p, p)


def main():
    os.makedirs(os.path.join(DOCS, "s"), exist_ok=True)
    axes = json.load(open(os.path.join(ROOT, "pipeline", "axes.json")))
    fit = json.load(open(os.path.join(FIT, "summary.json")))
    # fits with other numbers of dimensions (same model and data, warm-started from this fit, in
    # <fit>_k1/ and <fit>_k3/), for the About page's comparison table
    for k in (1, 2, 3):
        other = os.path.join(f"{FIT}_k{k}", "summary.json")
        if os.path.exists(other):
            fit["by_dims"].update(json.load(open(other))["by_dims"])
    people = {int(r["member_id"]): r for r in csv.DictReader(open(os.path.join(PROC, "members.csv")))}
    terms = {(int(t["member_id"]), int(t["leg"]), t["chamber"]): t for t in csv.DictReader(open(os.path.join(PROC, "terms.csv")))}
    scores = {(int(r["member_id"]), int(r["leg"]), r["chamber"]): r for r in csv.DictReader(open(os.path.join(FIT, "member_scores.csv")))}
    params = {r["rc_id"]: r for r in csv.DictReader(open(os.path.join(FIT, "rollcall_params.csv")))}
    day_url = {d["file"]: d["url"] for d in json.load(open(os.path.join(ROOT, "data", "raw", "journals", "index.json")))}

    # roll calls from both pipelines: the record-vote format (1973 on) and the older scanned journals
    rcs = collections.defaultdict(list)
    excluded = collections.Counter()
    votes = collections.defaultdict(dict)
    for rc_file, v_file in (("rollcalls.csv", "votes.csv.gz"), ("rollcalls_hist.csv", "votes_hist.csv.gz")):
        if not os.path.exists(os.path.join(PROC, rc_file)):
            continue
        for r in csv.DictReader(open(os.path.join(PROC, rc_file))):
            if r["status"] in GOOD:
                rcs[(int(r["leg"]), r["chamber"])].append(r)
            else:
                excluded[(int(r["leg"]), r["chamber"])] += 1
        with gzip.open(os.path.join(PROC, v_file), "rt") as f:
            rd = csv.reader(f); next(rd)
            for rc_id, mid, v in rd:
                votes[rc_id][int(mid)] = "E" if v == "M" else v

    # each legislature's span, from its members' service dates
    span = {}
    for t in terms.values():
        if t.get("start") and t.get("end"):
            a, b = span.get(int(t["leg"]), (t["start"], t["end"]))
            span[int(t["leg"])] = (min(a, t["start"]), max(b, t["end"]))
    fixed = sum(repair_dates(rcs[k], span[k[0]]) for k in rcs if k[0] in span)
    print(f"dates repaired: {fixed}")

    session_caps = json.load(open(os.path.join(ROOT, "data", "interim", "captions.json")))
    legislatures = []
    for (leg, ch) in sorted(rcs, key=lambda k: (-k[0], k[1])):
        rows = sorted(rcs[(leg, ch)], key=lambda r: (r["date"], r["session"] != "R", r["session"], int(r["record"] or 0), r["rc_id"]))
        # a vote that names its bill but missed the caption borrows it from another vote on the same bill
        cap_by_bill = {(r["session"], r["bill"]): r["caption"] for r in rows if r["bill"] and r["caption"]}
        for r in rows:
            if r["bill"] and not r["caption"]:
                r["caption"] = cap_by_bill.get((r["session"], r["bill"])) or \
                    session_caps.get(f"{ch}|{leg}|{r['session']}", {}).get(r["bill"], "")
        mids = sorted({m for r in rows for m in votes[r["rc_id"]]},
                      key=lambda m: people[m]["name"].split()[-1].lower())
        pos = {m: i for i, m in enumerate(mids)}
        members = []
        for m in mids:
            t = terms.get((m, leg, ch), {})
            sc = scores.get((m, leg, ch))
            members.append({"id": m, "n": people[m]["name"], "p": party_code(t.get("party", "")),
                            **({"pn": party_name(t.get("party", ""))} if party_code(t.get("party", "")) == "O" and
                               party_name(t.get("party", "")) else {}),
                            "d": t.get("district", ""),
                            "x": round(float(sc["dim1"]), 3) if sc else None,
                            "y": round(float(sc["dim2"]), 3) if sc and "dim2" in sc else None,
                            "v": int(sc["scaled_votes"]) if sc else 0})
        caps, cap_idx, days, day_idx = [], {}, [], {}
        out_rc = []
        for r in rows:
            v = ["-"] * len(mids)
            for m, code in votes[r["rc_id"]].items():
                v[pos[m]] = code
            if r["caption"] and r["caption"] not in cap_idx:
                cap_idx[r["caption"]] = len(caps); caps.append(r["caption"])
            url = day_url.get(r["file"], "")
            if url not in day_idx:
                day_idx[url] = len(days); days.append(url)
            item = {"id": r["rc_id"], "s": r["session"], "dt": r["date"], "r": int(r["record"]) if r["record"] else None,
                    "b": r["bill"], "c": cap_idx.get(r["caption"]), "t": r["text"][:300], "m": r["motion"][:300],
                    "a": r["amendment"][:240], "y": int(r["n_y"]), "n": int(r["n_n"]), "p": int(r["printed_p"]),
                    "f": day_idx[url], "v": "".join(v)}
            if r.get("page"):
                item["pg"] = int(r["page"]) + 1          # page within the day's PDF, for a #page= link
            p = params.get(r["rc_id"])
            if p:
                item["mid"] = [round(float(p["mid1"]), 4), round(float(p["mid2"]), 4)]
                item["spr"] = [round(float(p["spread1"]), 4), round(float(p["spread2"]), 4)]
                item["err"] = int(p["errors"])
            out_rc.append(item)
        years = sorted({r["date"][:4] for r in rows})
        label = f"{years[0]}–{str(int(years[0]) + 1)[2:]}"
        sessions = {s: session_name(s) for s in sorted({r["session"] for r in rows}, key=lambda s: (s != "R", s))}
        data = {"leg": leg, "ch": ch, "years": label, "sessions": sessions, "days": days, "caps": caps,
                "members": members, "rc": out_rc}
        with open(os.path.join(DOCS, "s", f"{leg}{ch}.json"), "w") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        entry = next((l for l in legislatures if l["leg"] == leg), None)
        if not entry:
            entry = {"leg": leg, "years": label, "chambers": {}}
            legislatures.append(entry)
        entry["chambers"][ch] = {"members": len(members), "rollcalls": len(out_rc),
                                 "scaled": sum(1 for x in out_rc if "mid" in x), "excluded": excluded[(leg, ch)]}
        print(f"{leg}{ch}: {len(members)} members, {len(out_rc)} roll calls ({entry['chambers'][ch]['scaled']} scaled)", flush=True)

    index = {"built": date.today().isoformat(), "dims": fit["chosen_dims"], "w": fit["w"], "beta": fit["beta"],
             "axes": axes, "legislatures": legislatures,
             "fit": {k: fit["by_dims"][str(k)] if str(k) in fit["by_dims"] else fit["by_dims"][k] for k in fit["by_dims"]},
             "totals": {"rollcalls": sum(c["rollcalls"] for l in legislatures for c in l["chambers"].values()),
                        "scaled": fit["roll_calls_scaled"], "members": fit["members"],
                        "both_chambers": fit["members_both_chambers"], "votes": fit["votes"],
                        "excluded": sum(excluded.values())}}
    json.dump(index, open(os.path.join(DOCS, "index.json"), "w"), indent=1, ensure_ascii=False)

    # about page with numbers filled in
    vv = json.load(open(os.path.join(PROC, "validation_voteview.json"))) if os.path.exists(os.path.join(PROC, "validation_voteview.json")) else None
    vt = json.load(open(os.path.join(PROC, "validation_texas.json"))) if os.path.exists(os.path.join(PROC, "validation_texas.json")) else None
    tmpl = open(os.path.join(ROOT, "pipeline", "about.html")).read()
    by = index["fit"]
    fill = {
        "ROLLCALLS": f"{index['totals']['rollcalls']:,}", "SCALED": f"{index['totals']['scaled']:,}",
        "MEMBERS": f"{index['totals']['members']:,}", "BOTH": f"{index['totals']['both_chambers']}",
        "VOTES": f"{index['totals']['votes']:,}", "EXCLUDED": f"{index['totals']['excluded']:,}",
        "FIRST_ORD": ordinal(min(l["leg"] for l in legislatures)), "LAST_ORD": ordinal(max(l["leg"] for l in legislatures)),
        "YEAR0": min(l["years"][:4] for l in legislatures), "YEAR1": str(max(int(l["years"][:4]) for l in legislatures)),
        "AXES_ERAS": eras_html(axes),
        "CORRECT1": f"{100 * by['1']['correct']:.1f}" if "1" in by else "–", "APRE1": f"{by['1']['apre']:.2f}" if "1" in by else "–",
        "CORRECT2": f"{100 * by['2']['correct']:.1f}" if "2" in by else "–", "APRE2": f"{by['2']['apre']:.2f}" if "2" in by else "–",
        "CORRECT3": f"{100 * by['3']['correct']:.1f}" if "3" in by else "–", "APRE3": f"{by['3']['apre']:.2f}" if "3" in by else "–",
        "W2": f"{fit['w'][1]:.2f}" if len(fit["w"]) > 1 else "–", "BETA": f"{fit['beta']:.1f}",
        "VV_R1": f"{vv['k1']['corr_dim1']:.3f}" if vv and "k1" in vv else "–",
        "VV_RW": f"{min(vv['k1']['corr_dim1_within_party'].values()):.2f}" if vv and "k1" in vv else "–",
        "VV_R21": f"{vv['k2']['corr_dim1']:.3f}" if vv and "k2" in vv else "–",
        "VV_R2": f"{vv['k2']['corr_dim2']:.2f}" if vv and "k2" in vv else "–",
        "VV_ROT": f"{abs(vv['k2']['rotation_deg']):.0f}" if vv and "k2" in vv else "–",
        "VV_CONG": f"{vv['congresses'][0]}th–{vv['congresses'][1]}th" if vv else "–",
        "SM_N": str(vt["matched_legislators"]) if vt else "–", "SM_R": f"{vt['corr_overall']:.2f}" if vt else "–",
        "SM_RD": f"{vt['corr_within_D']:.2f}" if vt else "–", "SM_RR": f"{vt['corr_within_R']:.2f}" if vt else "–",
        "BUILT": index["built"],
    }
    for k, v in fill.items():
        tmpl = tmpl.replace("{{" + k + "}}", v)
    for k, v in axes.items():
        if not isinstance(v, dict):          # "eras": the per-era wording, used by the app
            continue
        for kk, vv2 in v.items():
            tmpl = tmpl.replace("{{AX_" + k.upper() + "_" + kk.upper() + "}}", vv2)
    json.dump({"html": tmpl}, open(os.path.join(DOCS, "about.json"), "w"), ensure_ascii=False)

    # downloads
    dl = os.path.join(DOCS, "downloads")
    os.makedirs(dl, exist_ok=True)
    shutil.copy(os.path.join(FIT, "member_scores.csv"), os.path.join(dl, "member_scores.csv"))
    with open(os.path.join(dl, "members.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["member_id", "name", "full_name"])
        for m in sorted(people): w.writerow([m, people[m]["name"], people[m]["full_name"]])
    with gzip.open(os.path.join(dl, "rollcalls.csv.gz"), "wt", newline="") as f:
        cols = ["rc_id", "chamber", "leg", "session", "date", "record", "bill", "caption", "text", "motion", "amendment", "n_y", "n_n", "printed_p"]
        w = csv.writer(f); w.writerow(cols + ["mid1", "mid2", "spread1", "spread2"])
        for key in sorted(rcs):
            for r in rcs[key]:
                p = params.get(r["rc_id"], {})
                w.writerow([r[c] for c in cols] + [p.get("mid1", ""), p.get("mid2", ""), p.get("spread1", ""), p.get("spread2", "")])
    with gzip.open(os.path.join(dl, "votes.csv.gz"), "wt", newline="") as f:
        w = csv.writer(f); w.writerow(["rc_id", "member_id", "vote"])
        ok = {r["rc_id"] for v in rcs.values() for r in v}
        for rc_id, vs in votes.items():
            if rc_id in ok:
                for m, code in vs.items():
                    w.writerow([rc_id, m, code])
    print("site data written")


if __name__ == "__main__":
    main()

# Audit findings, as they come (2026-11-01 on)

Yea and nay errors are counted by `pipeline/audit.py report`. This file keeps the patterns behind them, and anything
else the readings turn up, for the v1.0 data pass.

## v1.2 (2026-11-10)

Decisions made on my own judgement for this pass (Luke: "go for it, prioritize 4"), for later review.

- **Traced: the wrong-member matches v1.1 left** (all in `pipeline/match_scans.py`):
  - 20R-Hx0107 "Newton of C." was read "Nt>wtonof C": the "of" glued to the name with a one-letter county wasn't taken
    as a county, so the name went to whichever Newton was free. Now parsed (three letters before a glued "of").
  - 16R-Hx0258 "Smith of Harris" was read "Smith ,pf Harris": "pf" wasn't a known misreading of "of", so the whole
    string scored 56 against every Smith and went to a free one in the second tier. "pf" added.
  - 8R-Hx0292 "Daniels" was read "Danids". Folding OCR look-alikes ("ni" ~ "m") made it "damds", nearer Edwards than
    Daniel. A name is now compared as spelled as well as folded (the better counts), and between names equally
    near (Daniel and Dennis, both 67) a small edge goes to the one that starts the same way.
  - 121-Hx0301 "Stirman" was read "enman": Evans 60, Stirman 50, and Evans, absent all day, was free. A yea or nay
    too garbled for a confident match now goes only to a member whom some roll call that day has voting with
    confidence (days with three or more roll calls; `SessionMatcher.learn_days`). Absent, excused and
    present-not-voting lists are not restricted: those are exactly the members with no votes that day.
  - 15R-Hx0482 "McKinnion" read "MeKinnion", folded to "mekmmon", went to Manion; as spelled it is McKinnon.
  - 32R-Hx0252 Lee/Teel: the yea "Lee" was read "T...~ee." and the excused "Teel" as "Tee!."; both came out "tee" and
    the two members were handed out the wrong way round. "!" after a lowercase letter is now read as "l". Watson's yea
    ("VfltMn.") stays lost: nothing in the reading says Watson.
  - 21R-Hx0010's false nay for Moody was not a page-break problem (my note of Nov 2 guessed wrong): a two-letter
    speck "oo" among the nays scored 57 against "Moody" and was placed in the second tier. A string of fewer than
    three letters can no longer be matched weakly.
- **"Absent, not voting" (1885) coded P.** `parse_scans.cell_label` had no such label and took the nearest,
  "present not voting". Added, with "Absent and not voting"; the 19th Legislature's 2,822 P codes should become A.
  No effect on the map (neither is a yea or nay).
- **The rebuilt data, reviewed against v1.1 before the refit** (every scan re-matched; ids kept by `stable_ids.py`):
  - Verified scan roll calls 134,271 -> 134,492; yeas and nays in them 8,632,884 -> 8,656,144. 337 roll calls that
    failed their checks now pass; 134 that passed now fail (mostly a weak match no longer allowed by the day rule,
    which leaves a name unresolved and the roll call outside "within one"; a few where the combined reading of two
    OCRs no longer comes out exact). Among roll calls verified both times, 1,658 differ in a yea or nay, 1,515 of
    them by one or two names.
  - Against the first audit sample's 159 scan roll calls: wrong votes 9 -> 3, lost votes 32 -> 22, P/A code errors
    33 -> 1. Every case traced above is fixed (121-Hx0301 too), and 32R-Hx0252 regained Watson from the other OCR.
    Three new errors: 8R-Hx0292 gained a false yea for Kinney (the layer split "Franklin" into "Frnn" and "klin";
    one took Franklin, the other now scores 60 against Kinney as spelled); 23R-Hx0019 and 32R-Hx0252 each lost one
    name (Barbee of Wharton, Hall) by passing to a different OCR reading that gained another (King of Ellis; Lee and
    Watson). Left as they are: the first sample was used to find these bugs, so it no longer measures anything.
  - The 20 scan roll calls of the fresh sample read by then: identical in both versions.
  - Modern journals and tally sheets (same matcher): 87,135 roll calls both times, 167 vote cells differ, 70 roll
    calls move from a looser check to exact, 3 fail that passed.
- **Traced: the weak agreement with W-NOMINATE before 1961** (item 4, the priority; `pipeline/wnominate_trace.py`,
  `wnominate_halves.py`, `data/processed/wnominate_trace_v7.log`). v1.1 turned each session's W-NOMINATE map onto
  ours and correlated axis by axis, by OUR axes: median r 0.68 on axis 1 before 1961. That number was a labelling
  artifact. Taking W-NOMINATE's dimensions as it orders them (first = the session's main divide) and predicting each
  from both of our axes (multiple R), on the v1.2 fit:
  - The main divide is recovered in every era: median R 0.89, 0.90, 0.92, 0.87 for 1846–73, 1874–1900, 1901–30,
    1931–60, against 0.91–0.98 since 1961. Before 1961 it lies nearer our axis 2 than axis 1 in 55–79% of sessions
    (0–39% since), so "axis 1 against axis 1" was mostly comparing our first axis with the session's second divide.
  - The real gap is each session's own second divide: R 0.57–0.63 before 1961 (0.44–0.55 since). It isn't noise:
    W-NOMINATE on two interleaved halves of a session's votes finds it in both (0.70–0.90, stepped up to full length
    0.82–0.95). A third shared dimension helps little (0.64–0.69). Our own estimator fit to one legislature at a
    time (1st–44th only; the later ones take hours each) matches W-NOMINATE at 0.96–0.98 and 0.78–0.88, so it isn't
    the estimator either.
  - Reading: one map for 180 years with one position per career can't carry every session's own second quarrel.
    The alternative would be era-specific or per-session axes, which gives up the shared space Luke chose. **No
    model change.** The About page and README now report R of each W-NOMINATE dimension given the map and say what
    the old 0.68 was.
- **Refit (fit_v7)** from fit_v6 with beta and the weights held (`FIX_BETA=1 FIX_W=1`): every era within 2.1 degrees
  of v1.1, r ≥ 0.992 on both axes. Sensitivity refits on the v1.2 data: exact-total roll calls only, at most 3.3
  degrees and r ≥ 0.94; without the Senate's implied yeas, at most 1.4 degrees and r ≥ 0.998; without the 1973–82
  tally sheets the eras since 1973 turn 8–15 degrees (r ≥ 0.88), as in v1.1.
- **A fresh sample for an honest error rate** (`audit.py sample fresh`, seed in the script; `audit/sample_fresh.csv`,
  `report_fresh.json`, `audit_fresh.csv`): 94 roll calls drawn the same way as the first sample, from the v1.1 list
  (so the 337 roll calls v1.2 newly verifies couldn't be drawn; all 94 are still in the data), read off the page
  images (6,413 names) and used for nothing else. Scored against v1.2: **5 votes wrong, 28 lost; weighted, 0.02% of
  yeas and nays wrong (95% interval 0.01–0.04%) and 0.11% lost (0.04–0.20%); 5.0% of roll calls carry an error.**
  The first sample, which found the bugs fixed in v1.0–v1.2, now shows 0.00% wrong (4 votes) and 0.05% lost (26) and
  flatters the data; the fresh figures are the ones to quote. As before the errors gather in the looser checks (3
  wrong and 27 lost in 28 roll calls, against 2 wrong and 1 lost in 66 exact ones) and in the oldest journals
  (1846–73: 2 wrong and 18 lost in 12 roll calls; nothing wrong or lost in the 22 since 1983).
  **Nothing found in the fresh sample was fixed**, so it stays a fair measure. Open, for a later data pass:
  - Wrong: 1R-Hx0176 (ok; "Edwards of San Augustine", William C. Edwards [5224], nay credited to Haden Harrison
    Edwards [5036]); 12R-Hx0521 (near; a yea for Ira Hobart Evans [4395], who isn't on the page); 23R-Sx0398 (near;
    Kearby's nay recorded as a yea, Presler's yea lost); 434-Sx0065 (near; Woodruff's nay credited to Woodul);
    74R-S1150 (ok, yeas implied; "Present-not voting: Leedom" was given to Teel Bivins, so Leedom has a yea he didn't
    cast and Bivins lost his).
  - Lost: 6R-Sx0058 (near, 1856 Senate: the whole nay list, 16 names, and the yea Bryan; the data has it 14–0 against
    the page's 15–16, the worst roll call in either sample); 13R-Sx0246 (Word); 15R-Hx0231 (Adams, nay; Dennis, yea;
    v1.1 also had Manion's nay as a yea, right in v1.2); 34R-Sx0840 (Bee); 35R-Hx0056 (Parks);
    39R-Hx0095 (Hall); 551-Sx0106 (Herring); 57R-Hx0907 (Cowles); 60R-Hx0828 (Scoggins of Nueces); 66R-HT0573 (tally
    sheet: Ezzell's nay recorded as excused).
  - Codes for non-voters (not scaled, not counted): 57R-Hx0907 has Thurman, absent, as present-not voting and the
    Speaker, Turman, as absent (the two names crossed); 66R-HT0180 has Whaley, present-not voting, as excused.
    77R-S0977 prints "Present-not voting: Mr. President" for Senator Ratliff, then presiding: the data is right.
    23R-Hx0693 leaves out Ragsdale, excused.
  - Fixed between v1.1 and v1.2 without looking (the matcher changes above): 67R-HT0180 regained Wright's yea.
  - Scoring change: a member the page lists as not voting whom the data credits with a yea or nay is now counted
    as a wrong vote (`audit.tally_rc`). The yea and nay lists couldn't show it, and with implied yeas (74R-S1150)
    nothing else can. It adds one wrong vote to the fresh sample and none to the first.
  - Tool note: `audit.py show` pointed at pp. 201–203 for the tally sheet 66R-HT1237; the data's `journal_page`
    (175) is right. Only the audit helper's page arithmetic is off for that file.
- **Not done, by agreement:** bill and motion labels, multi-day Senate dating, the 1876 "not voting" lists, the 119
  roll calls with no journal page.

## v1.1 (2026-11-08)

- **Readings renamed to v1.0 ids.** The scans number roll calls in page order, so the rebuild for v1.0 shifted ids, and
  ten readings made on Nov 1–2 (against the Oct 30 data) named roll calls that v1.0 calls by the id before:
  12A-Hx0463 → 12A-Hx0462, 12A-Hx0523 → 12A-Hx0522, 13R-Sx0399 → 13R-Sx0398, 13R-Sx0438 → 13R-Sx0437,
  22R-Hx0146 → 22R-Hx0145, 2R-Hx0098 → 2R-Hx0097, 49R-Hx0960 → 49R-Hx0959, 53R-Hx1362 → 53R-Hx1360,
  59R-Hx0825 → 59R-Hx0823, 62R-Hx1872 → 62R-Hx1871 (each matched on its date, page and printed totals; sample.csv
  follows). The v1.0 report scored them against the Oct 30 data, where they were right. From v1.1 on,
  `pipeline/stable_ids.py` keeps released ids fixed, so this can't recur.
- **Report on the v1.1 data:** 240 of 244 roll calls read (16,486 names); wrong 0.02% of yeas and nays, lost 0.07%
  (v1.0: 0.04% and 0.08%). 53R-Sx0758 (the 1953 Senate tie, its yeas read as "present") no longer passes the checks
  and is left out.
- **County binding didn't reach its cases.** The matcher now treats a printed county as binding, but 16R-Hx0258
  ("Smith of Harris") and 20R-Hx0107 ("Newton of C.") still credit the other member; 8R-Hx0292, 121-Hx0301 and
  15R-Hx0482 (misread names) are unchanged too. Not traced yet: left for the next data pass.

## Patterns

- **near** roll calls carry the errors so far: an extra yea (12A-Hx0522: R. L. Moore of Red River, beside the
  printed Moore of Harrison), a misread name taken for another member (13R-Sx0437: "Word" read as Ford) with
  another name lost (Saylor), a name lost to a blot (19R-Hx0372: "Camp of‖Limestone"). Each passed because its
  count came within one of the printed total.
- **ok_lists** let a short list through (351-Hx0156, 1917 House: Swope and White, yeas, lost by the OCR). The
  heading's total was misprinted (84 over 82 names) and the rule asks only that the lists place at least the
  session's usual full roll less one (extract_history.judge: `len(votes) >= full - 1`, with `full` itself a
  90th percentile); here they placed 140 of 142. Fix for v1.0: also require that every member the day's other
  roll calls place appears in one of its lists.
- **Tally grids (1973-78)**: hand-drawn ticks add votes the machine missed. 65R-HT0879 (1977): ticks in the Y box for
  Allred, Evans, Hill D. and Olson make the printed 126 yeas; the data has three of them as yeas and Evans as E
  (status near, 125 against 126). The grid's X (absent-excused) marks sit in the P column and come out as P for
  most members; a blank row (Slack) also came out P.
- **Tally printouts (1979-82)**: 66R-HT0843 read exactly on yeas and nays (90-33); three P codes came out E.
- **ok_lists is looser in code than in the README.** The README says the misprinted total is "a few off"; the code
  (extract_history.judge) asks only that the nay total check and the lists place the whole chamber, with no limit on
  the yea gap. So a heading missed or misread inside the yea list, which moves the rest of the yeas into the next
  list, passes: 58R-Hx0348 (1963 House, printed 147-1) has 39 yeas and 109 "excused"; 53R-Sx0758 (1953 Senate tie,
  13-13) has 2 yeas and 11 "present", because "Ramsey, President" (the Lieutenant Governor breaking the tie) heading
  the yea list was read as a "Present" heading. 434 of 1,321 ok_lists roll calls are more than 3 off a printed
  total (332 scaled). Mislabeled yeas become non-votes, so the scores lose votes rather than gain wrong ones.
  A targeted check (audit/targeted/, 12 drawn at random from the 434, seed 20261102) found that a big gap is
  usually not an error: the six read were all right, the journal having misprinted its total (63 over 60 names,
  97 over 103, 104 over 111) or the OCR having misread it (53 as 3, 63 as 13). What marks the bad ones is a
  non-voting list far bigger than that day's other roll calls have (58R-Hx0348: 109 "excused" against a day
  median of 1, its yea list cut where it ran onto the next page). None of the 12 has that; 39 ok_lists roll
  calls do (27 scaled; House more than 15 over the day's median, Senate more than 5).
  **v1.0 fix:** demote those to count_mismatch, or better, find why a list continued on the next page takes
  the last heading seen there.
- **consensus can add a member** (8R-Sx0337, 1860 Senate, status ok, source tesseract+consensus): the journal prints
  "Nays—...Walker and Whaley—9." over 8 names. The text layer garbled Whaley ("vVlrnlev") and matched it to Wallace;
  Tesseract read Whaley. extract_history.consensus() takes every member ANY reading placed, so the union has both,
  9 nays, and the journal's miscount makes that "exact". A false nay for Wallace. 2,127 roll calls carry +consensus.
  v1.0 fix: in consensus, a member only one reading places, beside a member only the other reading places in the same
  list, is a disputed name: drop both, so the roll call falls to near or fails, and never "ok".
- **A list continued over a page break can take a name from the next roll call** (21R-Hx0010, 1889 House, near,
  Tesseract): the nays' two sub-columns both continue at the top of page 17, where the next vote's lists sit in the
  right column; the reading took Moody from that vote's nays. The journal prints "NAYS—59." over 57 names (OCR'd as
  559), so 58 passed as near. A false nay.
- **Tally printouts: rows shifted around a membership change (serious).** 67R-HT0025 (1981-01-14, status ok): 8 of 133
  yeas and nays wrong. tally_sheets.place_rows gives each row position of a layout to one member for the whole
  session ("every sheet prints the same members in the same order") and draws the members from the whole
  legislature regardless of dates. Ashley Smith joined on Jan 27, 1981; on this sheet the rows from SMITH, C. to
  URIBE are each credited to the member one place earlier in the roll (Ashley Smith N, Terral Smith Y, Sutton N,
  Tejeda Y, ...). A shift keeps the totals, so status is "ok". Signature in the data: votes credited to members not
  yet (or no longer) serving: 264 such Y/N votes in 255 tally roll calls (Oliveira in 92 roll calls before he took
  office on Apr 28, 1981; Lamar Smith in 75 in Feb-Jun 1981, before Dec 1981; Tip Hall in 55 in July 1977, before
  1978; Polumbo, Ashley Smith, Cavness). Each marks a sheet whose rows near that member are likely shifted.
  **v1.0 fix:** in place_rows, take only members serving on each sheet's date, and split each layout group where
  the membership changes; then re-check every tally roll call for votes outside service dates.
- **Absent, not voting** (1885 House and Senate) is coded P (present, not voting) in votes.csv; it should be A.
  parse_scans' fuzzy heading match puts "absent not voting" nearer "present not voting" than "absent not excused".
  Doesn't touch the scores (only yeas and nays are scaled).
- **Not voting** lists in running text (1876 Senate, 15R-Sx0578) are missing from votes.csv.

- **A garbled name matched to a wrong member in an "ok" roll call** (8R-Hx0292, 1860 House): the layer read "Daniels"
  (Jasper N. Daniel) as "Danids", and the one-to-one matcher gave it to Edwards, a member nowhere on the page; the
  count is untouched, so status ok. A substitution only the page can reveal. Again in 121-Hx0301 (1870 House, ok):
  the nay Stirman credited to Evans, who isn't on the page. And 20R-Hx0107 (1887 House, ok): "Newton of C." (Cherokee)
  credited to Newton of Tarrant; the county abbreviation "C." wasn't used to choose between the two Newtons. And
  16R-Hx0258 (1879 House, ok): "Smith of Harris" credited to Smith of Titus. Same-surname members are where the
  substitutions fall: the matcher should treat a printed county as binding, not a hint. Also 15R-Hx0482 (1876 House,
  ok): "McKinnion" (McKinnon, hyphenated across a line) credited to Manion.

- **An "ok" roll call can lose a vote where the journal miscounts**: 401-Hx0108 (1927 House) prints "Yeas—84." over 85 names;
  the reading lost Hagaman (OCR), which made the count match the misprint exactly. A lost vote the counts can't flag.
  Again in 23R-Hx0019 (1893 House): "NAYS—50." over 51 names, King of Ellis lost.

- **A near roll call can mix up a "Lee"/"Teel" pair** (32R-Hx0252, 1911 House, near): the page's yea Lee is recorded as
  excused, and the excused Teel as a yea (OCR read one as the other); Watson's yea is lost.

## Labels (bill, motion)

- 65R-S0502 (1977 Senate): the lists are those of Lombardino's motion to take up CSSB 70, but the roll call carries
  the bill and motion of the vote before it (Snelson's motion on SB 821). The modern-format parser's context for a
  vote can come from an earlier one. Not measured systematically; noted where seen.

## Dates

- 682-S0018 (1984 Senate, 2nd called session): the Library's file for the 11th day (06201984_11_125.pdf) runs over June 20,
  21 and 22, the Senate having recessed rather than adjourned; every roll call in it is dated June 20, though this one's page
  is headed "FRIDAY, JUNE 22, 1984". A roll call's date is its journal day's first date. v1.0: date each roll call by the
  running head of its page where the scans print one, or say on the Data page that `date` is the legislative day's.

## Journal misprints (not the data's errors)

- 42R-Sx0160 (1931 Senate): "Westbrook" printed among the nays; no Westbrook sat in the 42nd Senate (Stevenson's
  place). The data leaves the name out (status near), which is right.

## Roster spellings (not errors)

The roster spells some names differently from the journals (Plumly/Plumley, Schuetze/Schutze, Coombes/Combes,
Truitt/Truit, Donalson/Donaldson, Douglas/Douglass): the audit resolves the clearly closest spelling.

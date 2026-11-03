# TX-NOMINATE

Every Texas legislator since statehood, placed by how they vote: a Texas version of
[DW-NOMINATE](https://voteview.com), built from the record votes in the official
House and Senate Journals, 1846–2025.

**Site:** https://luke-thomas-more.github.io/texas-nominate/

The journals since 1995 (House) and 1999 (Senate) are published as clean text. Everything
earlier is read from the Legislative Reference Library's scans: the scans' own text layer
first, then Tesseract OCR where that is too garbled.

## How it's built

```
pipeline/fetch_journals.py    download the House and Senate Journals (journal API, LRL scans)
pipeline/fetch_roster.py      member roster from LRL "Texas Legislators: Past & Present"
pipeline/parse_journals.py    record votes in the journals' modern format (1995 on, and the
                              scanned 1967–94 journals): context, totals and each list's names
pipeline/tally_sheets.py      the 1973–82 House's tally sheets, read from the page images
pipeline/match_votes.py       match those names to members; check each vote against its totals
pipeline/scan_layout.py       older scans (1846–1972): words, lines and page columns, from the
                              text layer or Tesseract
pipeline/parse_scans.py       the vote lists on those pages ("Yeas—Messrs. Allen, Baker of Travis …—52")
pipeline/match_scans.py       OCR'd names to members (surnames, counties, initials, service dates)
pipeline/extract_history.py   1846–1972 end to end: re-reads pages with failing or missed roll calls,
                              combines readings, checks every roll call against its printed totals
pipeline/shared_pages.py      a page two days' files share: each roll call on it kept once, under its own day
pipeline/check_terms.py       takes out votes credited to members out of office that day (misread names)
pipeline/nominate.py          DW-NOMINATE, implemented from the published model
pipeline/stitch_start.py      starting positions for the all-history fit, from era-by-era fits
pipeline/fit_texas.py         fit House and Senate, 1846–2025, together in one shared space
pipeline/era_check.py         does the shared space fit each era as well as that era alone?
pipeline/era_holdout.py       the same, scored on held-out votes
pipeline/interpret_eras.py    evidence for the site's era-by-era guide to the axes (axes.json)
pipeline/validate_*.py        check the estimator against Voteview and Shor-McCarty, and the votes against LegiScan
pipeline/trends.py            party gaps and party-line voting by legislature, beside Congress (Voteview)
pipeline/congress_bridge.py   members who also served in Congress: their scores beside Voteview's
pipeline/blocs.py             voting blocs, found from who votes with whom, by legislature and chamber
pipeline/stability.py         does the map's orientation hold? refits from turned starts, compared era by era
pipeline/audit.py             the accuracy audit: a stratified random sample of roll calls read off the page images
pipeline/build_site.py        write docs/data/ for the site, with the downloads and their codebook
docs/                         the site (GitHub Pages)
```

Run them in that order with Python 3.11+ and `numpy scipy pandas rapidfuzz numba`, plus
Tesseract 5 and poppler (`pdftoppm`, `pdftotext`) for the scans. Raw downloads go to
`data/raw/` and intermediate files to `data/`, neither of which is committed. The published
data is in `docs/data/` (see `docs/data/downloads/`).

## Method notes

- Votes come from the journals. Each roll call is checked name by name against the totals the
  journal prints. It's kept only if every name resolves to a member and the yea and nay counts
  agree with the totals, or come within one (the journal's own count is sometimes off). In the
  scanned years a name too garbled to read may be that one missing vote, and a printed total
  that is plainly wrong (more votes than the chamber has seats, or a few off from lists that
  name every member once) gives way to the lists.
- The Senate often prints only the nays and absentees. The yeas are then everyone else
  serving that day, which must also equal the printed count. Likewise, where a journal lists
  every member under some heading, a list a few names short is completed from the members it
  lists nowhere.
- The 1973–82 House printed tally sheets instead of names. A sheet is used if its marks add up
  to the journal's totals or fall one vote short of them.
- The scaling uses Voteview's filters: roll calls with under 2.5% on the losing side are
  dropped, and so are members with fewer than 20 scaled votes.
- The model is DW-NOMINATE (normal errors, Gaussian utility) in two dimensions, with one
  shared space for both chambers from 1846 to 2025. It's estimated by alternating
  Fisher-scoring blocks followed by a joint L-BFGS polish. On dimensions 2+, the scale is
  pinned by stretching until the outermost legislator reaches the unit circle.
- The fit starts from era-by-era fits stitched into one frame (`stitch_start.py`). Started from
  one SVD of all of 1846–2025, which the modern votes dominate, it gets stuck in a poor
  solution for 1901–60. With the stitched start, the shared space predicts held-out votes in every era
  about as well as a fit of that era alone (`era_holdout.py`).
- It uses DW-NOMINATE's **constant** model: one position per member for their whole career.
  The linear-movement model let the space rotate over time (in 1995 the party split sat on
  dimension 2, by 2025 on dimension 1), because the early years are weakly anchored and the
  vote-heavy recent sessions dominate. What the axes mean still differs from era to era; the
  site's guide to each era comes from `interpret_eras.py`.
- β (signal-to-noise) trades off against the roll call spreads along a nearly flat direction,
  and the second dimension's weight sits near 1, where the map is nearly free to rotate: a refit
  that lets the weight pass 1 turns the main divide toward dimension 2. So a refit takes β and
  the weights from the fit it starts from and holds them fixed (`FIX_BETA=1 FIX_W=1`).
- `pipeline/validate_voteview.py` checks the estimator against Voteview's published U.S. House
  scores.

## Accuracy and stability

- **Audit** (`pipeline/audit.py`, readings in `audit/`): 194 roll calls drawn at random across eras, chambers and
  verification statuses were read name by name off the page images (13,588 names). Weighted to the data, about 0.04%
  of yeas and nays were wrong and 0.08% lost; nearly all errors sat in roll calls that passed the looser checks. The
  causes found, and the v1.0 fixes, are in `audit/findings.md`.
- **LegiScan** (`pipeline/validate_legiscan.py`, 2013 on; LegiScan's 2009–12 files hold no Texas votes to match): 99.75% of 3.09 million individual votes agree.
- **Stability** (`pipeline/stability.py`): refits started with the pre-1961 eras turned 25 degrees either way came
  back to within about 5 degrees of each other relative to the modern eras, so the angle between eras is set by the
  votes; the map's overall orientation can float a few degrees (the second dimension's weight is near 1).
- **Standard errors** for each member (`se1`, `se2` in `member_scores.csv`) come from the information in their own
  votes, holding the roll calls fixed: a lower bound.

## Sources

- House and Senate Journals: Texas Legislature journal sites; Legislative Reference Library of
  Texas, [scanned journals](https://lrl.texas.gov/collections/journals.cfm).
- Members: Legislative Reference Library, [Texas Legislators: Past & Present](https://lrl.texas.gov/legeLeaders/members/lrlhome.cfm).
- Method: Poole & Rosenthal, *Congress: A Political-Economic History of Roll Call Voting*
  (1997); Carroll, Lewis, Lo, Poole & Rosenthal, "Measuring Bias and Uncertainty in
  DW-NOMINATE Ideal Point Estimates via the Parametric Bootstrap," *Political Analysis* (2009).
- Validation: [Voteview](https://voteview.com); Shor & McCarty, Individual State Legislator
  Ideology Data, January 2025 update (Harvard Dataverse, doi:10.7910/DVN/SGOQ7G), used only
  for correlation checks.

## Credit and license

Built by Claude Opus 5.5 (an AI model made by Anthropic) for luke-thomas-more, who maintains it.
It's an independent project, not commissioned, reviewed or endorsed by Anthropic. Corrections are
welcome as GitHub issues.

- Code: MIT License ([LICENSE](LICENSE)).
- Data (`docs/data/`, including the downloads): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Cite as on the site's Data page, or from `CITATION.cff`. DOI (all versions): [10.5281/zenodo.23912328](https://doi.org/10.5281/zenodo.23912328).

# TX-NOMINATE

Every Texas legislator, placed by how they vote: a Texas version of
[DW-NOMINATE](https://voteview.com), built from every record vote in the official
House and Senate Journals.

**Site:** https://luke-thomas-more.github.io/texas-nominate/

The first release covers the 74th–89th Legislatures (1995–2025). Older legislatures,
back to statehood in 1846, are next. They'll be read from the Legislative Reference
Library's scanned journals.

## How it's built

```
pipeline/fetch_journals.py   download the House and Senate Journals (journal API, LRL scans)
pipeline/fetch_roster.py     member roster from LRL "Texas Legislators: Past & Present"
pipeline/parse_journals.py   pull every record vote, its context and the names in each list
pipeline/match_votes.py      match journal names to members; check each vote against its printed totals
pipeline/nominate.py         DW-NOMINATE, implemented from the published model
pipeline/fit_texas.py        fit House and Senate together in one shared space
pipeline/validate_*.py       check the estimator against Voteview and Shor-McCarty
pipeline/build_site.py       write docs/data/ for the site
docs/                        the site (GitHub Pages)
```

Run them in that order with Python 3.11+ and `numpy scipy pandas rapidfuzz numba`.
Raw downloads go to `data/raw/` and intermediate files to `data/`, neither of which is
committed. The published data is in `docs/data/` (see `docs/data/downloads/`).

## Method notes

- Votes come from the journals. Each roll call is kept only if every name resolves to a
  member and the yea and nay counts equal the totals printed in the journal.
- The Senate often prints only the nays and absentees. The yeas are then everyone else
  serving that day, which must also equal the printed count.
- The scaling uses Voteview's filters: roll calls with under 2.5% on the losing side are
  dropped, and so are members with fewer than 20 scaled votes.
- The model is standard DW-NOMINATE (normal errors, Gaussian utility, linear movement over a
  career), estimated by alternating Fisher-scoring blocks followed by a joint L-BFGS polish.
  On dimensions 2+, the scale is pinned by stretching until the outermost legislator reaches
  the unit circle.
- `pipeline/validate_voteview.py` checks the estimator against Voteview's published U.S. House
  scores.

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

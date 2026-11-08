# The data

Every verified record vote of the Texas House and Senate from 1846 to 2025: who voted yea, who voted nay, and who didn't vote, for 219,883 roll calls and 16,793,305 individual votes, with each legislator's scores from the map. As far as we know, no public machine-readable record of the Legislature's roll calls reaches back before 2009 (LegiScan's files begin then); the votes before 1995 were read for this project from the Legislative Reference Library's scans of the House and Senate Journals, and the later ones from the Legislature's own journal pages.

## Download

| File | What's in it | Size |
|---|---|---|
| [rollcalls.csv.gz](rollcalls.csv.gz) | every verified roll call: date, bill, the journal's wording, totals, how it was verified, its journal page | 8.5 MB |
| [votes.csv.gz](votes.csv.gz) | every member's vote on every one of them | 44.3 MB |
| [members.csv](members.csv) | the legislators, with their Library ids | 0.7 MB |
| [terms.csv](terms.csv) | who served when: chamber, legislature, district, party, home county | 1.4 MB |
| [member_scores.csv](member_scores.csv) | each member's place on the map, by legislature | 0.8 MB |
| [trends.csv](trends.csv) | party gaps and party-line votes by legislature | 9 KB |
| [congress_bridge.csv](congress_bridge.csv) | members who also served in Congress, with their Voteview scores | 12 KB |
| [blocs.csv](blocs.csv) | each member's voting bloc, by legislature and chamber | 0.2 MB |
| [coverage.csv](coverage.csv) | by legislature and chamber: roll calls kept, and those the scans held that failed the checks | 5 KB |
| [audit.csv](audit.csv) | the accuracy audit: every name read off the journal pages for a random sample of roll calls, and what the data has | 0.5 MB |

Data version 1.1 (2026-11-08), built 2026-11-08. The files are plain CSV (UTF-8, comma-separated, one header row); the larger two are gzipped. The same files, with this page as a README, are in the [GitHub repository](https://github.com/luke-thomas-more/texas-nominate) under `docs/data/downloads/`.

## How the votes were checked

A roll call is in the data only if every name in its lists was matched to a member of that legislature and the counts agree with the totals the journal prints. 10,003 roll calls the scans held but that couldn't be verified are left out, not guessed. Each roll call's `status` says how it passed:

| status | Meaning | Roll calls |
|---|---|---|
| `ok` | The yea and nay counts equal the printed totals exactly. | 204,731 |
| `near` | Each count is within one of its printed total. The journal's own count is sometimes off by one, and a name too garbled to read may be the missing vote (it is then left out). | 11,870 |
| `ok_digit` | A printed total was misread by the OCR by one digit, or carries a stray digit that gives more votes than the chamber has seats; accepted only when every name matched and the lists account for the whole chamber. | 820 |
| `ok_lists` | The journal misprinted a total, and every member of the chamber is listed exactly once, with no not-voting list larger than that session's usual (which would mean part of a list was read under the wrong heading): the lists are trusted. | 1,278 |
| `ok_rest` | One list came up a few names short and exactly that many members serving that day appear in no list: they are its missing names (the column `implied` gives the list). | 1,184 |

Where the Senate printed only the nays and absentees, the yeas are everyone else serving that day, and those roll calls have `implied` = Y. From 1973 to 1982 the House printed tally sheets, a grid of marks instead of names; those roll calls have ids containing `-HT`, and a sheet is used only if its marks add up to the journal's totals or fall one short. Each sheet's rows are matched to that day's members by the names printed on them, in the sheet's alphabetical order, so that a member joining or leaving mid-session can't shift the rows after them. A vote the scans credit to a member who wasn't in the chamber that day (by the Library's service dates, and appearing in few of that day's roll calls, so a garbled name read as someone who had left or not yet arrived) is taken out; the roll call is then checked again. The Library's files of consecutive days often share a page, one day ending and the next beginning on it: a roll call there is kept once, dated by which side of the new day's heading it falls on. A call of the roll for attendance isn't a vote and is left out. Every roll call links to its page in the journal (`journal_url`, `journal_page`), so any vote can be checked against the original.

## Columns

### rollcalls.csv.gz

| Column | Meaning |
|---|---|
| `rc_id` | The roll call's id: legislature, session and chamber, then its number in the journal's order (`57R-Hx0899`: 57th Legislature, regular session, House; `x` marks a vote read from the scans, `T` a tally sheet). Ids stay the same from one data version to the next: a roll call first found in a later version takes the id before it plus a letter (`14R-Hx0127a`), and an id whose roll call no longer passes the checks is retired, never reused. |
| `chamber` | `H` House, `S` Senate. |
| `leg` | The legislature (1 = 1846–47, 89 = 2025–26). |
| `session` | `R` regular, `1`, `2`, … called sessions, `A` adjourned, `P` provisional. |
| `date` | The day of the vote (YYYY-MM-DD). |
| `record` | The journal's record vote number (1973 on; blank before). |
| `bill`, `caption` | The bill or resolution the vote was on, and its caption, where the journal names one. |
| `text`, `motion`, `amendment` | The journal's words around the vote: the sentence that announces it, the motion, the amendment. Before 1995 these come from OCR and keep its misreadings. |
| `n_y`, `n_n` | Yeas and nays in the data (they agree with the printed totals as `status` describes). |
| `printed_p` | Members the journal lists as present, not voting. |
| `status`, `implied` | How the roll call was verified (above); the list completed from the roll, if any. |
| `source` | Where the vote was read: `journal` (the Legislature's journal text, 1995 on), `scan` (the scanned journals), `tally` (the 1973–82 House tally sheets). |
| `journal_url`, `journal_page` | The journal for that day (a PDF scan from the Legislative Reference Library, or the Legislature's journal page), and the page of the PDF the roll call starts on. |
| `mid1`, `mid2`, `spread1`, `spread2` | For contested votes (at least 2.5% on the losing side), the vote's position on the map: the midpoint between its yea and nay outcomes, and half the distance between them, on each axis. Blank for near-unanimous votes, which aren't scaled. |

### votes.csv.gz

`rc_id`, `member_id`, `vote`: one row per member per roll call. `vote` is `Y` yea, `N` nay, `P` present and not voting, `E` absent with leave (excused), `A` absent. Members a journal doesn't list at all on a vote have no row.

### members.csv, terms.csv

`members.csv`: `member_id` (the Legislative Reference Library's own id), `name`, `full_name`, and `lrl_url`, the member's page in its [Texas Legislators: Past & Present](https://lrl.texas.gov/legeLeaders/members/lrlhome.cfm). `terms.csv`: one row per member, chamber and legislature: `district`, `start` and `end` of service, `party` (as the Library records it; often blank before Reconstruction), `city`, `county`, `counties` (all the counties the district covered), `note` (offices such as Speaker or President pro tempore).

### member_scores.csv

One row per member, chamber and legislature in which they cast at least 20 contested votes: `member_id`, `leg`, `chamber`, `party`, `district`, `scaled_votes` (contested votes that legislature), and `dim1`, `dim2`, the member's place on the map's two axes (one position per career, so the same in every legislature they served), and `se1`, `se2`, their standard errors: from the information in the member's own votes, holding the roll calls at their estimates, so a lower bound on the uncertainty (members with few votes, or votes that rarely split near them, have wide ones). What the axes mean differs by era; see [How this works](https://luke-thomas-more.github.io/texas-nominate/#/about).

### coverage.csv

By legislature and chamber: `rollcalls_kept` (verified, in the data), `rollcalls_left_out` (read from the journals but failing the checks), and `share_kept`.

### audit.csv

The accuracy audit (below): for each roll call in the sample, every name read off the page (`printed`, as printed), the member it is (`member_id`), and the `verdict`: `ok`; `not in data` (the page has the vote, the data doesn't); `not on page` (the data has a vote the page doesn't); `data has X` (the data records another vote); `misprint on page` (the journal prints a name that is no member that day).

### trends.csv, congress_bridge.csv, blocs.csv

The numbers behind the [Trends](https://luke-thomas-more.github.io/texas-nominate/#/trends) page and the voting blocs on each map: party gaps and party-line votes by legislature and chamber; the 121 members who also served in Congress, with their Voteview DW-NOMINATE scores; and each member's voting bloc by legislature and chamber.

## How accurate

To measure the data's accuracy, a random sample of 240 roll calls of data version 1.1 (this release; the fixes it led to are listed in the repository's `audit/findings.md`), drawn from every era, both chambers and each way of verifying a roll call (the looser checks oversampled), was read name by name off the journal pages and compared with the data: 16,486 names. Two kinds of error count. A **wrong** vote is credited to the wrong member, isn't on the page, or has yea and nay swapped; a **lost** vote is on the page but missing from the data or recorded as not voting. Wrong votes mislead the scores; a lost one only leaves the member out of that roll call.

Weighting each part of the sample by its share of the data, an estimated 0.02% of yeas and nays are wrong (95% interval 0.01–0.03%) and 0.07% are lost (0.04–0.11%). Roll calls whose counts match the printed totals exactly were almost always exactly right (5 wrong and 4 lost votes in 185 of them); the errors gather in the roll calls that passed a looser check (4 wrong and 27 lost in 55). The pages read and every name are in `audit.csv`; the readings themselves are in the repository's `audit/` folder.

From 2013 on, the votes were also checked against [LegiScan](https://legiscan.com)'s independent reading of the same journals (CC BY 4.0): of 3,088,022 individual votes on 38,842 roll calls found in both, 99.75% agree.

| Era | Roll calls read | Names | Wrong | Lost |
|---|---|---|---|---|
| 1846-73 | 32 | 1,493 | 4 | 5 |
| 1874-1900 | 32 | 1,740 | 4 | 6 |
| 1901-30 | 32 | 2,113 | 0 | 9 |
| 1931-60 | 31 | 2,442 | 0 | 2 |
| 1961-72 | 32 | 2,689 | 0 | 3 |
| 1973-82 | 29 | 2,074 | 1 | 5 |
| 1983-94 | 32 | 2,456 | 0 | 1 |
| 1995-2025 | 20 | 1,479 | 0 | 0 |

## How to cite

Claude Opus 5.5 (2026). *TX-NOMINATE: Texas Legislature roll-call votes and ideal points, 1846–2025* (data version 1.1 (2026-11-08)). https://luke-thomas-more.github.io/texas-nominate/ doi:10.5281/zenodo.23912328

DOI: [10.5281/zenodo.23912328](https://doi.org/10.5281/zenodo.23912328). The repository's `CITATION.cff` gives the same in machine-readable form. Please cite the Legislative Reference Library's scanned House and Senate Journals as the original source of the votes.

## License

The data are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): free to use for any purpose, with credit (the citation above). The code is under the [MIT License](https://github.com/luke-thomas-more/texas-nominate/blob/main/LICENSE).

## Gaps and cautions

  - **Unverified roll calls are left out,** so older legislatures have fewer votes than the journals hold: where the scans are hard to read, many roll calls fail their checks. The share verified is on each legislature's map page.
  - **The House Journals for 1861–62 and 1864** survive only as newspaper reconstructions with almost no roll calls, and the 1973–82 House is covered only where its tally sheets could be read.
  - **Party** is as the Legislative Reference Library records it; for most members before Reconstruction it is not recorded.
  - **OCR text** in `text`, `motion` and `amendment` before 1995 is as read from the scans, misreadings included; the names in the lists were matched to members, the surrounding text wasn't corrected.

# The data

Every verified record vote of the Texas House and Senate from 1846 to 2025: who voted yea, who voted nay, and who didn't vote, for 220,236 roll calls and 16,837,836 individual votes, with each legislator's scores from the map. As far as we know, no public machine-readable record of the Legislature's roll calls reaches back before 2009 (LegiScan's files begin then); the votes before 1995 were read for this project from the Legislative Reference Library's scans of the House and Senate Journals, and the later ones from the Legislature's own journal pages.

## Download

| File | What's in it | Size |
|---|---|---|
| [rollcalls.csv.gz](rollcalls.csv.gz) | every verified roll call: date, bill, the journal's wording, totals, how it was verified, its journal page | 8.4 MB |
| [votes.csv.gz](votes.csv.gz) | every member's vote on every one of them | 44.4 MB |
| [members.csv](members.csv) | the legislators, with their Library ids | 0.7 MB |
| [terms.csv](terms.csv) | who served when: chamber, legislature, district, party, home county | 1.4 MB |
| [member_scores.csv](member_scores.csv) | each member's place on the map, by legislature | 0.6 MB |
| [trends.csv](trends.csv) | party gaps and party-line votes by legislature | 9 KB |
| [congress_bridge.csv](congress_bridge.csv) | members who also served in Congress, with their Voteview scores | 12 KB |
| [blocs.csv](blocs.csv) | each member's voting bloc, by legislature and chamber | 0.2 MB |

Data version 2026-10-30, built 2026-11-01. The files are plain CSV (UTF-8, comma-separated, one header row); the larger two are gzipped. The same files, with this page as a README, are in the [GitHub repository](https://github.com/luke-thomas-more/texas-nominate) under `docs/data/downloads/`.

## How the votes were checked

A roll call is in the data only if every name in its lists was matched to a member of that legislature and the counts agree with the totals the journal prints. 9,690 roll calls the scans held but that couldn't be verified are left out, not guessed. Each roll call's `status` says how it passed:

| status | Meaning | Roll calls |
|---|---|---|
| `ok` | The yea and nay counts equal the printed totals exactly. | 206,223 |
| `near` | Each count is within one of its printed total. The journal's own count is sometimes off by one, and a name too garbled to read may be the missing vote (it is then left out). | 10,994 |
| `ok_digit` | A printed total was misread by the OCR by one digit, or carries a stray digit that gives more votes than the chamber has seats; accepted only when every name matched and the lists account for the whole chamber. | 813 |
| `ok_lists` | The journal misprinted a total (a few off, with every member of the chamber listed exactly once): the lists are trusted. | 1,321 |
| `ok_rest` | One list came up a few names short and exactly that many members serving that day appear in no list: they are its missing names (the column `implied` gives the list). | 885 |

Where the Senate printed only the nays and absentees, the yeas are everyone else serving that day, and those roll calls have `implied` = Y. From 1973 to 1982 the House printed tally sheets, a grid of marks instead of names; those roll calls have ids containing `-HT`, and a sheet is used only if its marks add up to the journal's totals or fall one short. A vote the scans credit to a member who wasn't in the chamber that day (by the Library's service dates, and appearing in few of that day's roll calls, so a garbled name read as someone who had left or not yet arrived) is taken out; the roll call is then checked again. The Library's files of consecutive days often share a page, one day ending and the next beginning on it: a roll call there is kept once, dated by which side of the new day's heading it falls on. A call of the roll for attendance isn't a vote and is left out. Every roll call links to its page in the journal (`journal_url`, `journal_page`), so any vote can be checked against the original.

## Columns

### rollcalls.csv.gz

| Column | Meaning |
|---|---|
| `rc_id` | The roll call's id: legislature, session and chamber, then its number in the journal's order (`57R-Hx0899`: 57th Legislature, regular session, House; `x` marks a vote read from the scans, `T` a tally sheet). |
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

One row per member, chamber and legislature in which they cast at least 20 contested votes: `member_id`, `leg`, `chamber`, `party`, `district`, `scaled_votes` (contested votes that legislature), and `dim1`, `dim2`, the member's place on the map's two axes (one position per career, so the same in every legislature they served). What the axes mean differs by era; see [How this works](https://luke-thomas-more.github.io/texas-nominate/#/about).

### trends.csv, congress_bridge.csv, blocs.csv

The numbers behind the [Trends](https://luke-thomas-more.github.io/texas-nominate/#/trends) page and the voting blocs on each map: party gaps and party-line votes by legislature and chamber; the 121 members who also served in Congress, with their Voteview DW-NOMINATE scores; and each member's voting bloc by legislature and chamber.

## How to cite

Claude Opus 5.5 (2026). *TX-NOMINATE: Texas Legislature roll-call votes and ideal points, 1846–2025* (data version 2026-10-30). https://luke-thomas-more.github.io/texas-nominate/

The repository's `CITATION.cff` gives the same in machine-readable form. Please cite the Legislative Reference Library's scanned House and Senate Journals as the original source of the votes.

## License

The data are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): free to use for any purpose, with credit (the citation above). The code is under the [MIT License](https://github.com/luke-thomas-more/texas-nominate/blob/main/LICENSE).

## Gaps and cautions

  - **Unverified roll calls are left out,** so older legislatures have fewer votes than the journals hold: where the scans are hard to read, many roll calls fail their checks. The share verified is on each legislature's map page.
  - **The House Journals for 1861–62 and 1864** survive only as newspaper reconstructions with almost no roll calls, and the 1973–82 House is covered only where its tally sheets could be read.
  - **Party** is as the Legislative Reference Library records it; for most members before Reconstruction it is not recorded.
  - **OCR text** in `text`, `motion` and `amendment` before 1995 is as read from the scans, misreadings included; the names in the lists were matched to members, the surrounding text wasn't corrected.

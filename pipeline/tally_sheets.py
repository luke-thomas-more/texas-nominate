"""House record votes 1979-1982, from the tally sheets printed at the back of each day's journal.

The journal text gives each record vote's totals and points to its sheet ("Record No. 2 (Appendix,
March 29)"); the sheet, a computer printout headed "REC.NO. 0008 ... AYE 138 NAY 00 PNV 01 ABS 10",
gives every member's vote as "ADKISSON____A". The letter is printed at its own place along the
underscores: A (aye), then N (nay), then a third place shared by P (present, not voting) and
E (absent, excused), with the Speaker's C (in the chair) just before it. That place, measured back
from the end of the underscore rule (the field's fixed right edge), reads the vote: the letters
themselves are often too faint or broken for OCR, but where the ink sits is not in doubt. Names are
read across all of a session's sheets (place_rows). On 24 sample days of 1979 and 1981, 247 of 283
record votes come out with every member's vote adding up to the journal's totals; most of the rest
miss a faint mark or two, and fail that check. The 1973-1978 sheets, a printed grid of hand marks,
are read by
grid_day (the cell with clearly the most ink is the vote).

read_day() returns {record number: {code: [names]}} for parse_journals; match_votes resolves the
names to members like any other scanned list.
"""
import os
import re
import sys
import subprocess
import tempfile

import numpy as np

import scan_layout as sl


def ocr_pages(path, pages, psm="6", dpi=300):
    """Tesseract word boxes (PDF points) for the given pages of a day's PDF, cached like the scans'."""
    env = dict(os.environ, OMP_THREAD_LIMIT="1")

    def read(i, W, H, tmp):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-f", str(i + 1), "-l", str(i + 1), "-singlefile",
                        path, os.path.join(tmp, "p")], check=True)
        tsv = subprocess.run([sl.TESSERACT, os.path.join(tmp, "p.pgm"), "-", "--psm", psm, "-l", "eng", "tsv"],
                             capture_output=True, text=True, env=env).stdout
        return sl.tsv_words(tsv, 72.0 / dpi)
    return sl.cached_ocr(path, f".tally{psm}", pages, read, eligible=pages)


def sheet_pages(path, n):
    """The day's tally sheets: one per record vote, record numbers starting at 1 each day, printed as
    the day's appendix on its last pages (n = the day's highest record number). 1973-1978 grids:
    grid_day. Pages that don't look
    like a sheet (the heading "STATE OF TEXAS ... SPEAKER ... LEGISLATURE", garbled) are left out."""
    pages = sl.pdf_pages(path)
    keep = lambda t: re.search(r"SPEA?K|CLAY", t, re.I) and re.search(r"L\W?E\W?G\W?I|LEGIS|SESSION|\bREC\W{0,2}\s*[NHK][O0D]\b", t, re.I)
    return [i for i in range(max(0, len(pages) - n), len(pages)) if keep(" ".join(wd[4] for wd in pages[i][2]))]


def lines_of(words):
    """OCR words grouped into Tesseract's lines, top to bottom, each sorted left to right."""
    by = {}
    for wd in words:
        by.setdefault(wd[5], []).append(wd)
    return sorted((sorted(ws) for ws in by.values()), key=lambda ws: min(w[1] for w in ws))


DIGITS = str.maketrans("OoQDIl|!JSsBZz", "00001111155822")


def number(tok):
    """A printed count as OCR reads it ("o1" = 1, "00" = 0); None if it isn't one."""
    t = re.sub(r"[^\w]", "", tok).translate(DIGITS)
    return int(t) if t.isdigit() and len(t) <= 4 else None


CODE_OF = {**dict.fromkeys("A4aÀÅÄ", "A"), **dict.fromkeys("NHWMVYn", "N"), **dict.fromkeys("EF€£", "E"),
           **dict.fromkeys("Pp", "P"), **dict.fromkeys("CcG(", "C")}


def cells(line, starts):
    """One printout line's words split into its column cells, a word running across a column start
    cut there in proportion: [(column, [(x0, x1, text), ...]), ...]."""
    out = {}
    for x0, y0, x1, y1, t, _ in line:
        while t:
            k = max([j for j, c in enumerate(starts) if c - 8 <= x0] or [0])
            nxt = starts[k + 1] - 8 if k + 1 < len(starts) else None
            if nxt is not None and x1 > nxt + 4 and len(t) > 1:
                cut = max(1, min(len(t) - 1, round(len(t) * (nxt - x0) / (x1 - x0))))
                out.setdefault(k, []).append((x0, nxt, t[:cut]))
                x0, t = nxt + 8, t[cut:]
                continue
            out.setdefault(k, []).append((x0, x1, t))
            break
    return sorted(out.items())


def left_of(parts, cut):
    """A cell's words that start left of x = cut (points). A word running past it stays whole: OCR
    squeezes a run of underscores into a character or two, so a cut in proportion lands in the name."""
    return [p for p in parts if p[0] < cut]


def name_and_code(parts):
    """A printout cell's words -> (name, code letter or None, x of the code). The code is the short
    token at the end, out along the underscores; an initial hugs its surname ("CLARK, B.")."""
    toks = []
    for x0, x1, t in parts:
        for m in re.finditer(r"[^\s_—~]+", t):
            span = (x1 - x0) / max(len(t), 1)
            toks.append((x0 + m.start() * span, x0 + m.end() * span, m.group()))
    if not toks:
        return None, None, None
    code, last = None, toks[-1]
    gap = last[0] - toks[-2][1] if len(toks) > 1 else 0
    initial = len(toks) > 1 and gap < 5 and re.fullmatch(r"[A-Z]\.?", last[2]) and toks[-2][2].endswith((",", "."))
    glued = re.fullmatch(r"(.*?[A-Za-z]{2,}[.,]?|.*?[A-Z]\.)([A-Z4])", last[2])     # "B.A" = initial B. + code A
    if len(toks) > 1 and len(last[2]) <= 3 and not initial and not last[2].endswith((".", ",")):
        code, toks = last, toks[:-1]
    elif glued and (len(toks) > 1 or len(glued.group(1)) >= 3):
        span = (last[1] - last[0]) / len(last[2])
        code = (last[1] - span, last[1], glued.group(2))
        toks = toks[:-1] + [(last[0], last[1] - span, glued.group(1))]
    name = re.sub(r"[,.]$", "", " ".join(t for _, _, t in toks)).strip()
    if code is None:
        return name, None, None
    letter = next((CODE_OF[ch] for ch in reversed(code[2]) if ch in CODE_OF), "?")
    return name, letter, (code[0] + code[1]) / 2


TOTAL_KEY = [("A", r"[AÀ][VY][EF]"), ("N", r"[NHM][AÀ][YV]"), ("P", r"[PF][NHM][VY]"), ("ABS", r"A[B8][S5]")]


def total_key(tok):
    return next((k for k, pat in TOTAL_KEY if re.fullmatch(pat, tok, re.I)), None)


def column_starts(body):
    """x of the four name columns: where the words that start names line up."""
    xs = sorted(w[0] for ln in body for w in ln if re.match(r"[A-Za-z]{2}", w[4]))
    groups, cur = [], []
    for x in xs:
        if cur and x - cur[-1] > 6:
            groups.append(cur); cur = []
        cur.append(x)
    if cur:
        groups.append(cur)
    big = sorted(sorted(groups, key=len, reverse=True)[:4], key=lambda g: g[0])
    return [sorted(g)[len(g) // 2] for g in big if len(g) >= 8]


def header_totals(lines):
    """The sheet's own totals under its AYE / NAY / PNV / ABS labels."""
    totals = {}
    hdr = next((i for i, ln in enumerate(lines) if sum(1 for w in ln if total_key(w[4])) >= 3), None)
    if hdr is not None:
        labels = [(w[0], w[2], total_key(w[4])) for w in lines[hdr] if total_key(w[4])]
        for ln in lines[hdr + 1: hdr + 3]:
            for w in ln:
                n, xc = number(w[4]), (w[0] + w[2]) / 2
                for lx0, lx1, key in labels:
                    if n is not None and lx0 - 14 <= xc <= lx1 + 14 and key not in totals:
                        totals[key] = n
    return totals, hdr


def record_no(lines):
    """The record number printed under "REC.NO." ("0002 03-21-79"), or None where OCR lost it."""
    for k, ln in enumerate(lines[:14]):
        if re.search(r"\bREC\W{0,2}\s*[NHK][O0D]", " ".join(w[4] for w in ln), re.I):
            for nxt in lines[k + 1:k + 3]:
                m = re.match(r"([0-9OoQDIl]{4})\s+\S{2}\W\d{2}\W\d{2}", " ".join(w[4] for w in nxt))
                if m:
                    return number(m.group(1))
            return None
    return None


def rule_rows(ink, x0, x1, top):
    """The underscore rules in one printout column, as (first, last) pixel rows: rows holding a long
    run of ink, a rule's rows merged."""
    sub = ink[top:, x0:x1]
    best, cur = np.zeros(sub.shape[0], int), np.zeros(sub.shape[0], int)
    for c in range(sub.shape[1]):
        cur = np.where(sub[:, c], cur + 1, 0)
        best = np.maximum(best, cur)
    on = best >= 0.2 * (x1 - x0)
    out, y = [], 0
    while y < len(on):
        if on[y]:
            z = y
            while z + 1 < len(on) and on[z + 1]:
                z += 1
            out.append((top + y, top + z))
            y = z + 1
        else:
            y += 1
    return out


def page_image(path, i, dpi=200):
    """Page i rendered in gray as a numpy array (0 = black), and points -> pixels scale."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-f", str(i + 1), "-l", str(i + 1), "-singlefile",
                        path, os.path.join(tmp, "p")], check=True)
        raw = open(os.path.join(tmp, "p.pgm"), "rb").read()
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", raw)
    w, h = int(m.group(1)), int(m.group(2))
    return np.frombuffer(raw[m.end():m.end() + w * h], dtype=np.uint8).reshape(h, w), dpi / 72.0


# where each vote's letter sits, in characters back from the end of its underscore rule (1979 sample,
# medians: A -6.6, N -4.1, the Speaker's C -2.8, P and E -1.7, an asterisk after the vote -0.6)
AYE_NAY, NAY_REST, NAY_REST_CHAIR = -5.5, -3.0, -3.9
FIRST, LAST = -8.2, 0.6          # the vote's places; a name's letters and the margin lie outside
MIN_HEIGHT = 4                   # rows of ink at 200 dpi: shorter is a stray bit of the rule
# a letter is tall, or failing that (faint ink) wide: A, N, P, E and C are; the period of an initial is
# neither, and a faded N's two strokes, apart, are each still tall
LETTER_HEIGHT, MIN_WIDTH = 6, 0.45
MIN_LETTERS = 40                 # pixels of ink above a faded rule that say a member's line is there


def parse_printout(path, i, words):
    """A printout sheet -> {"record": number printed or None, "totals": {A, N, P, ABS}, "rows":
    [((column, row), name, code)]} with code "Y", "N", "P" (present not voting, or in the chair),
    "E" (absent excused) or None (no mark found). Rows come from the underscore rules in the image,
    names from OCR, and the vote from where its mark sits along the rule. None for a page that isn't
    a printout (the 1973-1978 grid)."""
    lines = lines_of(words)
    totals, hdr = header_totals(lines)
    legend = next((k for k, ln in enumerate(lines) if re.search(r"AYE\s*VOTE|NAY\s*VOTE|IN\s*CHAIR", " ".join(w[4] for w in ln), re.I)), None)
    if legend is None:
        return None
    body = lines[legend + 1:]
    starts = column_starts(body)
    if len(starts) < 4 or not body:
        return None
    img, s = page_image(path, i)
    ink = img < 140
    pitch = starts[1] - starts[0]
    # the printout is monospaced: places along a line are counted in characters
    widths = [(w[2] - w[0]) / len(w[4]) for ln in body for w in ln if re.fullmatch(r"[A-Z]{4,}", w[4])]
    cw = (sorted(widths)[len(widths) // 2] if widths else pitch / 19) * s
    words_at = {}                                # (column, bottom px) -> the line's words in that column
    for ln in body:
        for k, parts in cells(ln, starts):
            words_at[(k, max(w[3] for w in ln) * s)] = parts
    top = int(min(w[1] for w in body[0]) * s) - 6
    spans = []
    for k, cs in enumerate(starts):
        x0 = int((cs - 1) * s)
        x1 = int(((starts[k + 1] if k + 1 < len(starts) else cs + pitch) - 3) * s)
        spans.append((x0, x1, rule_rows(ink, x0, x1, top)))
    # every printed line runs across all four columns, so the rows are the rules found in any column; a
    # rule that faded in one column (late 1979: the fourth column lost a few a sheet) is taken there at
    # the same height wherever that column has letters above it, its end where the column's rules end
    # (a skewed page sets a line a few pixels lower in one column than another, by more at one end: the
    # rows are the column with the most rules, gaps in it filled from the others after each is moved by
    # its median offset, and a column's rules are matched to them one to one within 0.4 of a line)
    best_rr = max((rr for _, _, rr in spans), key=len)
    ref = np.array([a for a, _ in best_rr])
    pitch = float(np.median(np.diff(ref))) if len(ref) > 2 else 19.0
    shift = []
    for _, _, rr in spans:
        d = [a - ref[np.abs(ref - a).argmin()] for a, _ in rr] if len(ref) else []
        d = [v for v in d if abs(v) <= 0.5 * pitch]
        shift.append(int(np.median(d)) if d else 0)
    grid = list(ref)
    for k, (_, _, rr) in enumerate(spans):
        for a, _ in rr:
            if all(abs(a - shift[k] - g) > 0.5 * pitch for g in grid):
                grid.append(a - shift[k])
    grid.sort()
    rows = []
    for k, (x0, x1, rr) in enumerate(spans):
        ends = [np.flatnonzero(ink[a:b + 1, x0:x1].any(axis=0))[-1] for a, b in rr]
        end = int(np.median(ends)) if ends else None
        col, used = [], set()
        for y in (g + shift[k] for g in grid):
            own = sorted((abs(a - y), j) for j, (a, b) in enumerate(rr) if abs(a - y) <= 0.4 * pitch and j not in used)
            if own:
                used.add(own[0][1])
                col.append(rr[own[0][1]])
            elif end is not None and ink[max(0, int(y) - 16):int(y) - 2, x0:x1].sum() >= MIN_LETTERS:
                col.append((int(y), int(y)))
        # each OCR line names one row, its nearest: a row whose line OCR lost gets no name, not a neighbour's
        named = {}
        for (kk, by), v in words_at.items():
            near = [(abs(by - yr), n_r) for n_r, (yr, _) in enumerate(col) if kk == k and -8 <= yr - by <= 14]
            if near:
                d, n_r = min(near)
                if n_r not in named or d < named[n_r][0]:
                    named[n_r] = (d, v)
        for n_r, (yr, yb) in enumerate(col):
            parts = named[n_r][1] if n_r in named else []
            on = np.flatnonzero(ink[yr:yb + 1, x0:x1].any(axis=0))
            if not len(on) or on[-1] < end - 2 * cw:
                on = np.array([end])                       # the rule faded here: the column's usual end
            above = col[n_r - 1][1] + 1 if n_r and yr - col[n_r - 1][1] < 26 else yr - 19
            band = ink[above + 2:yr - 1, x0:x1]          # the line's letters, clear of both rules
            xs = np.flatnonzero(band.any(axis=0))
            groups = np.split(xs, np.flatnonzero(np.diff(xs) > 0.45 * cw) + 1) if len(xs) else []
            # a vote letter is letter-sized (the period of an initial isn't); a long name's last letters can
            # reach the aye's place ("STANISWALIS", and in 1981 "THOMPSON,G.A" with no underscore between),
            # but the vote always comes after the name: the rightmost letter in the aye and nay places
            tall = lambda g: band[:, g[0]:g[-1] + 1].any(axis=1).sum()
            marks = [(((g[0] + g[-1]) / 2 - on[-1]) / cw, g[0]) for g in groups
                     if tall(g) >= LETTER_HEIGHT or (tall(g) >= MIN_HEIGHT and g[-1] - g[0] + 1 >= MIN_WIDTH * cw)]
            marks = [m for m in marks if FIRST <= m[0] <= LAST]
            chair = (k == 0 and n_r == 0) or bool(re.search(r"SPEAK", " ".join(p[2] for p in parts), re.I))
            vote = [m for m in marks if m[0] < (NAY_REST_CHAIR if chair else NAY_REST)][-1:]
            # the name is what OCR read left of the vote's mark (a letter close behind "THOMPSON,G." would
            # pass for another initial)
            mark = (vote or marks)[0][1] if marks else None
            name, letter, _ = name_and_code(left_of(parts, (x0 + mark) / s - 0.2 * cw / s) if mark is not None else parts)
            if name and not re.search(r"[A-Za-z]{2}", name):
                name = None
            elif name:      # "CLARK.B", "CLARK. J.L": the comma before a member's initials read as a period
                name = re.sub(r"^([A-Z]{3,})\.(?=\s*(?:[A-Z]\.?\s*){1,3}$)", r"\1,", name)
            if vote:
                code = "Y" if vote[0][0] < AYE_NAY else "N"
            elif marks:
                ocr = name_and_code(parts)[1]
                code = "P" if chair or ocr in ("P", "C") else "E"
            else:
                code = None
            rows.append(((k, n_r), name, code))
    return {"record": record_no(lines), "totals": totals, "rows": rows}


# ---- 1973-1978: a printed grid, four blocks of NAME | Y | N | P, one member a row in a fixed order
GRID_DOMINANT = 0.6  # a second mark in another cell at least this size (vs the largest) makes the row unreadable


def skew(img):
    """The page's skew as a slope (pixels down per pixel across): the shear that makes the dark
    pixels' row profile sharpest, i.e. lines up the grid's rules."""
    yy, xx = np.nonzero(img < 140)
    if len(yy) < 1000:
        return 0.0
    best = (0.0, -1.0)
    for t in np.arange(-0.03, 0.0301, 0.001):
        prof = np.bincount(np.clip(yy - np.round(xx * t).astype(int), 0, None))
        sharp = float((prof.astype(np.float64) ** 2).sum())
        if sharp > best[1]:
            best = (float(t), sharp)
    return best[0]


def deskew(img, t):
    """img sheared straight for slope t: rules that ran down by t per pixel across run level, and
    verticals (tilted the other way by a rotation) stand straight."""
    if abs(t) < 0.0005:
        return img
    H, W = img.shape
    ys = np.clip(np.arange(H)[:, None] + np.round(np.arange(W)[None, :] * t).astype(int), 0, H - 1)
    out = np.take_along_axis(img, ys, axis=0)
    xs = np.clip(np.arange(W)[None, :] - np.round(np.arange(H)[:, None] * t).astype(int), 0, W - 1)
    return np.take_along_axis(out, xs, axis=1)


def straight_page(path, i, dpi=200, t=None):
    """Page i rendered in gray and deskewed, and the slope used (estimated at 200 dpi if not given)."""
    img, _ = page_image(path, i, dpi)
    if t is None:
        t = skew(img)
    return deskew(img, t), t


def grid_geometry(img):
    """The grid's rules: {"rows": y of the rule under the header and under each member row,
    "blocks": [(x name|Y, x Y|N, x N|P, x P|next), ...], "x0", "x1"}, or None if the page has no grid.
    The table's sides are its two long vertical rules; its row rules are the lines dark across most of
    the table, the member rows the longest run of them at an even pitch; a block's vote columns are
    four vertical lines 12-26 px apart."""
    dark = img < 140
    H, W = dark.shape
    # rules: pixel rows dark across a good third of the page (a rule has small breaks in the print,
    # so count its ink rather than its longest run)
    ys = np.where(dark.sum(axis=1) > 0.3 * W)[0]
    if not len(ys):
        return None
    groups = [g for g in np.split(ys, np.where(np.diff(ys) > 2)[0] + 1) if len(g)]
    hy = [int(g.mean()) for g in groups]
    gaps, best, k = np.diff(hy), (0, 0), 0
    while k < len(gaps):
        j = k
        while j < len(gaps) and 14 <= gaps[j] <= 24:
            j += 1
        if j - k > best[1] - best[0]:
            best = (k, j)
        k = j + 1
    rows = hy[best[0]:best[1] + 1]
    if len(rows) < 21:
        return None
    # the table's bottom border is heavier, so its middle sits low: take its top edge
    if best[1] < len(gaps) and gaps[best[1]] <= 1.4 * np.median(np.diff(rows)):
        rows.append(int(groups[best[1] + 1].min()))
    # the table's sides: where most of the member rules are dark
    on = np.array([dark[g].any(axis=0) for g in groups[best[0]:best[1] + 1]]).mean(axis=0)
    xs = np.where(on > 0.6)[0]
    x0, x1 = int(xs.min()), int(xs.max())
    # the four blocks are equal, each ending in its three vote cells; marks in the cells cast false
    # "lines", so each line is placed by that pattern and then moved onto a real line within reach
    cf = (img[rows[0]:rows[-1], :] < 140).mean(axis=0)
    xs = np.where(cf > 0.3)[0]
    vx = np.array([int(g.mean()) for g in np.split(xs, np.where(np.diff(xs) > 2)[0] + 1) if len(g)])
    steps = [v for v in np.diff(vx) if 16 <= v <= 26]
    if len(vx) < 8 or len(steps) < 3:
        return None
    c, bw = float(np.median(steps)), (x1 - x0) / 4.0

    def snap(x, reach):
        near = vx[np.abs(vx - x) <= reach]
        return int(near[np.argmin(np.abs(near - x))]) if len(near) else int(round(x))
    blocks = []
    for b in range(4):
        xe = x0 + (b + 1) * bw if b < 3 else x1
        xe = snap(xe, 12)
        blocks.append(tuple(snap(xe - k * c, 4) for k in (3, 2, 1)) + (xe,))
    return {"rows": rows, "blocks": blocks, "x0": x0, "x1": x1}


def is_x(yy, xx):
    """Whether a mark's pixels (relative to its box) make an X, drawn corner to corner (absent-excused):
    ink in all four corners of its box and in the middle. A dot is too small; a check misses its top
    left corner."""
    if len(yy) < 40:
        return False
    h, w = yy.max() + 1, xx.max() + 1
    if h < 10 or w < 10:
        return False
    def has(ya, xa):
        return (((yy * 3 // h) == ya) & ((xx * 3 // w) == xa)).sum() >= 3
    return has(0, 0) and has(0, 2) and has(2, 0) and has(2, 2) and has(1, 1)


def is_c(yy, xx):
    """Whether a mark is a C (the member in the chair, so not voting): ink along its left side and
    across its top and bottom, its middle right open."""
    if len(yy) < 40:
        return False
    h, w = yy.max() + 1, xx.max() + 1
    if h < 10 or w < 8:
        return False
    def has(ya, xa):
        return (((yy * 3 // h) == ya) & ((xx * 3 // w) == xa)).sum() >= 3
    return has(1, 0) and not has(1, 2) and (has(0, 1) or has(0, 2)) and (has(2, 1) or has(2, 2))


def parse_grid(path, i, words, img=None):
    """{"rows": [((block, row), name, code)], "counts": {code: n}} from a grid sheet, or None.
    code is Y, N, P, E (X: absent-excused), "?" (two cells marked) or None (blank: absent). Names
    are OCR words (points) inside each row's name cell; place_rows reads them across the session."""
    if img is None:
        img, _ = straight_page(path, i)
    G = grid_geometry(img)
    if not G:
        return None
    k = 200 / 72.0
    dark = img < 160
    rows, blocks = G["rows"], G["blocks"]
    names = {}
    for x0, y0, x1, y1, t, _ in words:
        cx, cy = (x0 + x1) / 2 * k, (y0 + y1) / 2 * k
        r = int(np.searchsorted(rows, cy)) - 1
        if not 0 <= r < len(rows) - 1:
            continue
        for b, (xn, _, _, _) in enumerate(blocks):
            left = blocks[b - 1][3] if b else G["x0"]
            if left < cx < xn:
                names.setdefault((b, r), []).append((x0, t))
    # the marks alone: the printed lines are 2-3 px thick, a dot or a pen stroke 5 px or more, so an
    # opening 4 px wide (drops the vertical lines) then 4 px tall (the rules) keeps just the marks
    from scipy import ndimage
    clean = ndimage.binary_opening(dark, structure=np.ones((1, 4), bool))
    clean = ndimage.binary_opening(clean, structure=np.ones((4, 1), bool))
    # the marks, as pieces of ink with a centre and a size (scraps of a thick rule, short and wide, aren't)
    pieces = []
    for b, (xn, xy, xnp, xe) in enumerate(blocks):
        lab, n = ndimage.label(clean[rows[0] - 2:rows[-1] + 4, xn - 4:xe + 6])
        for k, sl in enumerate(ndimage.find_objects(lab), 1):
            yy, xx = np.nonzero(lab[sl] == k)
            h, w = yy.max() - yy.min() + 1, xx.max() - xx.min() + 1
            if len(yy) < 12 or h < 5 or w < 4 or (h <= 6 and w >= 2.5 * h):
                continue
            gy, gx = yy + sl[0].start + rows[0] - 2, xx + sl[1].start + xn - 4
            foot = gy >= gy.max() - 0.3 * h
            pieces.append((b, len(yy), float(gy.mean()), float(gx.mean()) - xn, gy - gy.min(), gx - gx.min(),
                           float(gy.max()), float(gx[foot].mean()) - xn))
    # likewise a sheet's dots can sit a few pixels off its rows: centre them in the rows (positions in
    # rows, u = row + fraction of the way down it; the rows aren't evenly spaced to the pixel)
    def u_of(cy):
        r = min(max(int(np.searchsorted(rows, cy)) - 1, 0), len(rows) - 2)
        return r + (cy - rows[r]) / (rows[r + 1] - rows[r])
    # A sheet marked in pen throughout (1973: checks, not printed dots) has no printing offset; a check
    # was drawn with its foot in the member's row and its stroke rising into the row above.
    pitch = float(np.median(np.diff(rows)))
    dotted = sum(1 for p in pieces if p[1] < 90) >= 40
    ph = np.array([u_of(p[2]) % 1 for p in pieces if p[1] < 90]) * 2 * np.pi
    lift = 0.0
    if dotted and len(ph) >= 10:
        lift = (np.angle(np.exp(1j * ph).mean()) / (2 * np.pi)) % 1 - 0.5
    marks = {}
    for b, a, cy, cx, yy, xx, fy, fx in pieces:
        if not dotted and a >= 60:
            cy, cx = fy - 0.25 * pitch, fx
        r = int(np.floor(u_of(cy) - lift))
        if 0 <= r < len(rows) - 1:
            marks.setdefault((b, r), []).append((a, cx, yy, xx))
    # the dots were printed onto the ruled form, sometimes up to half a cell off its lines: the sheet's
    # offset is the shift that best centres its dots in the cells
    c = float(np.median([blk[3] - blk[0] for blk in blocks])) / 3
    xs = np.array([cx for ms in marks.values() for a, cx, _, _ in ms if a < 90])
    shift = 0.0
    if dotted and len(xs) >= 10:
        cand = np.arange(-c / 2, c / 2 + 0.01, 0.5)
        cost = [np.minimum(np.abs(((xs - d) % c) - c / 2), c).sum() for d in cand]
        shift = float(cand[int(np.argmin(cost))])
    if os.environ.get("GRID_DEBUG"):
        print("grid offsets: dotted", dotted, "lift", round(float(lift), 3), "shift", shift, "cell", round(c, 1), file=sys.stderr)
    out = []
    for b in range(len(blocks)):
        for r in range(len(rows) - 1):
            ms = sorted(marks.get((b, r), []), key=lambda m: -m[0])
            code = None
            if ms:
                area, cx, yy, xx = ms[0]
                col = int((cx - shift) // c)
                if not 0 <= col <= 2:
                    code = "?"
                elif len(ms) > 1 and ms[1][0] > GRID_DOMINANT * area and int((ms[1][1] - shift) // c) != col:
                    code = "?"
                else:
                    # the Speaker, first on the sheet, is usually in the chair: a C (a check elsewhere
                    # can pass for one)
                    code = "E" if is_x(yy, xx) else "P" if (b, r) == (0, 0) and is_c(yy, xx) else "YNP"[col]
            name = re.sub(r"[^\w,.' -]", "", " ".join(t for _, t in sorted(names.get((b, r), [])))).strip(" .,'-")
            out.append(((b, r), name or None, code))
    counts = {}
    for _, _, code in out:
        counts[code] = counts.get(code, 0) + 1
    return {"rows": out, "counts": counts, "record": None}


def grid_names(path, pages):
    """OCR word boxes (points) for a grid sheet's names, read one block's name column at a time at
    300 dpi with the row rules whited out (on the whole page Tesseract's layout analysis tangles the
    names with the grid). Cached like the other reads."""
    env = dict(os.environ, OMP_THREAD_LIMIT="1")

    def read(i, W, H, tmp):
        img, t = straight_page(path, i)
        G = grid_geometry(img)
        if not G:
            return []
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-f", str(i + 1), "-l", str(i + 1), "-singlefile",
                        path, os.path.join(tmp, "p")], check=True)
        raw = open(os.path.join(tmp, "p.pgm"), "rb").read()
        m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", raw)
        w, h = int(m.group(1)), int(m.group(2))
        big = deskew(np.frombuffer(raw[m.end():m.end() + w * h], dtype=np.uint8).reshape(h, w), t).copy()
        f = 1.5                                   # 300 / 200 dpi
        for y in G["rows"]:
            big[max(0, int(y * f) - 3):int(y * f) + 4, :] = 255
        words = []
        for b, (xn, _, _, _) in enumerate(G["blocks"]):
            left = G["blocks"][b - 1][3] if b else G["x0"]
            xa, xb, ya, yb = int(left * f) + 6, int(xn * f) - 4, int(G["rows"][0] * f), int(G["rows"][-1] * f) + 4
            crop = np.ascontiguousarray(big[ya:yb, xa:xb])
            fn = os.path.join(tmp, f"b{b}.pgm")
            with open(fn, "wb") as out:
                out.write(b"P5 %d %d 255\n" % (crop.shape[1], crop.shape[0]) + crop.tobytes())
            tsv = subprocess.run([sl.TESSERACT, fn, "-", "--psm", "6", "-l", "eng", "tsv"],
                                 capture_output=True, text=True, env=env).stdout
            words += sl.tsv_words(tsv, 72.0 / 300, xa * 72.0 / 300, ya * 72.0 / 300, block_offset=b * 1000)
        return words
    return sl.cached_ocr(path, ".grid6", pages, read, eligible=pages)


def grid_day(path, n, totals=None):
    """{record: rows} from a day's grid sheets (1973-1978). The sheets carry no machine-readable
    record number (it's handwritten), so with one sheet per record they go in page order; otherwise
    they're lined up in order against the journal's totals ({record: (yeas, nays)}): each record takes
    the next sheet whose yea and nay marks agree with its totals, or none."""
    pages = sl.pdf_pages(path)
    cand = range(max(0, len(pages) - n - 4), len(pages))
    sheets = []
    for i in cand:
        img, _ = straight_page(path, i)
        if grid_geometry(img) is None:
            continue
        sheets.append((i, img))
    if not sheets:
        return {}
    ocr = grid_names(path, [i for i, _ in sheets])
    parsed = [(i, sh) for i, img in sheets for sh in [parse_grid(path, i, ocr[i][2], img)] if sh]
    if len(parsed) == n:
        return {k + 1: sh["rows"] for k, (i, sh) in enumerate(parsed)}
    out, j = {}, 0
    for rec in sorted(totals or {}):
        y, nn = totals[rec]
        for jj in range(j, len(parsed)):
            c = parsed[jj][1]["counts"]
            if c.get("Y", 0) == y and c.get("N", 0) == nn:
                out[rec], j = parsed[jj][1]["rows"], jj + 1
                break
    return out


def read_day(path, n, totals=None):
    """{record number: [((column, row), name, code)]} from a day's printout sheets; n = the day's
    highest record number. With a sheet for every record, they go in page order; otherwise a sheet's
    record is the number printed on it, or where OCR lost that, counted from the nearest sheet that
    has one, and a sheet that can't be placed is left out. {} for a day whose sheets aren't
    printouts (no "AYE VOTE" legend)."""
    pages = sheet_pages(path, n)
    layer = sl.pdf_pages(path)
    if not pages:
        return grid_day(path, n, totals)
    if not any(re.search(r"AYE\s*VOTE|NAY\s*VOTE|IN\s*CHAIR", " ".join(w[4] for w in layer[i][2]), re.I)
                            for i in pages):
        return grid_day(path, n, totals)
    ocr = ocr_pages(path, pages)
    sheets = {i: sh for i in pages for sh in [parse_printout(path, i, ocr[i][2])] if sh}
    if len(sheets) == n:        # every sheet read: one per record, in order (OCR misreads the numbers: "9003")
        known = {i: k + 1 for k, i in enumerate(sorted(sheets))}
    else:
        known = {i: sh["record"] for i, sh in sheets.items() if sh["record"] and 1 <= sh["record"] <= n}
    out = {}
    for i, sh in sorted(sheets.items()):
        rec = known.get(i)
        if rec is None:
            before = [(j, r) for j, r in sorted(known.items()) if j < i][-1:]
            after = [(j, r) for j, r in sorted(known.items()) if j > i][:1]
            guess = {r + (i - j) for j, r in before} | {r - (j - i) for j, r in after}
            rec = guess.pop() if len(guess) == 1 else None
        if rec and 1 <= rec <= n and rec not in out:
            out[rec] = sh["rows"]
    return out


MIN_SCORE = 60      # how well a row position's names must match its member, on average over the sheets
MIN_GROUP = 5       # sheets with the same layout that settle its positions among themselves
GAP = 10            # an alignment's cost for skipping a row or a member (a rule missed or doubled)
ALIGNED_SCORE = 40  # a name read on an odd sheet, held in line by the rows around it, needs only this much


def resolve(sheets, roster, chair=()):
    """Members' votes on a session's sheets: {key: [((column, row), name, code)]} -> {key: {member_id: code}}."""
    placed = place_rows(sheets, roster, chair)
    return {key: {placed[key][pos][0]: code for pos, _, code in rows if code and pos in placed[key]}
            for key, rows in sheets.items()}


def place_rows(sheets, roster, chair=()):
    """The member at each row position of a session's sheets: {key: {(column, row): (member_id, score)}}.
    Every sheet prints the same members in the same order, so a row position is read on all the
    sheets with its layout (rows per column) and goes to the member its names match best on average,
    one member to a position (an assignment problem): a name OCR garbled on one sheet ("nrospeaxen",
    "SS s«SEACD") is read right on others. A sheet whose rules came out differently (one missed or
    doubled, so its rows per column differ) has too few companions for that: each of its columns is
    aligned instead, in order, to the members of that column in the commonest layout, by its own names.
    chair: the Speaker(s) of the session."""
    import collections
    from scipy.optimize import linear_sum_assignment
    from match_scans import SessionMatcher, parse_raw
    from match_votes import initial_seqs
    sm = SessionMatcher(roster, chair)
    if not sm.members or not sheets:
        return {}
    memo = {}

    def score(name):
        """The matcher's scores, less 25 for members none of whose given names start with the printed
        first initial: the printout tells same-surname members apart by initials ("CLARK,B." Bill,
        "CLARK,J.L." Jerry L.), which the matcher alone lets pass when the first letter differs."""
        if name not in memo:
            S = np.clip(sm.best(name), 0, 100)
            sn, ini, _, _ = parse_raw(name)
            if ini and sn:
                S = S - 25 * np.array([not any(q.startswith(ini[0]) for q in initial_seqs(sm.terms[m]["toks"], sm.terms[m]["nicks"], sn))
                                       for m in sm.members])
            memo[name] = S
        return memo[name]
    def align(rows, ref):
        """One sheet's rows -> members, column by column: its names lined up in order against the
        reference layout's members of the same column (a row or a member may be skipped, at a cost).
        A row whose name OCR lost keeps its place in line between rows that were read."""
        placed = {}
        by_col = collections.defaultdict(list)
        for pos, name, _ in rows:
            by_col[pos[0]].append((pos, name))
        for k, rs in by_col.items():
            rs.sort(key=lambda t: t[0][1])
            col = [m for r, m in sorted((pos[1], m) for pos, (m, _) in ref.items() if pos[0] == k)]
            if not col:
                continue
            idx = [sm.idx[m] for m in col]
            G = np.array([score(name)[idx] - ALIGNED_SCORE if name else np.full(len(col), 1.0) for _, name in rs])
            D = np.zeros((len(rs) + 1, len(col) + 1))
            D[:, 0] = -GAP * np.arange(len(rs) + 1)
            D[0, :] = -GAP * np.arange(len(col) + 1)
            for i in range(1, len(rs) + 1):
                for j in range(1, len(col) + 1):
                    D[i, j] = max(D[i - 1, j - 1] + G[i - 1, j - 1], D[i - 1, j] - GAP, D[i, j - 1] - GAP)
            i, j = len(rs), len(col)
            while i and j:
                if D[i, j] == D[i - 1, j - 1] + G[i - 1, j - 1]:
                    if G[i - 1, j - 1] >= 0:
                        placed[rs[i - 1][0]] = (col[j - 1], G[i - 1, j - 1] + ALIGNED_SCORE)
                    i, j = i - 1, j - 1
                elif D[i, j] == D[i - 1, j] - GAP:
                    i -= 1
                else:
                    j -= 1
        return placed

    groups = collections.defaultdict(list)
    for key, rows in sheets.items():
        groups[tuple(sorted(collections.Counter(pos[0] for pos, _, _ in rows).items()))].append(key)
    out, settled = {}, {}
    for layout, keys in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(keys) < MIN_GROUP and settled:
            continue
        positions = sorted({pos for key in keys for pos, _, _ in sheets[key]})
        at = {p: j for j, p in enumerate(positions)}
        S, seen = np.zeros((len(positions), len(sm.members))), np.zeros(len(positions))
        for key in keys:
            for pos, name, _ in sheets[key]:
                if name:
                    S[at[pos]] += score(name)
                    seen[at[pos]] += 1
        S /= np.maximum(seen, 1)[:, None]
        rows, cols = linear_sum_assignment(-S)
        settled[layout] = {positions[r]: (sm.members[c], S[r, c]) for r, c in zip(rows, cols) if S[r, c] >= MIN_SCORE}
        for key in keys:
            out[key] = settled[layout]
    ref = settled[max(settled, key=lambda layout: len(groups[layout]))]
    for layout, keys in groups.items():
        if layout not in settled:
            for key in keys:
                out[key] = align(sheets[key], ref)
    return out

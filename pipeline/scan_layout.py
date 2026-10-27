"""Reading order for scanned journal pages, from the OCR text layer's word boxes.

pdftotext's own reading order interleaves the two page columns and the sub-columns of vote
lists, so the layout is rebuilt here:
  words -> lines (by vertical position)
        -> cells (split where the gap between words is much wider than a space)
        -> page columns (split at the gutter, found as the emptiest vertical band near the middle)
Reading order is: full-width lines in place, otherwise left column top to bottom, then right.
Each output line keeps its cells, so a line of vote-list names stays separable into names.
"""
import gzip, html, json, os, re, statistics, subprocess, tempfile


def pdf_pages(path):
    """[(width, height, [(x0, y0, x1, y1, text), ...]), ...] from the PDF's text layer."""
    out = subprocess.run(["pdftotext", "-bbox", path, "-"], capture_output=True, text=True).stdout
    pages = []
    for m in re.finditer(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', out, re.S):
        words = [(float(a), float(b), float(c), float(d), html.unescape(t))
                 for a, b, c, d, t in re.findall(
                     r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', m.group(3))]
        pages.append((float(m.group(1)), float(m.group(2)), words))
    return pages


TESSERACT = os.path.expanduser("~/.local/micromamba/envs/ocr/bin/tesseract")
OCR_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "interim", "ocr")


VOTE_MARK = re.compile(r"\by[eo][ao][s5]\b|\bn[ao]y[s5]\b|messrs|following\s+vote|yeas\s+and\s+nays", re.I)


YEA_HEADING = re.compile(r"\b(?:Y[a-z]{1,2}[s8z5]|Yea[abe]|Ayes)\s*[-—–=~:.,]", re.I)     # "Yeas—25", "YEAS.", "Ycas-", "Yeaa-"


def vote_marks(path):
    """Pages whose text layer mentions a vote: {page: how many yea headings it shows}."""
    marks = {}
    for i, (_, _, ws) in enumerate(pdf_pages(path)):
        text = " ".join(w[4] for w in ws)
        if VOTE_MARK.search(text):
            marks[i] = len(YEA_HEADING.findall(text))
    return marks


def vote_pages(path):
    """Pages whose text layer mentions a vote, plus the page after (lists run on)."""
    hits = set(vote_marks(path))
    return hits | {i + 1 for i in hits}


def drop_rules(words):
    """Tesseract sometimes reads a column rule as a "line" of junk stacked down the gutter
    ("RRR", "AERA", "SESE", ...), which hides the gutter. Such a line is far taller than wide."""
    if not words:
        return words
    h = statistics.median(w[3] - w[1] for w in words)
    lines = {}
    for w in words:
        lines.setdefault(w[5], []).append(w)
    junk = {k for k, ws in lines.items()
            if max(w[3] for w in ws) - min(w[1] for w in ws) > max(4 * h, 2 * (max(w[2] for w in ws) - min(w[0] for w in ws)))}
    return [w for w in words if w[5] not in junk]


def cached_ocr(path, suffix, pages, read_page, eligible=None):
    """One Tesseract read of a PDF, cached per PDF as (width, height, words) per page, words null for
    a page not read yet. Reads the pages among `pages` (default: all) that the cache lacks, with
    read_page(i, width, height, tmp) on `eligible` pages (default: the vote pages; others are empty),
    and returns every page, those outside `pages` empty, so a result doesn't depend on what an
    earlier run happened to read."""
    os.makedirs(OCR_CACHE, exist_ok=True)
    key = re.sub(r"[^\w.-]", "_", os.path.relpath(os.path.abspath(path), "/")) + suffix
    cache = os.path.join(OCR_CACHE, key + ".json.gz")
    if os.path.exists(cache):
        with gzip.open(cache, "rt") as f:
            stored = json.load(f)
    else:
        stored = [[w, h, None] for w, h, _ in pdf_pages(path)]
    want = set(range(len(stored))) if pages is None else {i for i in pages if 0 <= i < len(stored)}
    missing = [i for i in sorted(want) if stored[i][2] is None]
    if missing:
        votes = vote_pages(path) if eligible is None else set(eligible)
        with tempfile.TemporaryDirectory() as tmp:
            for i in missing:
                stored[i][2] = read_page(i, stored[i][0], stored[i][1], tmp) if i in votes else []
        tmp_cache = f"{cache}.{os.getpid()}.tmp"       # atomic: another process may read the cache meanwhile
        with gzip.open(tmp_cache, "wt") as f:
            json.dump(stored, f)
        os.replace(tmp_cache, cache)
    return [(w, h, drop_rules([tuple(x) for x in ws]) if i in want and ws else []) for i, (w, h, ws) in enumerate(stored)]


def tesseract_pages(path, dpi=300, psm=None, pages=None):
    """Re-OCR the vote pages with Tesseract (much better than the scans' own text layer on old
    print); other pages come back empty. pages: only these (default: every vote page). Words are
    in PDF points, like pdf_pages(). Cached per PDF."""
    psm = psm or os.environ.get("PSM", "3")   # automatic page segmentation finds the columns itself
    env = dict(os.environ, OMP_THREAD_LIMIT="1")

    def read(i, W, H, tmp):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-f", str(i + 1), "-l", str(i + 1), "-singlefile",
                        path, os.path.join(tmp, "p")], check=True)
        tsv = subprocess.run([TESSERACT, os.path.join(tmp, "p.pgm"), "-", "--psm", psm, "-l", "eng", "tsv"],
                             capture_output=True, text=True, env=env).stdout
        return tsv_words(tsv, 72.0 / dpi)
    return cached_ocr(path, "" if psm == "4" else f".psm{psm}", pages, read)


def tsv_words(tsv, scale, dx=0.0, dy=0.0, block_offset=0):
    """Word boxes in PDF points from Tesseract's TSV output (rendered at 72/scale dpi, cropped at dx, dy)."""
    words = []
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) == 12 and f[0] == "5" and f[11].strip():
            x, y, w, h = (int(v) for v in f[6:10])
            line_id = (int(f[2]) + block_offset) * 100000 + int(f[3]) * 1000 + int(f[4])   # Tesseract's own block/par/line
            words.append((dx + x * scale, dy + y * scale, dx + (x + w) * scale, dy + (y + h) * scale, f[11], line_id))
    return words


def column_pages(path, dpi=300, pages=None):
    """Tesseract once more, one page column at a time as a plain block of text (psm 6). On pages
    of vote lists the automatic layout analysis sometimes drops or scrambles a block of names,
    while a single column read row by row comes out nearly clean ("Barrett of Fannin. Looney.").
    The gutter comes from the text layer (single-column pages are read whole). Words are in PDF
    points, like pdf_pages(); pages that don't mention a vote, or aren't among `pages`, come back
    empty. Cached per PDF."""
    s = 72.0 / dpi
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    layout = {}

    def read(i, W, H, tmp):
        if not layout:
            layer = pdf_pages(path)
            singles = [single_column(p) for p in layer]
            decided = [v for v in singles if v is not None]
            layout.update(layer=layer, singles=singles, default=sum(decided) > len(decided) / 2 if decided else False)
        lw = layout["layer"][i][2]
        single = layout["singles"][i] if layout["singles"][i] is not None else layout["default"]
        g = None if single or not lw else find_gutter(lw, group_lines(lw)[1])
        cuts = [(0.0, W)] if g is None else [(0.0, g), (g, W)]
        words = []
        for k, (a, b) in enumerate(cuts):
            x, w = int(a / s), int((b - a) / s)
            subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-f", str(i + 1), "-l", str(i + 1), "-singlefile",
                            "-x", str(x), "-y", "0", "-W", str(w), "-H", str(int(H / s)), path,
                            os.path.join(tmp, "c")], check=True)
            tsv = subprocess.run([TESSERACT, os.path.join(tmp, "c.pgm"), "-", "--psm", "6", "-l", "eng", "tsv"],
                                 capture_output=True, text=True, env=env).stdout
            words += tsv_words(tsv, s, dx=x * s, block_offset=500 * k)
        return words
    return cached_ocr(path, ".cols6", pages, read)


def group_lines(words):
    """Words whose vertical centers are within half a line height of each other form a line.
    Words from Tesseract carry its own line ids, which are used instead."""
    if not words:
        return []
    h = statistics.median(w[3] - w[1] for w in words) or 1.0
    if len(words[0]) > 5:
        by_id = {}
        for w in words:
            by_id.setdefault(w[5], []).append(w)
        lines = [{"c": statistics.median((w[1] + w[3]) / 2 for w in ws), "words": sorted(ws, key=lambda w: w[0])}
                 for ws in by_id.values()]
        return sorted(lines, key=lambda l: l["c"]), h * 1.25      # Tesseract boxes are tighter than the layer's
    lines = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        c = (w[1] + w[3]) / 2
        if lines and abs(c - lines[-1]["c"]) < 0.5 * h:
            ln = lines[-1]
            ln["words"].append(w)
            ln["c"] = (ln["c"] * (len(ln["words"]) - 1) + c) / len(ln["words"])
        else:
            lines.append({"c": c, "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w[0])
    return lines, h


def find_gutter(words, h):
    """x of the gutter between two page columns, or None for a single-column page."""
    if len(words) < 40:
        return None
    x_lo = min(w[0] for w in words)
    x_hi = max(w[2] for w in words)
    span = x_hi - x_lo
    if span <= 0:
        return None
    step = max(span / 400, 0.25)
    nbins = int(span / step) + 1
    cover = [0] * nbins
    for w in words:
        for b in range(int((w[0] - x_lo) / step), min(nbins, int((w[2] - x_lo) / step) + 1)):
            cover[b] += 1
    lo, hi = int(nbins * 0.35), int(nbins * 0.65)
    mid = cover[lo:hi]
    if not mid:
        return None
    best = min(range(len(mid)), key=lambda i: (mid[i], abs(i - len(mid) / 2)))
    typical = statistics.median(c for c in cover if c > 0)
    # a real gutter is crossed by almost nothing (page headers and full-width titles only)
    if mid[best] > 0.08 * len(group_lines(words)[0]) and mid[best] > 0.15 * typical:
        return None
    # widen to the whole empty band and take its middle
    a = b = lo + best
    while a > 0 and cover[a - 1] <= cover[lo + best]:
        a -= 1
    while b < nbins - 1 and cover[b + 1] <= cover[lo + best]:
        b += 1
    return x_lo + (a + b) / 2 * step


def split_cells(words, h):
    """Split a line's words into cells where the gap is well beyond a word space."""
    cells, cur = [], [words[0]]
    for prev, w in zip(words, words[1:]):
        if w[0] - prev[2] > 1.1 * h:
            cells.append(cur)
            cur = [w]
        else:
            cur.append(w)
    cells.append(cur)
    return [{"x0": c[0][0], "x1": c[-1][2], "text": " ".join(w[4] for w in c)} for c in cells]


HEAD_RE = re.compile(r"(H|B|N)\W?[O0]\W?U\W?S\W?E|SENAT|J\W?[O0]\W?U\W?R\W?N\W?A\W?L|OURNAL", re.I)


MONTHS = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"


def is_running_head(text, y, H):
    """A page's running head ("722 HOUSE JOURNAL", "March 15, 1961  HOUSE JOURNAL  723"): short,
    near the top, and naming the journal, possibly mangled by OCR. Tesseract may hand back the
    dateline or page number as a line of its own, so those count too when right at the top."""
    if y > 0.2 * H or len(text) > 70:
        return False
    if HEAD_RE.search(text) and (re.search(r"JOURNAL|OURNAL|J\W?OUR", text, re.I) or re.search(r"\d{2,4}", text)):
        return True
    from rapidfuzz import fuzz
    letters = re.sub(r"[^A-Za-z ]", "", text).strip().upper()
    if letters and max(fuzz.ratio("HOUSE JOURNAL", letters), fuzz.ratio("SENATE JOURNAL", letters)) >= 65 \
            and len(letters) <= 30:
        return True                                        # "HOFSE JOl'HXAL." (but not a member named Houston)
    return y < 0.12 * H and bool(re.match(r"^\W*(?:(?:" + MONTHS + r")\w*\.?\s+\d{1,2},?\s+\d{4}|\d{1,4})\W*$", text.strip(), re.I))


def single_column(page):
    """True if the page is one column of text, False if two, None if it can't tell. On a real
    two-column page no prose line crosses the gutter; on a one-column page nearly all do, and the
    apparent "gutter" is the white space between columns of a vote list."""
    W, H, words = page
    if not words:
        return None
    lines, h = group_lines(words)
    g = find_gutter(words, h)
    if g is None:
        return True
    prose = [ln for ln in lines if len(ln["words"]) >= 7]
    if len(prose) < 4:
        return None
    crossing = sum(1 for ln in prose if any(w[0] < g < w[2] for w in ln["words"]))
    return crossing > 0.5 * len(prose)


def page_lines(page, single=False):
    """Lines of one page in reading order: [{"col": "L"|"R"|"F", "y": .., "cells": [...]}, ...]."""
    W, H, words = page
    if not words:
        return []
    lines, h = group_lines(words)
    g = None if single else find_gutter(words, h)
    # the top line, set apart, opening with a month or closing with a page number, is the running
    # head however OCR mangled the rest ("Jan. ,..,;., ()? J o' NO . 129")
    top = " ".join(w[4] for w in lines[0]["words"]).strip()
    head = len(lines) > 1 and lines[0]["c"] < 0.1 * H and lines[1]["c"] - lines[0]["c"] > 2.5 * h and \
        bool(re.match(r"^\W*(?:" + MONTHS + r")\w*\.", top, re.I) or re.search(r"\b\d{1,4}\W*$", top))
    out = []
    for k, ln in enumerate(lines):
        ws = ln["words"]
        if (k == 0 and head) or is_running_head(" ".join(w[4] for w in ws), ln["c"], H):
            continue
        if g is None:
            out.append({"col": "F", "y": ln["c"], "cells": split_cells(ws, h)})
            continue
        left = [w for w in ws if w[2] <= g]
        right = [w for w in ws if w[0] >= g]
        cross = [w for w in ws if w[0] < g < w[2]]
        if cross:
            out.append({"col": "F", "y": ln["c"], "cells": split_cells(ws, h)})
            continue
        if left:
            out.append({"col": "L", "y": ln["c"], "cells": split_cells(left, h)})
        if right:
            out.append({"col": "R", "y": ln["c"], "cells": split_cells(right, h)})
    # reading order: walk down the page; at each full-width line, flush the columns gathered above it
    ordered, left, right = [], [], []
    for ln in sorted(out, key=lambda l: l["y"]):
        if ln["col"] == "F":
            ordered += left + right + [ln]
            left, right = [], []
        elif ln["col"] == "L":
            left.append(ln)
        else:
            right.append(ln)
    ordered += left + right
    return ordered


def document_lines(path, header_re=None, ocr=None, pages=None):
    """All pages' lines in reading order, each tagged with its page index; drops running heads.
    ocr: "layer" (the PDF's own text layer) or "tesseract" (default: $OCR, else "layer").
    pages: for a Tesseract read, only these pages (the rest read as empty)."""
    ocr = ocr or os.environ.get("OCR", "layer")
    lines = []
    # "tesseract": automatic layout (psm 3); "tesseract4": single-column mode, a third, different read
    # "columns": each page column read alone as a block (psm 6)
    pages = tesseract_pages(path, pages=pages) if ocr == "tesseract" \
        else tesseract_pages(path, psm="4", pages=pages) if ocr == "tesseract4" \
        else column_pages(path, pages=pages) if ocr == "columns" else pdf_pages(path)
    # one layout per day: pages that can't tell (all vote list) follow the rest of the document
    votes = [single_column(p) for p in pages]
    decided = [v for v in votes if v is not None]
    default = sum(decided) > len(decided) / 2 if decided else False
    for i, page in enumerate(pages):
        for ln in page_lines(page, single=votes[i] if votes[i] is not None else default):
            text = " ".join(c["text"] for c in ln["cells"])
            if header_re is not None and header_re.match(text.strip()):
                continue
            ln["page"] = i
            ln["text"] = text
            lines.append(ln)
    return lines

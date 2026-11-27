"""Layout analysis: segments, page furniture, columns and reading order."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

CJK_RE = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")
MATH_FONT_RE = re.compile(r"(cmmi|cmsy|cmex|msbm|msam|eufm|rsfs|math|symbol|stix.*two.*math|cambria.?math|"
                          r"mtmi|mtsy|txmi|txsy|pxmi|pxsy|lmmath|euler|esint|wasy)", re.I)
MATH_CHARS = set("=+−-×÷·∑∏∫∮√∂∇≤≥≠≈≡∝∈∉⊂⊃⊆⊇∪∩∧∨→←↔⇒⇐⇔↦∞±∓‖∥⊥∠°′″^_|<>/*∗"
                 "αβγδεζηθϑικλμνξοπϖρϱσςτυφϕχψωΓΔΘΛΞΠΣΥΦΨΩℓℏℜℑ∀∃∅⊕⊗⊙†‡")
PAGE_NUM_RE = re.compile(r"^(page\s*)?[-–—]?\s*(\d{1,4}|[ivxlcdm]{1,7})\s*[-–—]?(\s*(of|/)\s*\d{1,4})?$|"
                         r"^第\s*\d{1,4}\s*页(\s*[/，,]?\s*共\s*\d{1,4}\s*页)?$|^\d{1,4}\s*/\s*\d{1,4}$", re.I)
# one-off page furniture (journal line, DOI, arXiv id, copyright) that a short document cannot
# confirm by repetition; only small-type lines inside the outer page margins qualify
TOP_HINT_RE = re.compile(r"(arxiv:\s*\d|preprint|manuscript|proceedings|conference|journal|\bvol\.|volume\s+\d|"
                         r"issn|doi\s*[:.]|https?://|www\.|©|copyright|licen[cs]e|accepted|submitted|published|"
                         r"学报|期刊|杂志|第\s*\d+\s*卷|第\s*\d+\s*期|\d{4}\s*年)", re.I)
BOTTOM_HINT_RE = re.compile(r"(©|copyright|all rights reserved|preprint submitted|manuscript submitted|"
                            r"accepted for publication|licen[cs]ed? under|issn|收稿日期|网络出版|版权)", re.I)


def is_cjk(ch):
    return bool(CJK_RE.match(ch))


def join_words(words):
    out = ""
    for w in words:
        t = w["t"]
        if out and not (is_cjk(out[-1]) and is_cjk(t[0])):
            out += " "
        out += t
    return out


def rich_words(words):
    """Text with <sup>/<sub> markup for raised / lowered runs."""
    out = ""
    for w in words:
        if w.get("pieces"):
            t = "".join("<%s>%s</%s>" % (r, x, r) if r else x for x, r in w["pieces"])
        else:
            t = w["t"]
        if out and not (is_cjk(out[-1]) and is_cjk(w["t"][0])):
            out += " "
        out += t
    return out


def cjk_join(a, b):
    """Join two text pieces with a space, except between two CJK characters."""
    if not a:
        return b
    if not b:
        return a
    if is_cjk(a[-1]) and is_cjk(b[0]):
        return a + b
    return a + " " + b


def tlen(t):
    """Display length: a CJK character is about as wide as two Latin letters."""
    return len(t) + len(CJK_RE.findall(t))


def word_count(t):
    """Words for length thresholds: Latin words + CJK characters / 2 (Chinese has no spaces)."""
    cjk = len(CJK_RE.findall(t or ""))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:[-'.][A-Za-z0-9]+)*", CJK_RE.sub(" ", t or "")))
    return latin + cjk // 2


def _median(xs, default=0.0):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else default


def _make_seg(page, words, line_idx, dirn):
    nchar = sum(len(w["t"]) for w in words)
    main = [w for w in words if not w.get("pieces")] or words
    sizes = Counter()
    for w in main:
        sizes[round(w["size"], 1)] += len(w["t"])
    size = sizes.most_common(1)[0][0] if sizes else 10.0
    bold_c = sum(len(w["t"]) for w in main if w["bold"])
    ital_c = sum(len(w["t"]) for w in main if w["italic"])
    mainc = sum(len(w["t"]) for w in main) or 1
    fonts = Counter(w["font"] for w in words for _ in range(len(w["t"])))
    text = join_words(words)
    math_font = sum(len(w["t"]) for w in words if MATH_FONT_RE.search(w["font"] or "")) / float(max(nchar, 1))
    nonspace = [c for c in text if not c.isspace()]
    sym = sum(1 for c in nonspace if c in MATH_CHARS) / float(max(len(nonspace), 1))
    confs = [w["conf"] for w in words if w.get("conf") is not None]
    return {
        "page": page, "words": words, "text": text,
        "x0": min(w["x0"] for w in words), "y0": min(w["y0"] for w in words),
        "x1": max(w["x1"] for w in words), "y1": max(w["y1"] for w in words),
        "size": float(size), "bold": bold_c > 0.6 * mainc, "italic": ital_c > 0.6 * mainc,
        "font": fonts.most_common(1)[0][0] if fonts else "",
        "dir": dirn, "line": line_idx, "math_font": round(math_font, 3), "sym": round(sym, 3),
        "conf": (sum(confs) / len(confs)) if confs else None,
        "role": None, "col": None, "band": None,
    }


def attach_scripts(pg):
    """Small-font fragments (stacked sub/superscripts, e.g. 410^{+160}_{-180},
    cm^{-1}) that the PDF stores as separate lines are merged into the text line
    they belong to and marked as <sup>/<sub>."""
    lines = [ln for ln in pg["lines"] if ln.get("dir", "h") == "h" and ln["words"]]
    if len(lines) < 2:
        return 0
    def info(ln):
        ws = ln["words"]
        size = max(w["size"] for w in ws)
        return (min(w["x0"] for w in ws), min(w["y0"] for w in ws), max(w["x1"] for w in ws),
                max(w["y1"] for w in ws), size, sum(len(w["t"]) for w in ws))
    infos = {id(ln): info(ln) for ln in lines}
    big = sorted(lines, key=lambda ln: -infos[id(ln)][4])
    moved = 0
    keep = []
    for ln in lines:
        x0, y0, x1, y1, size, nch = infos[id(ln)]
        host = None
        if nch <= 8 and len(ln["words"]) <= 3:
            for h in big:
                if h is ln:
                    continue
                hx0, hy0, hx1, hy1, hs, hn = infos[id(h)]
                if hn < 4 or size > 0.82 * hs:
                    continue
                vert = min(y1, hy1 + 0.45 * hs) - max(y0, hy0 - 0.45 * hs)
                horiz = (hx0 - 1.0 * hs) <= x0 <= (hx1 + 0.3 * hs)
                if vert > 0.5 * (y1 - y0) and horiz:
                    host = h
                    break
        if host is None:
            keep.append(ln)
            continue
        hx0, hy0, hx1, hy1, hs, _ = infos[id(host)]
        mid = (hy0 + hy1) / 2.0
        for w in ln["words"]:
            cy = (w["y0"] + w["y1"]) / 2.0
            w["pieces"] = [[w["t"], "sup" if cy < mid else "sub"]]
        host["words"] = sorted(host["words"] + ln["words"], key=lambda w: w["x0"])
        moved += 1
    if moved:
        others = [ln for ln in pg["lines"] if ln.get("dir", "h") != "h" or not ln["words"]]
        pg["lines"] = keep + others
    return moved


def build_segments(pg, gap_factor=0.8):
    """Split every text line into segments at large horizontal gaps."""
    pg["scripts_attached"] = attach_scripts(pg)
    segs = []
    for li, ln in enumerate(pg["lines"]):
        words = [w for w in ln["words"] if w["t"].strip()]
        if not words:
            continue
        if ln.get("dir") == "v":
            words.sort(key=lambda w: (w["y0"], w["x0"]))
            segs.append(_make_seg(pg["page"], words, li, "v"))
            continue
        words.sort(key=lambda w: w["x0"])
        cur = [words[0]]
        for w in words[1:]:
            prev = cur[-1]
            sz = max(min(prev["size"], w["size"]), 4.0)
            if w["x0"] - prev["x1"] > max(gap_factor * sz, 5.0):
                segs.append(_make_seg(pg["page"], cur, li, "h"))
                cur = [w]
            else:
                cur.append(w)
        segs.append(_make_seg(pg["page"], cur, li, "h"))
    for i, s in enumerate(segs):
        s["id"] = "p%d-s%d" % (pg["page"], i)
    pg["segs"] = segs
    return segs


# --------------------------------------------------------------------------
# page furniture: running headers/footers, page numbers, margin stamps,
# line numbers
# --------------------------------------------------------------------------

def _norm_key(t):
    t = t.lower()
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"\s+", "", t)
    return t


def detect_furniture(pages):
    n = len(pages)
    top_keys = defaultdict(set)
    bot_keys = defaultdict(set)
    for pg in pages:
        H = pg["height"]
        for s in pg["segs"]:
            if s["dir"] != "h":
                continue
            k = _norm_key(s["text"])
            if not k or len(k) > 160:
                continue
            if s["y1"] <= 0.095 * H:
                top_keys[(k, round(s["y0"] / 12.0))].add(pg["page"])
            elif s["y0"] >= 0.905 * H:
                bot_keys[(k, round(s["y0"] / 12.0))].add(pg["page"])
    need = max(2, int(0.3 * n + 0.5)) if n >= 3 else 2
    rep_top = {k for k, v in top_keys.items() if len(v) >= need}
    rep_bot = {k for k, v in bot_keys.items() if len(v) >= need}
    body = body_font_size(pages)
    removed = []
    for pg in pages:
        W, H = pg["width"], pg["height"]
        segs = pg["segs"]
        # line numbers: many short integers stacked near a margin
        numcol = defaultdict(list)
        for s in segs:
            if s["dir"] == "h" and re.fullmatch(r"\d{1,4}", s["text"]) and (s["x1"] < 0.14 * W or s["x0"] > 0.86 * W):
                numcol[round(s["x0"] / 8.0)].append(s)
        for _, lst in numcol.items():
            if len(lst) >= 8:
                for s in lst:
                    s["role"] = "line_number"
        for s in segs:
            if s["role"]:
                continue
            k = _norm_key(s["text"])
            ky = round(s["y0"] / 12.0)
            if s["dir"] == "v":
                hs = sorted(o["x0"] for o in segs if o["dir"] == "h")
                he = sorted(o["x1"] for o in segs if o["dir"] == "h")
                lo = hs[len(hs) // 20] if hs else 0.14 * W
                hi = he[(len(he) * 19) // 20] if he else 0.86 * W
                if (s["x1"] < min(lo - 2, 0.2 * W) or s["x0"] > max(hi + 2, 0.8 * W)) and len(s["text"]) >= 6:
                    s["role"] = "margin_text"
                continue
            if s["y1"] <= 0.095 * H and (k, ky) in rep_top:
                s["role"] = "running_header"
            elif s["y0"] >= 0.905 * H and (k, ky) in rep_bot:
                s["role"] = "running_footer"
            elif (s["y1"] <= 0.075 * H or s["y0"] >= 0.925 * H) and PAGE_NUM_RE.match(s["text"].strip()):
                s["role"] = "page_number"
            elif s["y1"] <= 0.07 * H and s["size"] <= 0.95 * body and len(s["text"]) <= 160 and \
                    TOP_HINT_RE.search(s["text"]):
                s["role"] = "running_header"
                s["furniture_rule"] = "top-margin journal/DOI/arXiv line (not repeated)"
            elif s["y0"] >= 0.93 * H and s["size"] <= 0.95 * body and len(s["text"]) <= 200 and \
                    BOTTOM_HINT_RE.search(s["text"]):
                s["role"] = "running_footer"
                s["furniture_rule"] = "bottom-margin copyright/licence line (not repeated)"
        for s in segs:
            if s["role"] in ("line_number", "margin_text", "running_header", "running_footer", "page_number"):
                removed.append({"page": pg["page"], "role": s["role"], "text": s["text"],
                                "bbox": _bb(s)})
                if s.get("furniture_rule"):
                    removed[-1]["rule"] = s["furniture_rule"]
    return removed


def _bb(o):
    return [round(o["x0"], 1), round(o["y0"], 1), round(o["x1"], 1), round(o["y1"], 1)]


# --------------------------------------------------------------------------
# columns
# --------------------------------------------------------------------------

def _body_segs(pg):
    return [s for s in pg["segs"] if s["role"] is None and s["dir"] == "h" and len(s["text"]) >= 2]


def _body_range(segs):
    xs0 = sorted(s["x0"] for s in segs if s["x1"] - s["x0"] > 15)
    xs1 = sorted(s["x1"] for s in segs if s["x1"] - s["x0"] > 15)
    if not xs0:
        return None
    lo = xs0[int(0.03 * (len(xs0) - 1))]
    hi = xs1[int(0.97 * (len(xs1) - 1))]
    return lo, hi


def _gutters_from_coverage(segs, lo, hi):
    bw = hi - lo
    if bw < 100:
        return []
    narrow = [s for s in segs if 4 < (s["x1"] - s["x0"]) < 0.55 * bw]
    if len(narrow) < 8:
        return []
    # in a multi-column page most of the running text sits in column-width lines
    prose = [s for s in segs if tlen(s["text"]) >= 25]
    wide_chars = sum(tlen(s["text"]) for s in prose if (s["x1"] - s["x0"]) >= 0.62 * bw)
    narrow_chars = sum(tlen(s["text"]) for s in prose if (s["x1"] - s["x0"]) < 0.55 * bw)
    if narrow_chars < 1.2 * wide_chars:
        return []
    n0 = int(lo)
    cov = [0] * (int(hi) - n0 + 2)
    mid_page = (lo + hi) / 2.0
    for s in narrow:
        # a short line centred on the page (author, affiliation, a centred title line) belongs to a
        # full-width band and does not block the gutter; column text is never centred there
        if abs((s["x0"] + s["x1"]) / 2.0 - mid_page) < 0.06 * bw and s["x0"] < mid_page < s["x1"]:
            continue
        a = max(int(s["x0"]) - n0, 0)
        b = min(int(s["x1"]) - n0, len(cov) - 1)
        for i in range(a, b + 1):
            cov[i] += 1
    thr = max(1, int(0.04 * len(narrow)))
    runs = []
    i = 0
    while i < len(cov):
        if cov[i] <= thr:
            j = i
            while j + 1 < len(cov) and cov[j + 1] <= thr:
                j += 1
            runs.append((i + n0, j + n0))
            i = j + 1
        else:
            i += 1
    gutters = []
    for a, b in runs:
        mid = (a + b) / 2.0
        if b - a < 4 or mid < lo + 0.18 * bw or mid > hi - 0.18 * bw:
            continue
        left = sum(tlen(s["text"]) for s in narrow if s["x1"] <= mid)
        right = sum(tlen(s["text"]) for s in narrow if s["x0"] >= mid)
        need = 0.15 * sum(tlen(s["text"]) for s in narrow)
        long_left = sum(1 for s in narrow if s["x1"] <= mid and tlen(s["text"]) >= 25)
        long_right = sum(1 for s in narrow if s["x0"] >= mid and tlen(s["text"]) >= 25)
        if left >= need and right >= need and long_left >= 3 and long_right >= 3:
            gutters.append(mid)
    # merge gutters closer than 40pt
    merged = []
    for g in sorted(gutters):
        if merged and g - merged[-1] < 40:
            merged[-1] = (merged[-1] + g) / 2.0
        else:
            merged.append(g)
    return merged[:3]


def detect_columns(pages):
    """Return {page: {"gutters": [...], "lo": x, "hi": x, "source": str}}."""
    res = {}
    doc_votes = Counter()
    per_page = {}
    for pg in pages:
        segs = _body_segs(pg)
        rng = _body_range(segs)
        if not rng:
            per_page[pg["page"]] = (None, [], segs)
            continue
        g = _gutters_from_coverage(segs, rng[0], rng[1])
        per_page[pg["page"]] = (rng, g, segs)
        if g:
            doc_votes[tuple(round(x / 10.0) for x in g)] += 1
    doc_g = None
    if doc_votes:
        key, cnt = doc_votes.most_common(1)[0]
        if cnt >= max(2, 0.3 * len(pages)):
            doc_g = [k * 10.0 for k in key]
    for pg in pages:
        rng, g, segs = per_page[pg["page"]]
        src = "page"
        if rng and not g and doc_g:
            # accept the document-level gutter if few segments cross it
            cross = sum(1 for s in segs for x in doc_g if s["x0"] < x - 3 and s["x1"] > x + 3)
            left = sum(1 for s in segs if s["x1"] <= doc_g[0])
            right = sum(1 for s in segs if s["x0"] >= doc_g[-1])
            if segs and cross <= 0.35 * len(segs) and left >= 3 and right >= 3:
                g = list(doc_g)
                src = "document"
        res[pg["page"]] = {"gutters": g, "lo": rng[0] if rng else 0.0,
                           "hi": rng[1] if rng else pg["width"], "source": src if g else "single"}
    return res


def _char_w(ch, size):
    if CJK_RE.match(ch):
        return size
    if ch.isspace():
        return 0.3 * size
    return 0.55 * size


def _split_word_at(w, g):
    """Split a word whose box straddles gutter x=g, estimating glyph positions
    (CJK ~1 em, Latin ~0.55 em).  Returns (left_word, right_word) or None."""
    t = w["t"]
    if len(t) < 2:
        return None
    size = max(w["size"], 4.0)
    est = [_char_w(c, size) for c in t]
    total = sum(est)
    width = w["x1"] - w["x0"]
    # the box includes the gutter gap; scale glyph widths to the remaining width
    gap = max(width - total, 0.0)
    scale = (width - gap) / total if total else 1.0
    x = w["x0"]
    k = 0
    while k < len(t) and x + est[k] * scale <= g + 0.5 * size:
        x += est[k] * scale
        k += 1
    if k <= 0 or k >= len(t):
        return None
    lw = dict(w, t=t[:k], x1=x, pieces=None)
    rw = dict(w, t=t[k:], x0=w["x1"] - sum(est[k:]) * scale, pieces=None)
    return lw, rw


def split_gutter_crossers(pg, colinfo):
    """Some engines (poppler with CJK text and narrow 10pt gutters) glue the end of a
    left-column line to the start of the right-column line.  Split such segments at
    the gutter.  Returns the number of segments split."""
    gutters = colinfo.get("gutters") or []
    if not gutters:
        return 0
    out, n = [], 0
    edges = [colinfo["lo"]] + list(gutters) + [colinfo["hi"]]
    for s in pg["segs"]:
        cut = None
        if s["role"] is None and s["dir"] == "h":
            for i, g in enumerate(gutters):
                colw = edges[i + 1] - edges[i]
                cx = (s["x0"] + s["x1"]) / 2.0
                if s["x0"] < g - 0.3 * colw and s["x1"] > g + 0.2 * colw and abs(cx - g) > 0.05 * colw \
                        and (s["x1"] - s["x0"]) < 2.3 * colw:
                    # only a CJK-dominant word glued across the gutter is evidence of two columns
                    glued = [w for w in s["words"] if w["x0"] < g - 1 and w["x1"] > g + 1
                             and len(CJK_RE.findall(w["t"])) >= 0.5 * len(w["t"])]
                    if glued:
                        cut = g
                        break
        if cut is None:
            out.append(s)
            continue
        left, right = [], []
        for w in s["words"]:
            if w["x1"] <= cut + 1:
                left.append(w)
            elif w["x0"] >= cut - 1:
                right.append(w)
            else:
                sp = _split_word_at(w, cut)
                if sp:
                    left.append(sp[0])
                    right.append(sp[1])
                else:
                    (left if (w["x0"] + w["x1"]) / 2.0 < cut else right).append(w)
        if not left or not right:
            out.append(s)
            continue
        for part, suffix in ((left, "a"), (right, "b")):
            ns = _make_seg(pg["page"], part, s["line"], "h")
            ns["id"] = s["id"] + suffix
            ns["split_at_gutter"] = round(cut, 1)
            out.append(ns)
        n += 1
    pg["segs"] = out
    return n


def column_intervals(colinfo):
    edges = [colinfo["lo"]] + list(colinfo["gutters"]) + [colinfo["hi"]]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def assign_column(obj, cols):
    """Return column index, or -1 when the object spans several columns."""
    if len(cols) == 1:
        return 0
    ov = []
    for (a, b) in cols:
        o = min(obj["x1"], b) - max(obj["x0"], a)
        ov.append(max(o, 0.0))
    big = [i for i, (o, (a, b)) in enumerate(zip(ov, cols)) if o > min(0.2 * (b - a), 30.0)]
    if len(big) >= 2:
        return -1
    # a short centred line straddling a gutter (author names, short captions) spans the columns
    cx = (obj["x0"] + obj["x1"]) / 2.0
    for i in range(len(cols) - 1):
        g = cols[i][1]
        if obj["x0"] < g - 2 and obj["x1"] > g + 2 and abs(cx - g) < 0.15 * (cols[i][1] - cols[i][0]):
            return -1
    best = max(range(len(cols)), key=lambda i: ov[i])
    if ov[best] <= 0:
        cx = (obj["x0"] + obj["x1"]) / 2.0
        best = min(range(len(cols)), key=lambda i: abs((cols[i][0] + cols[i][1]) / 2.0 - cx))
    return best


def order_items(items, cols):
    """Reading order for one page.

    items: dicts with x0,y0,x1,y1 (segments or region placeholders).
    Spanning items cut the page into horizontal bands; inside a band the
    columns are read left to right, each top to bottom.
    """
    for it in items:
        it["col"] = assign_column(it, cols)
    spans = sorted([it for it in items if it["col"] == -1], key=lambda it: (it["y0"], it["x0"]))
    colitems = [it for it in items if it["col"] != -1]
    # merge vertically-overlapping spanning items into one cut each
    cuts = []
    for sp in spans:
        if cuts and sp["y0"] < cuts[-1][1] - 1:
            cuts[-1][1] = max(cuts[-1][1], sp["y1"])
            cuts[-1][2].append(sp)
        else:
            cuts.append([sp["y0"], sp["y1"], [sp]])
    bands = defaultdict(list)
    for it in colitems:
        cy = (it["y0"] + it["y1"]) / 2.0
        k = 0
        for i, c in enumerate(cuts):
            if cy > (c[0] + c[1]) / 2.0:
                k = i + 1
        it["band"] = k
        bands[k].append(it)
    ordered = []
    for k in range(len(cuts) + 1):
        for ci in range(len(cols)):
            seq = [it for it in bands.get(k, []) if it["col"] == ci]
            ordered.extend(_rows_sorted(seq))
        if k < len(cuts):
            for it in _rows_sorted(cuts[k][2]):
                it["band"] = k
                ordered.append(it)
    return ordered


def _rows_sorted(seq):
    seq = sorted(seq, key=lambda it: ((it["y0"] + it["y1"]) / 2.0, it["x0"]))
    out, row = [], []
    for it in seq:
        if row:
            ry = sum((r["y0"] + r["y1"]) / 2.0 for r in row) / len(row)
            h = max(min(it["y1"] - it["y0"], row[0]["y1"] - row[0]["y0"]), 1.0)
            if abs((it["y0"] + it["y1"]) / 2.0 - ry) > 0.45 * h:
                out.extend(sorted(row, key=lambda r: r["x0"]))
                row = []
        row.append(it)
    out.extend(sorted(row, key=lambda r: r["x0"]))
    return out


def body_font_size(pages):
    c = Counter()
    for pg in pages:
        for s in pg["segs"]:
            if s["role"] is None and s["dir"] == "h":
                c[round(s["size"] * 2) / 2.0] += len(s["text"])
    return c.most_common(1)[0][0] if c else 10.0

"""Captions, table regions / cell structure, figure regions."""
from __future__ import annotations

import re
from collections import Counter

from .layout import assign_column, cjk_join, join_words, rich_words, _bb

CAPTION_RE = re.compile(
    r"^(?P<kind>Supplementary\s+Table|Supplementary\s+Figure|Extended\s+Data\s+Table|Extended\s+Data\s+Fig\.?|"
    r"Table|TABLE|Tab\.|Figure|FIGURE|Fig\.|FIG\.|Fig|Scheme|SCHEME|Algorithm|ALGORITHM|Chart|Listing|续表|附表|附图|图|表)"
    r"\s*(?P<num>S?\d+(?:[.\-]\d+)?[a-z]?|[A-Z]\.?\d+(?:[.\-]\d+)*[a-z]?|[IVXLC]+\b)"
    r"(?P<punc>\s*[.:：|]|\s+[-–—]\s|\s+)?(?P<rest>.*)$")
CONTINUED_RE = re.compile(r"^\s*[(（\[]?\s*(continued|cont(?:inued|'d|\.)?|续(?:表|上表)?)\s*[)）\]]?", re.I)
CJK_KINDS = ("图", "表", "续表", "附表", "附图")
NUMERIC_RE = re.compile(r"^[\(\[]?[-−+±~≈<>]?\d[\d.,]*(e[-+]?\d+)?%?[\)\]]?[*†‡]*$|^[-–—]$|^n/?a$", re.I)


def caption_info(seg):
    """Return (kind, number, valid) for a caption-looking segment, else None."""
    t = seg["text"].strip()
    m = CAPTION_RE.match(t)
    if not m:
        return None
    kind_raw = m.group("kind")
    k = kind_raw.lower()
    if "tab" in k or kind_raw in ("表", "续表", "附表"):
        kind = "table"
    elif kind_raw in ("图", "附图") or "fig" in k or "chart" in k or "scheme" in k:
        kind = "figure"
    elif "algorithm" in k or "listing" in k:
        kind = "algorithm"
    else:
        kind = "figure"
    punc = (m.group("punc") or "")
    rest = m.group("rest") or ""
    strong_punc = bool(re.search(r"[.:：|\-–—]", punc))
    cjk = kind_raw in CJK_KINDS
    valid = strong_punc or not rest.strip() or seg["bold"] or (cjk and punc.strip() == "" and punc != "")
    # "Table 1 shows ..." style in-text references are rejected unless bold; a caption without
    # punctuation after the number ("Table 1 Summary of results") starts with a capital letter
    if not strong_punc and rest.strip() and not seg["bold"] and not cjk:
        first = rest.strip().split()[0]
        valid = bool(re.match(r"^[A-Z][A-Za-z\-]*$", first)) and first not in ("In", "As", "We", "It", "This",
                                                                             "These", "Here", "For", "From")
    m2 = re.match(r"^\s*\(?\s*(continued|cont)", rest, re.I)
    if m2:
        valid = True
    return kind, m.group("num"), valid


def caption_label(text, num):
    """Display label of a caption: "Table 3", "Fig. 2", "表3", "续表3"."""
    m = CAPTION_RE.match(text.strip())
    kind_raw = m.group("kind") if m else text.split()[0]
    if kind_raw in CJK_KINDS:
        return kind_raw + str(num)
    return re.sub(r"\s+", " ", kind_raw) + " " + str(num)


def caption_continued(text):
    m = CAPTION_RE.match(text.strip())
    if not m:
        return False
    return m.group("kind") == "续表" or bool(CONTINUED_RE.match(m.group("rest") or ""))


def _window(seg, cols, colinfo):
    c = assign_column(seg, cols)
    if c == -1:
        return colinfo["lo"] - 4, colinfo["hi"] + 4, -1
    a, b = cols[c]
    return a - 6, b + 6, c


def _rows(segs):
    segs = sorted(segs, key=lambda s: ((s["y0"] + s["y1"]) / 2.0, s["x0"]))
    rows = []
    for s in segs:
        cy = (s["y0"] + s["y1"]) / 2.0
        if rows:
            r = rows[-1]
            h = max(min(s["y1"] - s["y0"], r["y1"] - r["y0"]), 1.0)
            if abs(cy - (r["y0"] + r["y1"]) / 2.0) <= 0.5 * h or (s["y0"] < r["y1"] - 0.5 * h and s["y1"] > r["y0"] + 0.5 * h):
                r["segs"].append(s)
                r["y0"] = min(r["y0"], s["y0"])
                r["y1"] = max(r["y1"], s["y1"])
                continue
        rows.append({"segs": [s], "y0": s["y0"], "y1": s["y1"]})
    for r in rows:
        r["segs"].sort(key=lambda s: s["x0"])
        r["x0"] = min(s["x0"] for s in r["segs"])
        r["x1"] = max(s["x1"] for s in r["segs"])
    return rows


def _caption_block(cap, cands, size, lead, rules=()):
    """The caption row (all segments right of the label on the same line) plus
    following rows with tight line spacing and the same font size. Stops at a ruling
    line (booktabs \\toprule between caption and table) and at rows split into cells."""
    h = max(cap["y1"] - cap["y0"], 1.0)
    blk = [cap] + sorted([s for s in cands if s is not cap and s["x0"] >= cap["x1"] - 2 and
                          min(s["y1"], cap["y1"]) - max(s["y0"], cap["y0"]) > 0.45 * h], key=lambda s: s["x0"])
    others = sorted([s for s in cands if s not in blk and s["y0"] > cap["y0"] + 0.5 * h], key=lambda s: s["y0"])
    rows = _rows(others)
    bottom = max(s["y1"] for s in blk)
    for r in rows:
        if r["y0"] - bottom > lead + 0.35 * size:
            break
        if any(abs(s["size"] - cap["size"]) > 0.15 * cap["size"] for s in r["segs"]):
            break
        if any(caption_info(s) and caption_info(s)[2] for s in r["segs"]):
            break
        gaps = [b["x0"] - a["x1"] for a, b in zip(r["segs"], r["segs"][1:])]
        if gaps and max(gaps) > 3.0 * size:
            break
        if gaps and len(r["segs"]) >= 3 and min(gaps) > 0.9 * size:
            break  # a table header row ("Model  Setting  Acc  F1"), not caption text
        x0, x1 = min(s["x0"] for s in blk + r["segs"]), max(s["x1"] for s in blk + r["segs"])
        if rules and _rule_between(rules, bottom, r["y0"], x0, x1):
            break
        blk.extend(r["segs"])
        bottom = r["y1"]
    return blk


def _is_prose_seg(s, ref_w):
    words = s["text"].split()
    alpha = sum(1 for w in words if re.match(r"^[A-Za-z][a-z]+[,.;:]?$", w))
    cjk_len = sum(1 for ch in s["text"] if "\u4e00" <= ch <= "\u9fff")
    wide = (s["x1"] - s["x0"]) >= 0.62 * ref_w
    return wide and (len(words) >= 7 and alpha >= 0.6 * len(words) or cjk_len >= 18)


def _is_paragraph_row(r, win_w, col_w=None):
    """A row of running text: one wide prose segment, or (in a multi-column
    window) prose segments that each fill a text column."""
    if len(r["segs"]) == 1:
        return _is_prose_seg(r["segs"][0], win_w)
    if col_w:
        prose = [s for s in r["segs"] if _is_prose_seg(s, col_w)]
        return len(prose) >= 1 and sum(s["x1"] - s["x0"] for s in prose) >= 0.6 * col_w
    return False


def _rule_between(rules, y0, y1, x0, x1):
    for r in rules:
        if y0 - 1 <= (r[1] + r[3]) / 2.0 <= y1 + 1 and min(r[2], x1) - max(r[0], x0) > 0.3 * (r[2] - r[0]):
            return True
    return False


def _walk(rows, start_y, direction, size, win_w, rules, wx0, wx1, col_w=None):
    """Collect table rows walking away from the caption."""
    seq = sorted(rows, key=lambda r: r["y0"], reverse=(direction == "up"))
    if direction == "up":
        seq = [r for r in seq if r["y1"] <= start_y + 1]
    else:
        seq = [r for r in seq if r["y0"] >= start_y - 1]
    # ruling lines (booktabs/hline) bound the table: never walk past the last rule
    # of the rule group that starts next to the caption
    limit_y = None
    rl = sorted(set(round((q[1] + q[3]) / 2.0, 1) for q in rules
                    if min(q[2], wx1) - max(q[0], wx0) > 0.25 * (wx1 - wx0)))
    rl = [y for y in rl if (y < start_y + 2 if direction == "up" else y > start_y - 2)]
    rl.sort(reverse=(direction == "up"))
    if rl and abs(rl[0] - start_y) < 4.5 * size:
        last = rl[0]
        n = 1
        for y in rl[1:]:
            lo_, hi_ = min(last, y), max(last, y)
            between = [r for r in rows if lo_ <= (r["y0"] + r["y1"]) / 2.0 <= hi_]
            # the next rule still belongs to this table when only table rows lie between
            # (long booktabs tables put many rows between \midrule and \bottomrule)
            if abs(y - last) < 15 * size or (between and not any(
                    _is_paragraph_row(r, win_w, col_w) or any(caption_info(s) and caption_info(s)[2] for s in r["segs"])
                    for r in between)):
                last = y
                n += 1
            else:
                break
        limit_y = last
        if n == 2 and abs(rl[1] - rl[0]) < 4.5 * size:
            # only \toprule + \midrule around the header and no closing rule on this page:
            # a table continued on the next page (longtable); the body runs past the last rule
            limit_y = None
    got = []
    edge = start_y
    for r in seq:
        if limit_y is not None:
            cy = (r["y0"] + r["y1"]) / 2.0
            if (direction == "down" and cy > limit_y) or (direction == "up" and cy < limit_y):
                break
        if len(r["segs"]) == 1 and re.match(r"^\d+(\.\d+)*\s+[\u4e00-\u9fffA-Z][^\d]*$", r["segs"][0]["text"]):
            break  # a section heading below/above the float
        gap = (edge - r["y1"]) if direction == "up" else (r["y0"] - edge)
        limit = (4.0 if not got else 2.6) * size
        if gap > limit:
            if not (_rule_between(rules, min(edge, r["y0"]), max(edge, r["y1"]), wx0, wx1) and gap < 6 * size):
                break
        if any(caption_info(s) and caption_info(s)[2] for s in r["segs"]):
            break
        if any(s.get("role") in ("caption", "table") for s in r["segs"]):
            break  # another float's caption or an already claimed table body
        if _is_paragraph_row(r, win_w, col_w):
            break
        if len(r["segs"]) == 1:
            # last line of a paragraph: a short row tightly attached to a prose row beyond it
            k = seq.index(r)
            if k + 1 < len(seq):
                nx = seq[k + 1]
                tight = (r["y0"] - nx["y1"]) if direction == "up" else (nx["y0"] - r["y1"])
                if tight < 0.5 * size and _is_paragraph_row(nx, win_w, col_w):
                    break
        got.append(r)
        edge = r["y0"] if direction == "up" else r["y1"]
    # trim trailing single-segment rows that look like prose
    while got and len(got[-1]["segs"]) == 1 and len(got[-1]["segs"][0]["text"].split()) > 10:
        got.pop()
    # rows of a compact table can be one segment (narrow column gaps): count cells, not segments
    multi = sum(1 for r in got if len(r["segs"]) >= 2 or len(_resplit(r["segs"], size)) >= 2)
    return got, multi


def find_regions(pg, cols, colinfo, size, lead=1.5, prefer=None):
    """Detect table and figure regions on a page.

    Marks segments with role 'caption', 'table' or 'figure_text' and returns
    (tables, figures, captions) lists of dicts.
    """
    segs = [s for s in pg["segs"] if s["role"] is None and s["dir"] == "h"]
    tables, figures, captions = [], [], []
    used = set()
    caps = []
    for s in segs:
        ci = caption_info(s)
        if not ci or not ci[2]:
            continue
        col = assign_column(s, cols)
        h = max(s["y1"] - s["y0"], 1.0)
        same_line_left = [o for o in segs if o is not s and o["x1"] <= s["x0"] + 1
                          and min(o["y1"], s["y1"]) - max(o["y0"], s["y0"]) > 0.45 * h
                          and assign_column(o, cols) == col]
        if same_line_left:
            continue
        # in-text reference that happens to start a line ("... shown in / Table 6. In this ...")
        cw = (cols[col][1] - cols[col][0]) if col >= 0 else (colinfo["hi"] - colinfo["lo"])
        above = [o for o in segs if o is not s and assign_column(o, cols) == col and o["y1"] <= s["y0"] + 1
                 and s["y0"] - o["y1"] < lead + 0.35 * s["size"] and abs(o["size"] - s["size"]) < 0.1 * s["size"]
                 and min(o["x1"], s["x1"] + 200) - max(o["x0"], s["x0"]) > 0 and (o["x1"] - o["x0"]) > 0.6 * cw]
        if above:
            last = max(above, key=lambda o: (o["y1"], o["x1"]))
            if not last["text"].rstrip().endswith((".", ":", "!", "?", "\u3002")):
                continue
        if re.match(r"^(Table|Figure|Fig\.?|Tab\.)\d", s["text"]) and not re.match(r"^\S+\d+[a-z]?\s*[.:|]", s["text"]):
            continue
        caps.append((s, ci))
    # pass 1: caption blocks
    capinfo = []
    for cap, (kind, num, _) in caps:
        if id(cap) in used:
            continue
        wx0, wx1, wcol = _window(cap, cols, colinfo)
        cands = [s for s in segs if s["x0"] >= wx0 - 2 and s["x1"] <= wx1 + 2 and id(s) not in used]
        cblk = _caption_block(cap, cands, size, lead, pg.get("rules", []))
        for s in cblk:
            used.add(id(s))
            s["role"] = "caption"
        ctext = ""
        for s_ in cblk:
            ctext = cjk_join(ctext, s_["text"])
        capobj = {"kind": kind, "number": num, "label": caption_label(cap["text"], num),
                  "continued": caption_continued(cap["text"]),
                  "segs": cblk, "page": pg["page"], "text": ctext,
                  "x0": min(s["x0"] for s in cblk), "y0": min(s["y0"] for s in cblk),
                  "x1": max(s["x1"] for s in cblk), "y1": max(s["y1"] for s in cblk)}
        captions.append(capobj)
        # the search window spans at least the caption (a page full of a narrow table makes
        # the text-column estimate too narrow)
        wx0, wx1 = min(wx0, capobj["x0"] - 6), max(wx1, capobj["x1"] + 6)
        if kind == "table":
            # a table wider than its text column (overfull in LaTeX) still sits between its own
            # rules: widen the window to the rule next to the caption
            w = wx1 - wx0
            near = [r for r in pg.get("rules", []) if min(r[2], wx1) - max(r[0], wx0) > 0.5 * w and
                    r[0] >= wx0 - 0.25 * w and r[2] <= wx1 + 0.6 * w and
                    0.08 * pg["height"] < r[1] < 0.92 * pg["height"] and  # not a header/footer rule
                    (capobj["y1"] - 2 <= r[1] <= capobj["y1"] + 4.5 * size or
                     capobj["y0"] - 4.5 * size <= r[3] <= capobj["y0"] + 2)]
            if near:
                wx0 = min(wx0, min(r[0] for r in near) - 2)
                wx1 = max(wx1, max(r[2] for r in near) + 2)
        capinfo.append((kind, num, capobj, wx0, wx1))
    col_w = min(bb - aa for aa, bb in cols) if len(cols) > 1 else None

    def walk_both(capobj, wx0, wx1):
        own = set(id(x) for x in capobj["segs"])
        rest = [s for s in segs if s["x0"] >= wx0 - 2 and s["x1"] <= wx1 + 2 and id(s) not in own and
                (id(s) not in used or s["role"] in ("caption", "table"))]
        rows = _rows(rest)
        up, mu = _walk(rows, capobj["y0"], "up", size, wx1 - wx0, pg["rules"], wx0, wx1, col_w)
        dn, md = _walk(rows, capobj["y1"], "down", size, wx1 - wx0, pg["rules"], wx0, wx1, col_w)
        return up, mu, dn, md

    # pass 2: table bodies. Captions with a body on one side only claim first;
    # ambiguous ones then follow the page's (or document's) caption convention.
    tcaps = [c for c in capinfo if c[0] == "table"]
    cand = {}
    vote = {"above": 0, "below": 0}
    for c in tcaps:
        up, mu, dn, md = walk_both(c[2], c[3], c[4])
        cand[id(c[2])] = (mu, md)
        if mu >= 2 and md < 2:
            vote["above"] += 1  # table body above the caption
        elif md >= 2 and mu < 2:
            vote["below"] += 1
    if vote["above"] == vote["below"]:
        pref = prefer or "below"
    else:
        pref = "above" if vote["above"] > vote["below"] else "below"
    order = sorted(tcaps, key=lambda c: (min(cand[id(c[2])]) >= 2, c[2]["y0"]))
    results = {}
    for kind, num, capobj, wx0, wx1 in order:
        up, mu, dn, md = walk_both(capobj, wx0, wx1)
        if mu >= 2 and md >= 2:
            side = pref
        elif md >= 2:
            side = "below"
        elif mu >= 2:
            side = "above"
        else:
            side = None
        got = (dn if side == "below" else up) if side else []
        tab = {"kind": "table", "number": num, "caption": capobj, "page": pg["page"],
               "caption_side": {"below": "above table", "above": "below table"}.get(side, "unknown"),
               "rows_raw": [], "notes": []}
        if side:
            for r in got:
                for sg in r["segs"]:
                    sg["role"] = "table"
                    used.add(id(sg))
            tab["rows_raw"] = sorted(got, key=lambda r: r["y0"])
            tab["x0"] = min(r["x0"] for r in got)
            tab["x1"] = max(r["x1"] for r in got)
            tab["y0"] = min(r["y0"] for r in got)
            tab["y1"] = max(r["y1"] for r in got)
            if mu >= 2 and md >= 2:
                tab["notes"].append("text rows on both sides of the caption; chose the %s side by layout convention" % (
                    "upper" if side == "above" else "lower"))
            build_cells(tab, size, pg["rules"])
        else:
            tab["notes"].append("no text-layer table body found next to the caption "
                                "(table may be an image or uses an unusual layout)")
            tab["x0"], tab["y0"], tab["x1"], tab["y1"] = capobj["x0"], capobj["y0"], capobj["x1"], capobj["y1"]
            tab["image_only"] = True
            img = _adjacent_image(pg, capobj, wx0, wx1, pref)
            if img:
                tab["image_bbox"] = img
                tab["notes"].append("an embedded image sits %s the caption: probably the table body" % (
                    "below" if img[1] >= capobj["y1"] - 4 else "above"))
            elif not pg.get("images") and pg.get("source") in ("poppler", "pypdf"):
                blank = _blank_region(pg, capobj, wx0, wx1, pref, size)
                if blank:
                    tab["image_bbox"] = blank
                    tab["image_bbox_guessed"] = True
                    tab["notes"].append("this text engine reports no image positions: the empty area %s the "
                                        "caption is assumed to hold the table image" % (
                                            "below" if blank[1] >= capobj["y1"] - 1 else "above"))
        results[id(capobj)] = tab
    for c in tcaps:
        tables.append(results[id(c[2])])
    pg["table_caption_side"] = pref
    # pass 2b: tables without a caption, bounded by ruling lines
    for tab in find_uncaptioned_tables(pg, segs, cols, colinfo, size, used, captions, col_w):
        tables.append(tab)
    # pass 3: figures
    for kind, num, capobj, wx0, wx1 in capinfo:
        if kind == "table":
            continue
        rest = [s for s in segs if s["x0"] >= wx0 - 2 and s["x1"] <= wx1 + 2 and id(s) not in used]
        rows = _rows(rest)
        fig = {"kind": kind, "number": num, "caption": capobj, "page": pg["page"], "text_inside": [],
               "notes": []}
        region = _figure_region(pg, capobj, wx0, wx1, size, rows)
        if region:
            fx0, fy0, fx1, fy1 = region
            for sg in rest:
                cx, cy = (sg["x0"] + sg["x1"]) / 2.0, (sg["y0"] + sg["y1"]) / 2.0
                if fx0 - 2 <= cx <= fx1 + 2 and fy0 - 2 <= cy <= fy1 + 2 and id(sg) not in used:
                    sg["role"] = "figure_text"
                    used.add(id(sg))
                    fig["text_inside"].append(sg)
            fig["x0"], fig["y0"], fig["x1"], fig["y1"] = region
        else:
            fig["x0"], fig["y0"], fig["x1"], fig["y1"] = capobj["x0"], capobj["y0"], capobj["x1"], capobj["y1"]
            fig["notes"].append("graphic region not located; caption only")
        figures.append(fig)
    return tables, figures, captions


def _adjacent_image(pg, cap, wx0, wx1, pref="below"):
    """A raster image directly below/above a caption (an image-only table body)."""
    W, H = pg["width"], pg["height"]
    cands = []
    for r in pg.get("images", []):
        if r[0] <= 1 and r[1] <= 1 and r[2] >= W - 1 and r[3] >= H - 1:
            continue  # full-page scan image
        if (r[2] - r[0]) < 40 or (r[3] - r[1]) < 15:
            continue
        if min(r[2], wx1) - max(r[0], wx0) < 0.5 * (r[2] - r[0]):
            continue
        if r[1] >= cap["y1"] - 4 and r[1] - cap["y1"] < 40:
            cands.append((0 if pref == "below" else 1, r[1] - cap["y1"], r))
        elif r[3] <= cap["y0"] + 4 and cap["y0"] - r[3] < 40:
            cands.append((0 if pref == "above" else 1, cap["y0"] - r[3], r))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    return [round(v, 1) for v in cands[0][2]]


def _blank_region(pg, cap, wx0, wx1, pref, size):
    """Text-free band next to a caption (where an image table must be when the backend
    gives no image boxes): [x0, y0, x1, y1] or None."""
    own = set(id(s) for s in cap["segs"])
    segs = [s for s in pg["segs"] if id(s) not in own and s["dir"] == "h" and s["x1"] > wx0 and s["x0"] < wx1]
    H = pg["height"]
    out = []
    below = [s["y0"] for s in segs if s["y0"] >= cap["y1"] - 1]
    end = min(below) if below else 0.95 * H
    if end - cap["y1"] >= 4 * size:
        out.append((0 if pref == "below" else 1, [round(wx0 + 6, 1), round(cap["y1"] + 1, 1), round(wx1 - 6, 1), round(end - 1, 1)]))
    above = [s["y1"] for s in segs if s["y1"] <= cap["y0"] + 1]
    start = max(above) if above else 0.05 * H
    if cap["y0"] - start >= 4 * size:
        out.append((0 if pref == "above" else 1, [round(wx0 + 6, 1), round(start + 1, 1), round(wx1 - 6, 1), round(cap["y0"] - 1, 1)]))
    if not out:
        return None
    out.sort(key=lambda o: o[0])
    return out[0][1]


def _hrules(pg, min_w=15.0):
    """Horizontal rules as (y, x0, x1); collinear pieces (cmidrule, dashed lines) joined."""
    out = sorted(((r[1] + r[3]) / 2.0, r[0], r[2]) for r in pg.get("rules", [])
                 if (r[3] - r[1]) <= 2.0 and (r[2] - r[0]) >= min_w)
    merged = []
    for y, a, b in out:
        if merged and abs(merged[-1][0] - y) < 0.8 and a <= merged[-1][2] + 4:
            m = merged[-1]
            merged[-1] = (m[0], min(m[1], a), max(m[2], b))
        else:
            merged.append((y, a, b))
    return merged


def find_uncaptioned_tables(pg, segs, cols, colinfo, size, used, captions, col_w):
    """Tables without a caption (or whose caption is on another page, e.g. a continued
    table): text in aligned columns between horizontal ruling lines."""
    tables = []
    lines = [ln for ln in _hrules(pg) if ln[2] - ln[1] >= 40.0]
    if len(lines) < 2:
        return tables

    def inbox(s, y0, y1, x0, x1, pad):
        cx, cy = (s["x0"] + s["x1"]) / 2.0, (s["y0"] + s["y1"]) / 2.0
        return x0 - 3 <= cx <= x1 + 3 and y0 - pad <= cy <= y1 + pad

    groups = []
    for ln in lines:
        placed = False
        for g in groups:
            last = g[-1]
            ov = min(last[2], ln[2]) - max(last[1], ln[1])
            if ov < 0.8 * min(last[2] - last[1], ln[2] - ln[1]) or ln[0] - last[0] > 0.6 * pg["height"]:
                continue
            x0, x1 = max(last[1], ln[1]), min(last[2], ln[2])
            between = [s for s in segs if s["dir"] == "h" and inbox(s, last[0], ln[0], x0, x1, 0)]
            if any(s["role"] in ("caption", "table", "figure_text") for s in between):
                continue
            rows = _rows([s for s in between if s["role"] is None])
            if any(_is_paragraph_row(r, x1 - x0, col_w) for r in rows):
                continue
            g.append(ln)
            placed = True
            break
        if not placed:
            groups.append([ln])
    for g in groups:
        if len(g) < 2:
            continue
        y0, y1 = g[0][0], g[-1][0]
        if y1 - y0 < 1.2 * size:
            continue
        x0, x1 = min(l[1] for l in g), max(l[2] for l in g)
        box = [s for s in segs if s["dir"] == "h" and inbox(s, y0, y1, x0, x1, 0.2 * size)]
        if not box or any(s["role"] is not None or id(s) in used for s in box):
            continue
        rows = _rows(box)
        if len(rows) < 2:
            continue
        nch = float(sum(len(s["text"]) for s in box)) or 1.0
        if sum(s.get("math_font", 0) * len(s["text"]) for s in box) / nch > 0.3 or \
                sum(s.get("sym", 0) * len(s["text"]) for s in box) / nch > 0.15:
            continue  # display maths between fraction bars / rules, not a table
        multi = sum(1 for r in rows if len(_resplit(r["segs"], size)) >= 2)
        if multi < 2 or multi < 0.5 * len(rows) or any(_is_paragraph_row(r, x1 - x0, col_w) for r in rows):
            continue
        if any(c["kind"] == "algorithm" and (abs(c["y1"] - y0) < 3 * size or abs(c["y0"] - y1) < 3 * size)
               for c in captions):
            continue  # an algorithm / listing box
        if any(c["kind"] != "table" and min(c["x1"], x1) - max(c["x0"], x0) > 0 and
               (-size <= c["y0"] - y1 < 4 * size or -size <= y0 - c["y1"] < 4 * size) for c in captions):
            continue  # grid lines of a plot next to a figure caption
        area = max((x1 - x0) * (y1 - y0), 1.0)
        if any(max(0.0, min(r[2], x1) - max(r[0], x0)) * max(0.0, min(r[3], y1) - max(r[1], y0)) > 0.3 * area
               for r in pg.get("images", []) if not (r[0] <= 1 and r[1] <= 1 and r[2] >= pg["width"] - 1)):
            continue  # text over a raster image (figure)
        cap = {"kind": "table", "number": None, "label": None, "segs": [], "page": pg["page"], "text": "",
               "x0": x0, "y0": y0, "x1": x1, "y1": y0, "synthetic": True}
        tab = {"kind": "table", "number": None, "uncaptioned": True, "caption": cap, "page": pg["page"],
               "caption_side": "none", "rows_raw": sorted(rows, key=lambda r: r["y0"]),
               "notes": ["no caption next to this table: detected from %d ruling lines and column alignment" % len(g)],
               "x0": min(r["x0"] for r in rows), "x1": max(r["x1"] for r in rows),
               "y0": min(r["y0"] for r in rows), "y1": max(r["y1"] for r in rows)}
        build_cells(tab, size, pg["rules"])
        full = sum(1 for g in tab["grid"] if len(g["cells"]) >= 2)
        if tab["ncols"] < 2 or tab["nrows"] < 3 or full < 2:
            continue
        for s in box:
            s["role"] = "table"
            used.add(id(s))
        tables.append(tab)
    return tables


def table_from_ocr_lines(lines, size):
    """Rows for build_cells() from OCR lines of a table image (words already in PDF points)."""
    from .layout import _make_seg
    rows = []
    for k, ln in enumerate(lines):
        ws = [w for w in ln["words"] if w["t"].strip()]
        if not ws:
            continue
        seg = _make_seg(0, ws, k, "h")
        rows.append(seg)
    return _rows(rows)


def _figure_region(pg, cap, wx0, wx1, size, rows):
    rects = [r for r in (pg.get("images", []) + pg.get("graphics", []))
             if min(r[2], wx1) - max(r[0], wx0) > 0.5 * (r[2] - r[0]) and (r[2] - r[0]) * (r[3] - r[1]) > 400
             and not (r[0] <= 1 and r[1] <= 1 and r[2] >= pg["width"] - 1)]
    def near(r, y_top, y_bot):
        return (r[3] <= y_top + 6 and y_top - r[3] < 60) or (r[1] >= y_bot - 6 and r[1] - y_bot < 30)
    seed = [r for r in rects if near(r, cap["y0"], cap["y1"])]
    if seed:
        above = [r for r in seed if r[3] <= cap["y0"] + 6]
        grp = above or seed
        ux0, uy0 = min(r[0] for r in grp), min(r[1] for r in grp)
        ux1, uy1 = max(r[2] for r in grp), max(r[3] for r in grp)
        changed = True
        while changed:
            changed = False
            for r in rects:
                if r in grp:
                    continue
                if r[3] >= uy0 - 15 and r[1] <= uy1 + 15 and r[2] >= ux0 - 15 and r[0] <= ux1 + 15 \
                        and not (r[1] > cap["y0"] and uy1 <= cap["y0"] + 6):
                    grp.append(r)
                    ux0, uy0, ux1, uy1 = min(ux0, r[0]), min(uy0, r[1]), max(ux1, r[2]), max(uy1, r[3])
                    changed = True
        return [ux0, uy0, ux1, uy1]
    # no graphics information: walk upward through non-prose rows
    above = sorted([r for r in rows if r["y1"] <= cap["y0"] + 1], key=lambda r: -r["y1"])
    win_w = wx1 - wx0
    top = None
    edge = cap["y0"]
    for r in above:
        if _is_paragraph_row(r, win_w):
            break
        if any(caption_info(s) for s in r["segs"]):
            break
        if r["y1"] < edge - 8 * size:
            break
        # last line of a paragraph: short row directly below a prose row
        nxt = [o for o in above if o["y1"] <= r["y0"] + 1]
        if nxt and _is_paragraph_row(nxt[0], win_w) and r["y0"] - nxt[0]["y1"] < 0.6 * size \
                and abs(r["x0"] - nxt[0]["x0"]) < 3:
            break
        top = r["y0"]
        edge = r["y0"]
    if top is None:
        return None
    return [wx0 + 6, top, wx1 - 6, cap["y0"] - 0.5]


# --------------------------------------------------------------------------
# cell reconstruction
# --------------------------------------------------------------------------

def _numlike(t):
    return bool(NUMERIC_RE.match(t)) or t in ("-", "–", "—", "/", "x", "✓", "✗")


def _resplit(seg_list, size, factor=0.55):
    """Split row segments into cells: at gaps wider than `factor` x font size,
    and between two numeric tokens (numbers are rarely space-separated inside
    one cell)."""
    cells = []
    for s in seg_list:
        words = sorted(s["words"], key=lambda w: w["x0"])
        cur = [words[0]]
        for w in words[1:]:
            gap = w["x0"] - cur[-1]["x1"]
            if gap > max(factor * min(cur[-1]["size"], w["size"]), 2.5) or \
                    (gap > 1.0 and _numlike(cur[-1]["t"]) and _numlike(w["t"])):
                cells.append(cur)
                cur = [w]
            else:
                cur.append(w)
        cells.append(cur)
    out = []
    for ws in cells:
        out.append({"text": join_words(ws), "rich": rich_words(ws), "x0": min(w["x0"] for w in ws),
                    "x1": max(w["x1"] for w in ws), "y0": min(w["y0"] for w in ws),
                    "y1": max(w["y1"] for w in ws), "words": ws})
    return out


def _split_on_seps(c, seps):
    """Split a cell whose words fall cleanly on both sides of column separators."""
    inner = [x for x in seps if c["x0"] < x < c["x1"]]
    if not inner or len(c["words"]) < 2:
        return [c]
    for x in inner:
        if any(w["x0"] < x < w["x1"] for w in c["words"]):
            return [c]
    groups = []
    for w in c["words"]:
        k = sum(1 for x in inner if w["x0"] >= x)
        while len(groups) <= k:
            groups.append([])
        groups[k].append(w)
    out = []
    for ws in groups:
        if ws:
            out.append({"text": join_words(ws), "rich": rich_words(ws), "x0": min(w["x0"] for w in ws),
                        "x1": max(w["x1"] for w in ws), "y0": min(w["y0"] for w in ws),
                        "y1": max(w["y1"] for w in ws), "words": ws})
    return out


def build_cells(tab, size, rules):
    rows = []
    for r in tab["rows_raw"]:
        rows.append({"cells": _resplit(r["segs"], size), "y0": r["y0"], "y1": r["y1"]})
    counts = [len(r["cells"]) for r in rows]
    mx = max(counts)
    body_rows = [r for r in rows if len(r["cells"]) >= max(2, int(0.6 * mx + 0.5))]
    words = [c["text"] for c in body_rows[0]["cells"]] if len(body_rows) == 1 else []
    prose_split = sum(1 for a, b in zip(words, words[1:])
                      if re.fullmatch(r"[a-z][a-z\-]*", a) and re.fullmatch(r"[a-z][a-z\-]*[,;]?", b))
    if len(body_rows) < 2 and prose_split >= 2:
        # one row over-split at the wide word gaps of justified text in a p{} column must not
        # define the columns on its own: use the largest cell count seen in at least two rows
        seen = Counter(c for c in counts if c >= 2)
        rep = [c for c, n in seen.items() if n >= 2]
        if rep:
            body_rows = [r for r in rows if len(r["cells"]) >= max(2, int(0.6 * max(rep) + 0.5))]
    x0 = min(c["x0"] for r in rows for c in r["cells"])
    x1 = max(c["x1"] for r in rows for c in r["cells"])
    # column separators from the x-projection of body-row cells
    n0 = int(x0) - 1
    cov = [0] * (int(x1) - n0 + 3)
    for r in body_rows:
        for c in r["cells"]:
            for i in range(max(int(c["x0"]) - n0, 0), min(int(c["x1"]) - n0, len(cov) - 1) + 1):
                cov[i] += 1
    seps = []
    tol = int(0.15 * len(body_rows))  # a few spanning header cells may cross a separator
    i = 0
    while i < len(cov):
        if cov[i] <= tol:
            j = i
            while j + 1 < len(cov) and cov[j + 1] <= tol:
                j += 1
            if i > 0 and j < len(cov) - 1 and j - i >= 1:
                seps.append((i + j) / 2.0 + n0)
            i = j + 1
        else:
            i += 1
    for r in rows:
        r["cells"] = [p for c in r["cells"] for p in _split_on_seps(c, seps)]
    edges = [x0 - 1] + seps + [x1 + 1]
    ncol = len(edges) - 1
    colspans = [(edges[k], edges[k + 1]) for k in range(ncol)]
    grid = []
    issues = []
    for ri, r in enumerate(rows):
        line = []
        for c in r["cells"]:
            hit = [k for k, (a, b) in enumerate(colspans)
                   if min(c["x1"], b) - max(c["x0"], a) > 0.25 * min(b - a, c["x1"] - c["x0"] + 0.1)]
            if not hit:
                cx = (c["x0"] + c["x1"]) / 2.0
                hit = [min(range(ncol), key=lambda k: abs((colspans[k][0] + colspans[k][1]) / 2.0 - cx))]
            line.append({"text": c["text"], "rich": c["rich"], "col": hit[0], "colspan": len(hit),
                         "bbox": [round(c["x0"], 1), round(c["y0"], 1), round(c["x1"], 1), round(c["y1"], 1)]})
        # merge cells that landed in the same column (over-split)
        merged = []
        for c in line:
            if merged and merged[-1]["col"] + merged[-1]["colspan"] - 1 >= c["col"]:
                m = merged[-1]
                m["text"] += " " + c["text"]
                m["rich"] += " " + c["rich"]
                m["colspan"] = max(m["col"] + m["colspan"], c["col"] + c["colspan"]) - m["col"]
                m["bbox"] = [min(m["bbox"][0], c["bbox"][0]), min(m["bbox"][1], c["bbox"][1]),
                             max(m["bbox"][2], c["bbox"][2]), max(m["bbox"][3], c["bbox"][3])]
            else:
                merged.append(c)
        # single label cell that is much wider than the first column: section row
        if len(merged) == 1 and ncol > 2 and merged[0]["col"] == 0 and merged[0]["colspan"] == 1:
            w = merged[0]["bbox"][2] - merged[0]["bbox"][0]
            if w > 1.5 * (colspans[0][1] - colspans[0][0]):
                issues.append("row %d: single label cell wider than column 1 (treated as group row)" % ri)
        filled = sum(c["colspan"] for c in merged)
        if 0 < filled < ncol and len(merged) > 1 and ri > 0:
            issues.append("row %d: %d of %d columns filled (empty cells or wrapped text)" % (ri, filled, ncol))
        grid.append({"cells": merged, "bbox": [round(x0, 1), round(r["y0"], 1), round(x1, 1), round(r["y1"], 1)]})
    # header rows: above the second horizontal rule (booktabs midrule) if rules exist,
    # otherwise the leading rows before the first mostly-numeric row
    hdr = 0
    trules = sorted(set(round((q[1] + q[3]) / 2.0, 1) for q in rules
                        if min(q[2], x1) - max(q[0], x0) > 0.5 * (x1 - x0) and tab["y0"] - 8 <= q[1] <= tab["y1"] + 8))
    inner = [y for y in trules if grid and grid[0]["bbox"][1] - 2 < y < grid[-1]["bbox"][3] + 2]
    # the header ends at the first (almost) full-width inner rule (\\midrule), not at a partial
    # \\cmidrule that groups header columns, even when that one spans more than half the table
    full = sorted(set(round((q[1] + q[3]) / 2.0, 1) for q in rules
                      if min(q[2], x1) - max(q[0], x0) > 0.9 * (x1 - x0) and tab["y0"] - 8 <= q[1] <= tab["y1"] + 8))
    inner_full = [y for y in full if y in inner]
    if inner:
        cut = inner_full[0] if inner_full else inner[0]
        hdr = sum(1 for g in grid if g["bbox"][3] <= cut + 1)
    def numeric_row(g):
        texts = [c["text"] for c in g["cells"]]
        return sum(1 for t in texts if NUMERIC_RE.match(t.replace(" ", ""))) >= max(1, len(texts) // 2)
    if not hdr:
        for i, g in enumerate(grid):
            texts = [c["text"] for c in g["cells"]]
            if numeric_row(g):
                break
            if hdr >= 1 and len(texts) == 1 and (i + 1 >= len(grid) or numeric_row(grid[i + 1])):
                break  # a group label row ("Published", "Ours") followed by data starts the body
            hdr += 1
            if hdr >= 3:
                break
        if hdr == len(grid):
            hdr = 1
    hdr = min(hdr, len(grid))
    repairs = []
    if len(grid) - hdr >= 2 and ncol >= 2:
        grid, repairs = _repair_body(grid, hdr, ncol, size, rules, x0, x1, inner)
    if repairs:  # row numbers changed: recount the partly filled rows on the repaired grid
        issues = [i for i in issues if not re.match(r"^row \d+: \d+ of \d+ columns filled", i)]
        covered = {}
        for ri, g in enumerate(grid):
            for c in g["cells"]:
                for r in range(ri + 1, ri + c.get("rowspan", 1)):
                    covered[r] = covered.get(r, 0) + c["colspan"]
        for ri, g in enumerate(grid):
            filled = sum(c["colspan"] for c in g["cells"]) + covered.get(ri, 0)
            if 0 < filled < ncol and len(g["cells"]) > 1 and ri > 0:
                issues.append("row %d: %d of %d columns filled (empty cells or wrapped text)" % (ri, filled, ncol))
    for i, g in enumerate(grid):
        g["header"] = i < hdr
    tab["ncols"] = ncol
    tab["nrows"] = len(grid)
    tab["header_rows"] = hdr
    tab["grid"] = grid
    tab["issues"] = issues + ["repaired: " + r for r in repairs]
    tab["repairs"] = repairs
    tab["rules_used"] = bool(trules)
    del tab["rows_raw"]


CONNECT_END = re.compile(r"([-\u2010/&(,+]|\b(and|or|of|with|the|for|in|on|to|a|an|by|from|via|vs\.?|at|under|per))$",
                         re.I)


def _is_num_text(t):
    t = (t or "").replace(" ", "")
    return bool(NUMERIC_RE.match(t)) or t in ("-", "–", "—", "/", "✓", "✗", "×")


def _join_cell(a, b):
    if re.search(r"[A-Za-z]-$", a) and b[:1].islower():
        return a[:-1] + b
    if a and b and re.match(r"[\u4e00-\u9fff]", a[-1]) and re.match(r"[\u4e00-\u9fff]", b[0]):
        return a + b
    return a + " " + b


def _repair_body(grid, hdr, ncol, size, rules, x0, x1, inner_rules):
    """Body-row repairs on the reconstructed grid:
    1. wrapped cell text: a row that only continues the text cells of the row above
       (lower-case start, hyphen/conjunction at the end above, or a CJK cell that filled
       its column) is merged into that row;
    2. row spans: a label in a text column that covers several data rows whose label
       cell is empty (\\multirow, top-aligned or vertically centred) gets rowspan."""
    repairs = []
    body = range(hdr, len(grid))
    colfill, colnum, colw = [0] * ncol, [0] * ncol, [0.0] * ncol
    for i in body:
        for c in grid[i]["cells"]:
            if c["colspan"] == 1 and c["text"].strip():
                colfill[c["col"]] += 1
                colnum[c["col"]] += 1 if _is_num_text(c["text"]) else 0
                colw[c["col"]] = max(colw[c["col"]], c["bbox"][2] - c["bbox"][0])
    numeric_col = [bool(colfill[k]) and colnum[k] >= 0.6 * colfill[k] for k in range(ncol)]

    def rule_between(ya, yb):
        return any(ya - 0.5 <= y <= yb + 0.5 for y in inner_rules)

    def continuation(g, prev):
        cells = g["cells"]
        if not cells or len(cells) >= ncol or any(c["colspan"] > 1 for c in cells):
            return False
        if any(_is_num_text(c["text"]) or numeric_col[c["col"]] for c in cells):
            return False
        if g["bbox"][1] - prev["bbox"][3] > 0.8 * size or rule_between(prev["bbox"][3], g["bbox"][1]):
            return False
        signal = 0
        for c in cells:
            pc = next((x for x in prev["cells"] if x["col"] == c["col"] and x["colspan"] == 1), None)
            if pc is None or not pc["text"].strip() or _is_num_text(pc["text"]):
                return False
            t, pt = c["text"].strip(), pc["text"].strip()
            if t[:1].islower() or CONNECT_END.search(pt):
                signal += 1
            elif re.match(r"[\u4e00-\u9fff]", pt[-1:]) and re.match(r"[\u4e00-\u9fff]", t[:1]) and \
                    (pc["bbox"][2] - pc["bbox"][0]) >= 0.85 * colw[c["col"]]:
                signal += 1
        return signal >= 1 and (signal == len(cells) or len(cells) >= 2)
    out = grid[:hdr]
    for i in range(hdr, len(grid)):
        g = grid[i]
        prev = out[-1] if len(out) > hdr else None
        if prev is not None and continuation(g, prev):
            for c in g["cells"]:
                pc = next(x for x in prev["cells"] if x["col"] == c["col"])
                before = pc["text"]
                pc["text"] = _join_cell(pc["text"], c["text"])
                pc["rich"] = _join_cell(pc["rich"], c["rich"])
                pc["bbox"] = [min(pc["bbox"][0], c["bbox"][0]), min(pc["bbox"][1], c["bbox"][1]),
                              max(pc["bbox"][2], c["bbox"][2]), max(pc["bbox"][3], c["bbox"][3])]
                repairs.append("wrapped text joined in column %d: '%s' + '%s'" % (c["col"] + 1, before[-30:], c["text"][:30]))
            prev["bbox"][3] = max(prev["bbox"][3], g["bbox"][3])
            continue
        out.append(g)
    grid = out

    def covers(g, col):
        return any(x["col"] <= col < x["col"] + x["colspan"] for x in g["cells"])

    def is_data(g):
        return any(numeric_col[x["col"]] and _is_num_text(x["text"]) for x in g["cells"])
    for col in (0, 1):
        if col >= ncol - 1 or numeric_col[col]:
            break
        rows_b = list(range(hdr, len(grid)))
        if len(rows_b) < 3:
            break
        if col == 1 and not all(covers(grid[i], 0) for i in rows_b if is_data(grid[i]) and not covers(grid[i], 1)):
            break  # column-2 spans only inside groups whose column-1 label is present
        empty = [i for i in rows_b if not covers(grid[i], col) and is_data(grid[i])]
        if not empty:
            continue
        anchors, between, boundaries = [], [], set()
        for i in rows_b:
            g = grid[i]
            cell = next((x for x in g["cells"] if x["col"] == col and x["colspan"] == 1), None)
            if cell is None or not cell["text"].strip():
                continue
            if is_data(g):
                anchors.append(i)
            elif len(g["cells"]) == 1 and i - 1 in empty and i + 1 in empty:
                anchors.append(i)
                between.append(i)
            else:
                boundaries.add(i)  # group / section label row
        if not anchors:
            continue
        # segments separated by inner rules or group-label rows
        seg_of, sid, ruled = {}, 0, False
        for k, i in enumerate(rows_b):
            if k and rule_between(grid[rows_b[k - 1]]["bbox"][3], grid[i]["bbox"][1]):
                sid += 1
                ruled = True
            elif i in boundaries:
                sid += 1
            seg_of[i] = sid
        assign, modes = {}, {}
        for sg in sorted(set(seg_of.values())):
            rows_s = [i for i in rows_b if seg_of[i] == sg and i not in boundaries]
            anc = [a for a in anchors if seg_of[a] == sg]
            emp = [e for e in empty if seg_of[e] == sg]
            if not anc or not emp:
                continue
            if ruled and len(anc) == 1 and all(i in emp or i == anc[0] for i in rows_s):
                # one label between two ruling lines: it covers the whole group
                for e in emp:
                    assign[e] = anc[0]
                modes[anc[0]] = "ruled group"
                continue
            centred = any(a in between for a in anc) or min(emp) < min(anc)
            if not centred:  # label on the first row of its group: fill down
                for e in emp:
                    above = [a for a in anc if a < e]
                    if above and all(x in emp for x in range(max(above) + 1, e)):
                        assign[e] = max(above)
                        modes[max(above)] = "top-aligned"
                continue
            # centred labels (\\multirow): take the same number of free rows above and below
            for a in anc:
                up = 0
                while (a - up - 1) in emp and (a - up - 1) not in assign:
                    up += 1
                dn = 0
                while (a + dn + 1) in emp and (a + dn + 1) not in assign:
                    dn += 1
                k = min(up, dn)
                if a in between and k == 0:
                    continue
                for x in list(range(a - k, a)) + list(range(a + 1, a + k + 1)):
                    assign[x] = a
                modes[a] = "centred"
            # rows left over that only one label can reach (e.g. a top-aligned last group)
            for e in emp:
                if e in assign:
                    continue
                reach = []
                for step in (-1, 1):
                    x = e + step
                    while x in emp and x not in assign:
                        x += step
                    if x in anc and x not in between:
                        reach.append(x)
                    elif x in assign:
                        reach.append(None)  # blocked by another label's rows: ambiguous
                if len([r for r in reach if r is not None]) == 1 and None not in reach:
                    a = next(r for r in reach if r is not None)
                    lo, hi = min(a, e), max(a, e)
                    for x in range(lo, hi + 1):
                        if x != a:
                            assign[x] = a
                    modes.setdefault(a, "top-aligned" if a < e else "bottom-aligned")
        moved = []
        for a in anchors:
            members = sorted([e for e, aa in assign.items() if aa == a] + ([] if a in between else [a]))
            block = sorted(set(members + [a]))
            if len(members) < 2 or block != list(range(block[0], block[-1] + 1)):
                continue
            if any(i in between and i != a for i in block):
                continue
            cell = next(x for x in grid[a]["cells"] if x["col"] == col)
            cell["rowspan"] = len(members)
            if members[0] != a:
                grid[a]["cells"].remove(cell)
                tgt = grid[members[0]]["cells"]
                tgt.append(cell)
                tgt.sort(key=lambda x: x["col"])
            if a in between:
                moved.append(a)
            repairs.append("column %d label '%s' spans %d rows (rowspan, %s)" % (
                col + 1, cell["text"][:30], len(members), modes.get(a, "")))
        if moved:
            grid = [g for i, g in enumerate(grid) if not (i in moved and not g["cells"])]
    return grid, repairs


def table_matrix(tab, rich=False):
    """Dense matrix (list of rows of strings). A column-spanning cell fills its first
    slot (the others stay ''); a row-spanning label is repeated in every row it covers."""
    grid = tab.get("grid", [])
    mat = [[""] * tab["ncols"] for _ in grid]
    for i, g in enumerate(grid):
        for c in g["cells"]:
            t = (c["rich"] if "<su" in c["rich"] else c["text"]) if rich else c["text"]
            for r in range(i, min(len(grid), i + c.get("rowspan", 1))):
                mat[r][c["col"]] = t
    return mat


def flat_header(tab):
    """Combine multi-level header rows into one label per column."""
    n = tab["ncols"]
    labels = [[] for _ in range(n)]
    for g in tab["grid"][:tab["header_rows"]]:
        for c in g["cells"]:
            for k in range(c["col"], min(c["col"] + c["colspan"], n)):
                if c["text"] and (not labels[k] or labels[k][-1] != c["text"]):
                    labels[k].append(c["text"])
    return [" / ".join(x) for x in labels]


def table_markdown(tab):
    if not tab.get("grid"):
        return "_(table body not available in the text layer)_"
    n = tab["ncols"]
    hdr = flat_header(tab) if tab["header_rows"] else ["col%d" % (i + 1) for i in range(n)]
    esc = lambda t: t.replace("|", "\\|")
    lines = ["| " + " | ".join(esc(h) for h in hdr) + " |", "|" + "---|" * n]
    for row in table_matrix(tab, rich=True)[tab["header_rows"]:]:
        lines.append("| " + " | ".join(esc(x) for x in row) + " |")
    return "\n".join(lines)


def table_html(tab):
    import html
    out = ["<table>"]
    covered = set()
    for i, g in enumerate(tab.get("grid", [])):
        tag = "th" if g["header"] else "td"
        out.append("  <tr>")
        pos = 0
        for c in sorted(g["cells"], key=lambda x: x["col"]):
            while pos < c["col"]:
                if (i, pos) not in covered:
                    out.append("    <%s></%s>" % (tag, tag))
                pos += 1
            rs = c.get("rowspan", 1)
            span = (' colspan="%d"' % c["colspan"] if c["colspan"] > 1 else "") + (' rowspan="%d"' % rs if rs > 1 else "")
            out.append("    <%s%s>%s</%s>" % (tag, span, html.escape(c["text"]), tag))
            for r in range(i + 1, i + rs):
                for k in range(c["col"], c["col"] + c["colspan"]):
                    covered.add((r, k))
            pos += c["colspan"]
        while pos < tab["ncols"]:
            if (i, pos) not in covered:
                out.append("    <%s></%s>" % (tag, tag))
            pos += 1
        out.append("  </tr>")
    out.append("</table>")
    return "\n".join(out)

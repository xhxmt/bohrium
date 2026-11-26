"""Captions, table regions / cell structure, figure regions."""
from __future__ import annotations

import re

from .layout import assign_column, join_words, rich_words, _bb

CAPTION_RE = re.compile(
    r"^(?P<kind>Supplementary\s+Table|Supplementary\s+Figure|Extended\s+Data\s+Table|Extended\s+Data\s+Fig\.?|"
    r"Table|TABLE|Tab\.|Figure|FIGURE|Fig\.|FIG\.|Fig|Scheme|SCHEME|Algorithm|ALGORITHM|Chart|Listing|图|表)"
    r"\s*(?P<num>S?\d+(?:[.\-]\d+)?[a-z]?|[IVXLC]+\b)"
    r"(?P<punc>\s*[.:：|]|\s+[-–—]\s|\s+)?(?P<rest>.*)$")
NUMERIC_RE = re.compile(r"^[\(\[]?[-−+±~≈<>]?\d[\d.,]*(e[-+]?\d+)?%?[\)\]]?[*†‡]*$|^[-–—]$|^n/?a$", re.I)


def caption_info(seg):
    """Return (kind, number, valid) for a caption-looking segment, else None."""
    t = seg["text"].strip()
    m = CAPTION_RE.match(t)
    if not m:
        return None
    kind_raw = m.group("kind")
    k = kind_raw.lower()
    if "tab" in k or kind_raw == "表":
        kind = "table"
    elif kind_raw == "图" or "fig" in k or "chart" in k or "scheme" in k:
        kind = "figure"
    elif "algorithm" in k or "listing" in k:
        kind = "algorithm"
    else:
        kind = "figure"
    punc = (m.group("punc") or "")
    rest = m.group("rest") or ""
    strong_punc = bool(re.search(r"[.:：|\-–—]", punc))
    cjk = kind_raw in ("图", "表")
    valid = strong_punc or not rest.strip() or seg["bold"] or (cjk and punc.strip() == "" and punc != "")
    # "Table 1 shows ..." style in-text references are rejected unless bold
    if not strong_punc and rest.strip() and not seg["bold"] and not cjk:
        valid = False
    return kind, m.group("num"), valid


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


def _caption_block(cap, cands, size, lead):
    """The caption row (all segments right of the label on the same line) plus
    following rows with tight line spacing and the same font size."""
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
        for y in rl[1:]:
            if abs(y - last) < 15 * size:
                last = y
            else:
                break
        limit_y = last
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
    multi = sum(1 for r in got if len(r["segs"]) >= 2)
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
        cblk = _caption_block(cap, cands, size, lead)
        for s in cblk:
            used.add(id(s))
            s["role"] = "caption"
        capobj = {"kind": kind, "number": num, "label": cap["text"].split()[0] + " " + num,
                  "segs": cblk, "page": pg["page"], "text": " ".join(s["text"] for s in cblk),
                  "x0": min(s["x0"] for s in cblk), "y0": min(s["y0"] for s in cblk),
                  "x1": max(s["x1"] for s in cblk), "y1": max(s["y1"] for s in cblk)}
        captions.append(capobj)
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
        results[id(capobj)] = tab
    for c in tcaps:
        tables.append(results[id(c[2])])
    pg["table_caption_side"] = pref
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
    if inner:
        cut = inner[0]
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
    for i, g in enumerate(grid):
        g["header"] = i < hdr
    tab["ncols"] = ncol
    tab["nrows"] = len(grid)
    tab["header_rows"] = hdr
    tab["grid"] = grid
    tab["issues"] = issues
    tab["rules_used"] = bool(trules)
    del tab["rows_raw"]


def table_matrix(tab):
    """Dense matrix (list of rows of strings); spanned slots repeat '' ."""
    mat = []
    for g in tab.get("grid", []):
        row = [""] * tab["ncols"]
        for c in g["cells"]:
            row[c["col"]] = c["text"]
        mat.append(row)
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
    for g in tab["grid"][tab["header_rows"]:]:
        row = [""] * n
        for c in g["cells"]:
            row[c["col"]] = c["rich"] if "<su" in c["rich"] else c["text"]
        lines.append("| " + " | ".join(esc(x) for x in row) + " |")
    return "\n".join(lines)


def table_html(tab):
    import html
    out = ["<table>"]
    for g in tab.get("grid", []):
        tag = "th" if g["header"] else "td"
        out.append("  <tr>")
        pos = 0
        for c in g["cells"]:
            while pos < c["col"]:
                out.append("    <%s></%s>" % (tag, tag))
                pos += 1
            span = ' colspan="%d"' % c["colspan"] if c["colspan"] > 1 else ""
            out.append("    <%s%s>%s</%s>" % (tag, span, html.escape(c["text"]), tag))
            pos += c["colspan"]
        while pos < tab["ncols"]:
            out.append("    <%s></%s>" % (tag, tag))
            pos += 1
        out.append("  </tr>")
    out.append("</table>")
    return "\n".join(out)

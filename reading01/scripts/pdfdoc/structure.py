"""Document assembly: blocks, block types, sections, references, continuation."""
from __future__ import annotations

import hashlib
import os
import re
import time
from collections import Counter, defaultdict

from . import backends as B
from . import layout as L
from . import tables as T

TERMINAL = tuple(".!?。！？:：;；")
EQNUM_RE = re.compile(r"^\(\s*(?:[A-Z]\.?)?\d+(?:\.\d+)*[a-z]?\s*\)$|^（\s*\d+(?:\.\d+)*\s*）$")
NUMBERED_HEAD_RE = re.compile(
    r"^(?P<num>(?:\d{1,2}(?:\.\d{1,2}){0,4})\.?|[A-H](?:\.\d{1,2}){1,3}\.?|[IVXLC]{1,6}\.|[A-H]\.|[A-H](?=\s+[A-Z][a-z])|"
    r"Appendix\s+[A-Z0-9]+[.:]?|附录\s*[A-Z0-9]*|第[一二三四五六七八九十百\d]+[章节部分]|"
    r"[一二三四五六七八九十]+[、．.]|（[一二三四五六七八九十]+）)\s*(?P<title>\S.*)$")
KNOWN_HEAD_RE = re.compile(
    r"^(abstract|introduction|background|related\s+work|prior\s+work|preliminaries|methods?|methodology|"
    r"materials\s+and\s+methods|experiments?|experimental(\s+section|\s+setup|\s+details)?|results?"
    r"(\s+and\s+discussion)?|discussion|conclusions?(\s+and\s+future\s+work)?|summary|outlook|"
    r"acknowledge?ments?|references|bibliography|works\s+cited|literature\s+cited|appendix|appendices|"
    r"supplementary\s+(material|information|materials)|data\s+availability|code\s+availability|"
    r"author\s+contributions|competing\s+interests|conflicts?\s+of\s+interest|funding|limitations|"
    r"ethics\s+statement|keywords|index\s+terms|摘\s*要|关键词|引\s*言|前\s*言|参考文献|致\s*谢|结\s*论|"
    r"附\s*录|讨\s*论|结果与讨论|实验部分|材料与方法)\s*[:.：]?$", re.I)
REF_HEAD_RE = re.compile(r"^(\d+\.?\s*)?(references|bibliography|works\s+cited|literature\s+cited|"
                         r"references\s+and\s+notes|参考文献)\s*[:.]?$", re.I)
ABSTRACT_RUNIN_RE = re.compile(r"^(abstract|summary|摘\s*要)\s*([.:：—–-]|\s)\s*(?=\S)", re.I)
REF_START_RE = re.compile(r"^(\[\d{1,4}\]|\d{1,4}\.\s|\(\d{1,4}\)\s|\[[A-Za-z][A-Za-z+\-]*\d{2,4}[a-z]?\])")
BULLET_RE = re.compile(r"^([•◦▪●○■□–\-*]\s|\(?[a-z]\)\s|\(?\d{1,2}\)\s|[ivx]{1,4}\)\s)")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_page_spec(spec, n):
    if not spec:
        return list(range(1, n + 1))
    pages = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else n
            pages.update(range(max(1, a), min(n, b) + 1))
        else:
            k = int(part)
            if 1 <= k <= n:
                pages.add(k)
    return sorted(pages)


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------

def parse_document(path, pages_spec=None, backend="auto", ocr="auto", ocr_lang="auto",
                   workdir="/tmp/pdfdoc_work", ocr_budget_s=420, log=None, force_ocr=False):
    t0 = time.time()
    log = log or (lambda *a: None)
    warnings = []
    stats = {}
    os.makedirs(workdir, exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    is_image = ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")
    doc = {"schema": "pdfdoc/1", "source": {"file": os.path.abspath(path), "sha256": _sha256(path),
                                            "bytes": os.path.getsize(path), "kind": "image" if is_image else "pdf"},
           "engine": {"tools": B.detect_tools()}, "warnings": warnings}
    if is_image:
        n = 1
        pages_list = [1]
        bk = "ocr"
        raw_pages = []
    else:
        n = B.page_count(path)
        pages_list = parse_page_spec(pages_spec, n)
        bk = B.choose_backend(backend)
        if bk is None:
            raise B.PDFOpenError("unsupported", "no PDF text engine available")
        log("backend: %s, pages %s of %d" % (bk, _compact(pages_list), n))
        raw_pages = getattr(B, "extract_" + bk)(path, pages_list, stats)
    doc["source"]["pages_total"] = n
    doc["source"]["pages_parsed"] = pages_list
    doc["engine"]["backend"] = bk
    # ---- text-layer quality, OCR where needed
    need_ocr = []
    pinfo = {}
    for pg in raw_pages:
        text = "".join(w["t"] for ln in pg["lines"] for w in ln["words"])
        nchar = len(text)
        bad = B.bad_char_count(text)
        img_area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in pg["images"])
        cover = img_area / max(pg["width"] * pg["height"], 1.0)
        status = "text"
        if nchar < 25:
            # vector-outlined text (glyphs drawn as paths) or a sparse scan also has no text layer
            status = "no_text_layer" if (cover > 0.1 or bk != "pymupdf" or pg.get("graphics")
                                         or len(pg.get("rules", [])) > 5) else "empty"
        elif bad > 0.15 * nchar:
            status = "garbled_text_layer"
        pinfo[pg["page"]] = {"page": pg["page"], "text_chars": nchar, "bad_chars": bad,
                             "image_cover": round(cover, 2), "status": status, "source": pg["source"]}
        if status in ("no_text_layer", "garbled_text_layer") or force_ocr:
            need_ocr.append(pg["page"])
    if is_image:
        need_ocr = [1]
    ocr_report = None
    if need_ocr and ocr != "off":
        log("OCR needed for pages %s" % _compact(need_ocr))
        ocr_pages, ocr_report = B.ocr_pages(path, need_ocr, stats, os.path.join(workdir, "ocr"),
                                            lang=ocr_lang, budget_s=ocr_budget_s, is_image=is_image)
        byno = {p["page"]: p for p in ocr_pages}
        newpages = []
        if is_image:
            newpages = ocr_pages
            for p in ocr_pages:
                pinfo[p["page"]] = {"page": p["page"], "status": "ocr", "source": p["source"]}
        else:
            for pg in raw_pages:
                if pg["page"] in byno:
                    o = byno[pg["page"]]
                    pinfo[pg["page"]]["status"] = "ocr"
                    pinfo[pg["page"]]["source"] = o["source"]
                    pinfo[pg["page"]]["replaced_text_layer"] = pinfo[pg["page"]].get("text_chars", 0)
                    newpages.append(o)
                else:
                    newpages.append(pg)
        raw_pages = newpages
        for p in (ocr_report or {}).get("skipped_pages", []):
            warnings.append("page %d needs OCR but was not OCR'd (%s)" % (
                p, "; ".join((ocr_report or {}).get("notes", [])) or "budget/engine"))
            if p in pinfo:
                pinfo[p]["status"] += "_unreadable"
    elif need_ocr:
        warnings.append("pages %s need OCR but OCR is disabled" % _compact(need_ocr))
    doc["ocr"] = ocr_report
    pages = raw_pages
    # ---- segments, furniture, columns
    for pg in pages:
        L.build_segments(pg)
    furniture = L.detect_furniture(pages)
    body = L.body_font_size(pages)
    colmap = L.detect_columns(pages)
    lead = _leading_gap(pages, colmap, body)
    doc["engine"]["body_font_size"] = body
    # ---- regions + reading order
    all_tables, all_figures, ordered_pages = [], [], []
    table_ocr = {"n": 0, "budget": max(60.0, ocr_budget_s)}
    side_votes, side_pref = Counter(), None
    gutter_splits = 0
    for pg in pages:
        gutter_splits += L.split_gutter_crossers(pg, colmap[pg["page"]])
    for pg in pages:
        ci = colmap[pg["page"]]
        cols = L.column_intervals(ci)
        tabs, figs, caps = T.find_regions(pg, cols, ci, body, lead, prefer=side_pref)
        for tb in tabs:
            if tb.get("image_only") and tb.get("image_bbox") and not is_image and ocr != "off" \
                    and table_ocr["n"] < 8 and time.time() - t0 < table_ocr["budget"]:
                table_ocr["n"] += 1
                note = _ocr_table_image(path, tb, os.path.join(workdir, "table_ocr"), ocr_lang, stats, body)
                log("image table %s p%d: %s" % (tb["number"], tb["page"], note))
        if tabs:
            side_votes[pg.get("table_caption_side", "below")] += 1
            side_pref = side_votes.most_common(1)[0][0]
        items = [s for s in pg["segs"] if s["role"] is None]
        for tb in tabs:
            reg = _region_item(pg["page"], "table", tb, [tb["caption"]])
            items.append(reg)
        for fg in figs:
            reg = _region_item(pg["page"], "figure", fg, [fg["caption"]])
            items.append(reg)
        # captions of other kinds (algorithms) become caption items
        ordered = L.order_items(items, cols)
        ordered_pages.append((pg, cols, ordered))
        all_tables.extend(tabs)
        all_figures.extend(figs)
        pi = pinfo.setdefault(pg["page"], {"page": pg["page"]})
        pi.update({"width": round(pg["width"], 1), "height": round(pg["height"], 1),
                   "columns": len(cols), "gutters": [round(g, 1) for g in ci["gutters"]],
                   "column_source": ci["source"], "notes": pg.get("notes", [])})
        pi.setdefault("source", pg["source"])
    # ---- blocks
    blocks = _form_blocks(ordered_pages, body, lead)
    vocab = _vocabulary(pages)
    changes = {"dehyphenation": [], "ligatures_expanded": stats.get("ligatures", 0),
               "pi_font_glyphs_remapped": stats.get("pi_font_glyphs", 0), "pi_fonts": stats.get("pi_fonts", []),
               "invisible_chars_removed": stats.get("invisible_chars", 0),
               "ocr_numeric_corrections": stats.get("ocr_corrections", [])[:200],
               "furniture_removed": len(furniture), "merged_paragraphs": [],
               "lines_split_at_column_gutter": gutter_splits}
    for b in blocks:
        if b["kind"] == "text":
            _block_text(b, vocab, changes)
    _classify(blocks, body, pages_list)
    blocks = [b for b in blocks if b.get("type") != "merged"]
    for i, b in enumerate(blocks):
        b["order"] = i
    sections = _sections(blocks)
    refs = _references(blocks, body)
    _continuations(blocks, changes)
    equations = _equations(blocks)
    tables_out = _tables_out(all_tables, blocks, {pg["page"]: pg["height"] for pg in pages}, changes)
    figures_out = _figures_out(all_figures, blocks)
    footnotes = [fn for b in blocks if b["type"] == "footnote" for fn in _footnote_items(b)]
    meta = _metadata(blocks)
    # ---- warnings / quality
    for p, info in sorted(pinfo.items()):
        if info.get("status", "text") not in ("text", "ocr"):
            warnings.append("page %d: %s" % (p, info["status"]))
    for tb in tables_out:
        if tb.get("image_only"):
            warnings.append("%s (page %d): caption found but no table text; table may be an image" % (tb["label"], tb["page"]))
    doc.update({
        "pages": [pinfo[k] for k in sorted(pinfo)],
        "metadata": meta,
        "sections": sections,
        "blocks": [_public_block(b) for b in blocks],
        "tables": tables_out,
        "figures": figures_out,
        "equations": equations,
        "footnotes": footnotes,
        "references": refs,
        "furniture": furniture,
        "changes": changes,
    })
    doc["engine"]["elapsed_s"] = round(time.time() - t0, 2)
    return doc


def _ocr_table_image(path, tb, workdir, lang, stats, body):
    """OCR an image-only table (raster image next to its caption) into cells."""
    tools = B.detect_tools()
    if not tools.get("tesseract"):
        tb["notes"].append("table body is an image; no OCR engine to read it")
        return "no tesseract"
    os.makedirs(workdir, exist_ok=True)
    bb = tb["image_bbox"]
    dpi = 300
    png = os.path.join(workdir, "table_p%d_%d.png" % (tb["page"], int(bb[1])))
    if not B.render_clip_png(path, tb["page"], bb, dpi, png, pad=0.0):
        tb["notes"].append("table image could not be rendered for OCR")
        return "render failed"
    langs = tools.get("tesseract_langs") or []
    if lang and lang != "auto":
        lg = lang
    elif "chi_sim" in langs and re.search(r"[\u4e00-\u9fff]", tb["caption"]["text"]):
        lg = "chi_sim+eng"
    else:
        lg = "eng" if ("eng" in langs or not langs) else langs[0]
    pre, _notes = B._preprocess_for_ocr(png, png[:-4] + "_pre.png")
    scale = 72.0 / dpi
    try:
        from PIL import Image  # type: ignore
        with Image.open(png) as a, Image.open(pre) as b:
            scale *= float(a.size[0]) / b.size[0]
    except Exception:
        pass
    try:
        lines, mean, n, low = B.ocr_image_to_lines(pre, scale, lg, stats, psm=6)
    except Exception as e:
        tb["notes"].append("OCR of the table image failed: %s" % e)
        return "ocr failed"
    for ln in lines:
        for w in ln["words"]:
            w["x0"] += bb[0]
            w["x1"] += bb[0]
            w["y0"] += bb[1]
            w["y1"] += bb[1]
    sizes = sorted(w["size"] for ln in lines for w in ln["words"])
    size = sizes[len(sizes) // 2] if sizes else body
    rows = T.table_from_ocr_lines(lines, size)
    if len(rows) < 2:
        tb["notes"].append("OCR found no table rows in the image")
        return "no rows"
    keep = {k: tb.get(k) for k in ("x0", "y0", "x1", "y1")}
    tb["rows_raw"] = rows
    tb["x0"], tb["y0"], tb["x1"], tb["y1"] = bb
    T.build_cells(tb, size, [])
    if tb["ncols"] < 2:
        for k in ("grid", "ncols", "nrows", "header_rows", "repairs", "rules_used", "issues"):
            tb.pop(k, None)
        tb.update(keep)
        tb["notes"].append("OCR text of the table image did not form columns")
        return "no columns"
    tb["image_only"] = False
    tb["body_source"] = "ocr-tesseract:" + lg
    tb["ocr_mean_conf"] = round(mean, 1)
    tb["issues"] = tb.get("issues", []) + ["table body read by OCR from an embedded image (mean confidence %.0f); "
                                           "verify every value against the crop" % mean]
    if low:
        tb["issues"].append("%d low-confidence OCR words: %s" % (len(low), ", ".join(w["t"] for w in low[:8])))
    return "OCR %dx%d, mean conf %.0f" % (tb["nrows"], tb["ncols"], mean)


def _compact(nums):
    nums = sorted(nums)
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else "%d-%d" % (nums[i], nums[j]))
        i = j + 1
    return ",".join(out)


def _region_item(page, kind, obj, extra):
    xs0 = [obj["x0"]] + [e["x0"] for e in extra]
    ys0 = [obj["y0"]] + [e["y0"] for e in extra]
    xs1 = [obj["x1"]] + [e["x1"] for e in extra]
    ys1 = [obj["y1"]] + [e["y1"] for e in extra]
    return {"region": kind, "obj": obj, "page": page, "x0": min(xs0), "y0": min(ys0),
            "x1": max(xs1), "y1": max(ys1), "role": "region", "text": "", "size": 0}


def _leading_gap(pages, colmap, body):
    gaps = []
    for pg in pages:
        cols = L.column_intervals(colmap[pg["page"]])
        segs = sorted([s for s in pg["segs"] if s["role"] is None and s["dir"] == "h" and abs(s["size"] - body) < 0.6],
                      key=lambda s: (L.assign_column(s, cols), s["y0"]))
        for a, b in zip(segs, segs[1:]):
            if L.assign_column(a, cols) == L.assign_column(b, cols):
                g = b["y0"] - a["y1"]
                if -2 < g < 1.5 * body:
                    gaps.append(g)
    return L._median(gaps, 0.15 * body)


# --------------------------------------------------------------------------
# block formation
# --------------------------------------------------------------------------

def _col_edges(rows, cols):
    edges = {}
    by = defaultdict(list)
    for r in rows:
        if r.get("region"):
            continue
        by[r["col"]].append(r)
    for c, items in by.items():
        if c == -1 or not items:
            continue
        width = cols[c][1] - cols[c][0]
        wide = [s for s in items if (s["x1"] - s["x0"]) > 0.6 * width] or items
        x0s = sorted(s["x0"] for s in wide)
        x1s = sorted(s["x1"] for s in wide)
        edges[c] = (x0s[len(x0s) // 5], x1s[(len(x1s) * 4) // 5])
    return edges


def _make_rows(ordered):
    """Merge segments of one visual line (same column/band) into rows."""
    rows = []
    for it in ordered:
        if it.get("region"):
            rows.append(it)
            continue
        if rows and not rows[-1].get("region"):
            r = rows[-1]
            h = max(min(it["y1"] - it["y0"], r["y1"] - r["y0"]), 1.0)
            ov = min(it["y1"], r["y1"]) - max(it["y0"], r["y0"])
            if it["col"] == r["col"] and it.get("band") == r.get("band") and ov > 0.45 * h and it["x0"] >= r["x1"] - 2:
                r["segs"].append(it)
                r["x1"] = max(r["x1"], it["x1"])
                r["y0"] = min(r["y0"], it["y0"])
                r["y1"] = max(r["y1"], it["y1"])
                continue
        rows.append({"segs": [it], "x0": it["x0"], "y0": it["y0"], "x1": it["x1"], "y1": it["y1"],
                     "col": it["col"], "band": it.get("band"), "page": it["page"]})
    for r in rows:
        if r.get("region"):
            continue
        segs = r["segs"]
        parts, rparts = [], []
        for i, s in enumerate(segs):
            if i:
                gap = s["x0"] - segs[i - 1]["x1"]
                sep = "  " if gap > 3.0 * max(s["size"], 1) else " "
                parts.append(sep)
                rparts.append(sep)
            parts.append(s["text"])
            rparts.append(L.rich_words(s["words"]))
        r["text"] = "".join(parts)
        r["rich"] = "".join(rparts)
        nch = Counter()
        for s in segs:
            nch[round(s["size"], 1)] += len(s["text"])
        r["size"] = nch.most_common(1)[0][0]
        tot = float(sum(len(s["text"]) for s in segs)) or 1.0
        words = [w for s in segs for w in s["words"]]
        wtot = float(sum(len(w["t"]) for w in words)) or 1.0
        bfrac = sum(len(w["t"]) for w in words if w["bold"]) / wtot
        r["bold"] = bfrac > 0.85
        lead = []
        for w in words:
            if w["bold"]:
                lead.append(w)
            else:
                break
        r["lead"] = L.join_words(lead) if (lead and len(lead) < len(words) and len(lead) <= 10) else None
        r["math_font"] = sum(s["math_font"] * len(s["text"]) for s in segs) / tot
        r["sym"] = sum(s["sym"] * len(s["text"]) for s in segs) / tot
        r["eqnum"] = len(segs) >= 1 and bool(EQNUM_RE.match(segs[-1]["text"].strip()))
        confs = [s["conf"] for s in segs if s.get("conf") is not None]
        r["conf"] = sum(confs) / len(confs) if confs else None
    return rows


def _row_mathy(r, edges, body):
    t = r["text"].strip()
    if EQNUM_RE.match(t):
        return True
    left, right = edges.get(r["col"], (r["x0"], r["x1"]))
    centered = (r["x0"] - left) > 1.0 * body and (right - r["x1"]) > 1.0 * body
    body_t = t
    if r["eqnum"] and len(r["segs"]) > 1:
        body_t = " ".join(s["text"] for s in r["segs"][:-1])
        if r["segs"][-1]["x1"] > right - 2.5 * body:
            centered = centered or (r["segs"][0]["x0"] - left) > 1.0 * body
    words = body_t.split()
    longwords = sum(1 for w in words if re.match(r"^[A-Za-z]{4,}[,.;:]?$", w))
    mathish = r["math_font"] >= 0.3 or r["sym"] >= 0.12 or bool(re.search(r"[=≤≥≈∝∈∑∏∫]", body_t))
    if r["eqnum"] and len(r["segs"]) > 1 and mathish and longwords <= max(4, len(words) // 2):
        return True
    return centered and mathish and longwords <= max(3, len(words) // 3)


def _row_headish(r, body):
    t = r["text"].strip()
    first = r["segs"][0]["words"][0]
    if first.get("pieces") and first["pieces"][0][1] == "sup":
        return False  # "¹University of ..." affiliation / footnote line
    if r.get("lead") and not NUMBERED_HEAD_RE.match(t) and not KNOWN_HEAD_RE.match(t):
        return False  # bold run-in lead followed by body text: a paragraph, not a heading
    pseudo = {"text": t, "bold": r["bold"], "size": r["size"],
              "ocr": any(sg.get("conf") is not None for sg in r["segs"])}
    return _is_heading_seg(pseudo, body)


def _is_heading_seg(s, body):
    t = re.sub(r"\s+", " ", s["text"].strip())
    if len(t) > 160 or len(t) < 2:
        return False
    # headings are never set smaller than the body text. OCR sizes come from ink height, and a
    # one-line heading without descenders ("1 Introduction", "3 BERT") reads ~15% small; OCR
    # also has no bold flag, so a numbered title-case line may be up to 20% under body size.
    ocr_num = bool(s.get("ocr")) and bool(NUMBERED_HEAD_RE.match(t)) and len(t.split()) <= 8
    if s["size"] < (0.8 if ocr_num else 0.95) * body:
        return False
    if re.search(r"\bet al\b|\d{3,}.*\d{3,}|\b(Phys|Rev|Lett|J\.|Proc)\.", t) and not s["bold"]:
        return False
    nw = len(t.split())
    if KNOWN_HEAD_RE.match(t) and (s["bold"] or s["size"] >= body * 1.05 or t.isupper() or nw <= 3):
        return True
    m = NUMBERED_HEAD_RE.match(t)
    if m and nw <= 16:
        title = m.group("title")
        ends_period = t.endswith(".") and not re.match(r"^[\dIVXLC.]+\.?$", t)
        first = title[:1]
        if (first.isupper() or L.is_cjk(first)) and not ends_period and not re.match(r"^\d", title) \
                and not re.search(r"\d\s*%|\b(GHz|MHz|mm|cm|nm|kg|mg|ml|mL|K|°C)\b", t[:12]):
            if s["bold"] or s["size"] >= body * 1.05 or title.isupper() or _smallcaps_like(title):
                return True
            if nw <= 8 and _title_case(title):
                return True
    if s["bold"] and nw <= 12 and L.word_count(t) <= 14 and not t.endswith((".", ",", ";", "。", "，", "；", "！", "？")) \
            and s["size"] >= body * 0.95 and (t[:1].isupper() or L.is_cjk(t[:1])) and \
            not (L.CJK_RE.search(t) and re.search(r"[。；！？]", t[:-1])):
        return True
    if s["size"] >= body * 1.18 and nw <= 14 and not t.endswith("."):
        return True
    return False


def _title_case(t):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]*", t) if len(w) >= 3 or w[0].isupper()]
    if not words:
        return bool(L.CJK_RE.search(t))
    caps = sum(1 for w in words if w[0].isupper() or w.lower() in ("of", "and", "the", "for", "in", "on", "to", "a", "an", "with", "via", "vs"))
    return caps >= 0.8 * len(words)


def _smallcaps_like(t):
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if c.isupper()) > 0.8 * len(letters)


def _form_blocks(ordered_pages, body, lead):
    blocks = []
    for pg, cols, ordered in ordered_pages:
        rows = _make_rows(ordered)
        edges = _col_edges(rows, cols)
        pno = pg["page"]
        start = len(blocks)
        cur = None
        prev = None
        for r in rows:
            if r.get("region"):
                if cur:
                    blocks.append(cur)
                blocks.append({"kind": "region", "region": r["region"], "obj": r["obj"], "page": pno,
                               "rows": [], "col": r["col"], "band": r.get("band"),
                               "x0": r["x0"], "y0": r["y0"], "x1": r["x1"], "y1": r["y1"]})
                cur, prev = None, None
                continue
            r["mathy"] = _row_mathy(r, edges, body)
            r["headish"] = _row_headish(r, body) and not r["mathy"]
            if cur is None or prev is None:
                cur = _new_block(r, pno)
                prev = r
                continue
            brk = False
            size = max(r["size"], 1.0)
            gap = r["y0"] - prev["y1"]
            left, right = edges.get(r["col"], (r["x0"], r["x1"]))
            width = max(right - left, 1.0)
            if prev.get("col_joined_full_width"):
                brk = True
            elif r["col"] != prev["col"] or (r["col"] != -1 and r.get("band") != prev.get("band")):
                brk = True
                # last line of a full-width paragraph (abstract) that is short enough to fall
                # inside one column: same left edge, normal leading, sentence still open
                if prev["col"] == -1 and r["col"] != -1 and abs(r["x0"] - prev["x0"]) < 2.0 and \
                        -1.0 <= gap <= lead + 0.4 * size and abs(r["size"] - prev["size"]) < 0.08 * size and \
                        not prev["text"].rstrip().endswith(TERMINAL) and not r["headish"] and not r["mathy"] and \
                        not prev["headish"] and cur["col"] == -1:
                    brk = False
                    r["col_joined_full_width"] = True
            elif prev["headish"] and r["headish"]:
                numbered = bool(NUMBERED_HEAD_RE.match(r["text"].strip())) or KNOWN_HEAD_RE.match(r["text"].strip())
                brk = numbered or abs(r["size"] - prev["size"]) > 0.08 * size or r["bold"] != prev["bold"] \
                    or gap > lead + 0.4 * size or len(cur["rows"]) >= 3
            elif abs(r["size"] - prev["size"]) > 0.12 * max(r["size"], prev["size"]):
                brk = True
            elif gap > lead + 0.5 * size:
                brk = True
            elif r["headish"] or prev["headish"] or r["mathy"] != prev["mathy"]:
                brk = True
            elif cur["type_hint"] == "caption_like":
                brk = False
            else:
                pt = prev["text"].rstrip()
                indent = r["x0"] - left
                prev_short = prev["x1"] < right - 0.12 * width
                if 0.7 * size < indent < 6 * size and (pt.endswith(TERMINAL) or prev_short) and prev["x0"] - left < 0.7 * size:
                    brk = True
                elif pt.endswith(TERMINAL) and prev_short:
                    brk = True
                elif BULLET_RE.match(r["text"]) and pt.endswith(TERMINAL + (",",)):
                    brk = True
                elif REF_START_RE.match(r["text"]) and pt.endswith((".", "]")) and prev_short:
                    brk = True
            if brk:
                blocks.append(cur)
                cur = _new_block(r, pno)
            else:
                cur["rows"].append(r)
                cur["x0"] = min(cur["x0"], r["x0"])
                cur["y0"] = min(cur["y0"], r["y0"])
                cur["x1"] = max(cur["x1"], r["x1"])
                cur["y1"] = max(cur["y1"], r["y1"])
            prev = r
        if cur:
            blocks.append(cur)
        for k, b in enumerate(blocks[start:]):
            b["id"] = "p%d-b%d" % (pno, k)
            b["edges"] = edges.get(b["col"])
            b["page_h"] = pg["height"]
            b["page_w"] = pg["width"]
            b["rules"] = pg.get("rules", [])
    for i, b in enumerate(blocks):
        b["order"] = i
    return blocks


def _new_block(r, pno):
    hint = "caption_like" if T.caption_info(r["segs"][0]) else None
    return {"kind": "text", "page": pno, "rows": [r], "col": r["col"], "band": r.get("band"),
            "x0": r["x0"], "y0": r["y0"], "x1": r["x1"], "y1": r["y1"], "type_hint": hint,
            "lead": r.get("lead")}


# --------------------------------------------------------------------------
# text assembly (dehyphenation, CJK joins, sup/sub markup)
# --------------------------------------------------------------------------

def _vocabulary(pages):
    v = Counter()
    for pg in pages:
        for s in pg.get("segs", []):
            ws = s["text"].split()
            for w in ws[:-1]:
                v[re.sub(r"^[^\w]+|[^\w\-]+$", "", w).lower()] += 1
    return v


def _dehyphen_pair(text, rich, plain, rch, vocab, changes, block_id):
    m = re.search(r"([A-Za-z]+)-$", text)
    nxt = re.match(r"^([a-z][A-Za-z]*)", plain)
    if m and nxt and not text.endswith("--"):
        left, right = m.group(1), nxt.group(1)
        joined, hyph = (left + right).lower(), (left + "-" + right).lower()
        keep = vocab.get(hyph, 0) > vocab.get(joined, 0)
        if changes is not None:
            changes["dehyphenation"].append({
                "block": block_id, "from": left + "-|" + right,
                "to": (left + "-" + right) if keep else (left + right),
                "rule": ("kept hyphen" if keep else "removed hyphen") +
                        (" (form seen in document)" if (vocab.get(hyph) or vocab.get(joined)) else " (default)")})
        if keep:
            return text + plain, rich + rch
        return text[:-1] + plain, rich[:-1] + rch
    if text and plain and L.is_cjk(text[-1]) and L.is_cjk(plain[0]):
        return text + plain, rich + rch
    return text + " " + plain, rich + " " + rch


def _join_lines(lines, vocab, changes, block_id):
    text, rich = "", ""
    for plain, rch in lines:
        if not text:
            text, rich = plain, rch
        else:
            text, rich = _dehyphen_pair(text, rich, plain, rch, vocab, changes, block_id)
    return text, rich


def _block_text(b, vocab, changes):
    b["lines"] = [{"plain": r["text"], "rich": r["rich"], "segs": r["segs"], "x0": r["x0"], "y0": r["y0"],
                   "x1": r["x1"], "y1": r["y1"]} for r in b["rows"]]
    b["segs"] = [s for r in b["rows"] for s in r["segs"]]
    text, rich = _join_lines([(ln["plain"], ln["rich"]) for ln in b["lines"]], vocab, changes, b.get("id"))
    b["text"] = text
    b["rich"] = rich
    sizes = Counter()
    for s in b["segs"]:
        sizes[round(s["size"], 1)] += len(s["text"])
    b["size"] = sizes.most_common(1)[0][0] if sizes else 0
    tot = max(1, sum(len(s["text"]) for s in b["segs"]))
    b["bold"] = sum(len(s["text"]) for s in b["segs"] if s["bold"]) > 0.6 * tot
    confs = [s["conf"] for s in b["segs"] if s.get("conf") is not None]
    b["ocr_conf"] = round(sum(confs) / len(confs), 1) if confs else None


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def _classify(blocks, body, pages_list):
    for b in blocks:
        if b["kind"] == "region":
            b["type"] = b["region"]
            continue
        b["type"] = "paragraph"
        segs = b["segs"]
        t = b["text"].strip()
        if not t:
            b["type"] = "empty"
            continue
        if b["type_hint"] == "caption_like":
            ci = T.caption_info(segs[0])
            if ci and ci[2]:
                b["type"] = "caption"
                b["caption_kind"], b["caption_number"] = ci[0], ci[1]
                continue
        rows = b["rows"]
        mathy = sum(1 for r in rows if r.get("mathy"))
        if mathy >= max(1, 0.6 * len(rows)) and len(t) < 800:
            b["type"] = "equation"
            continue
        if len(rows) <= 3 and rows[0].get("headish") and len(t) <= 200:
            b["type"] = "heading"
            continue
        if BULLET_RE.match(t):
            b["type"] = "list_item"
    # title on the first page of the document
    first = [b for b in blocks if b["page"] == 1 and b["kind"] == "text" and b["type"] in ("paragraph", "heading")
             and b["y0"] < 0.45 * b["page_h"] and L.tlen(b.get("text", "").replace(" ", "")) >= 8
             and not NUMBERED_HEAD_RE.match(b.get("text", "")) and not KNOWN_HEAD_RE.match(b.get("text", "").strip())]
    if first and 1 in pages_list:
        big = max(first, key=lambda b: (b["size"], -b["y0"]))
        if big["size"] >= body * 1.15 and len(big["text"]) < 300:
            big["type"] = "title"
            title_idx = big["order"]
            for nb in blocks[big["order"] + 1:]:
                if nb["page"] != 1 or nb["kind"] != "text" or abs(nb["size"] - big["size"]) > 0.08 * big["size"] \
                        or nb["y0"] - big["y1"] > 1.2 * big["size"]:
                    break
                big["rows"].extend(nb["rows"])
                big["lines"].extend(nb["lines"])
                big["segs"].extend(nb["segs"])
                big["text"] = L.cjk_join(big["text"], nb["text"])
                big["rich"] = L.cjk_join(big["rich"], nb["rich"])
                big["x0"], big["y0"] = min(big["x0"], nb["x0"]), min(big["y0"], nb["y0"])
                big["x1"], big["y1"] = max(big["x1"], nb["x1"]), max(big["y1"], nb["y1"])
                nb["type"] = "merged"
                nb["merged_into"] = big["id"]
            # a licence / permission note above the title is not a section heading
            for b in blocks:
                if b["page"] == 1 and b["order"] < title_idx and b.get("type") == "heading" and \
                        not NUMBERED_HEAD_RE.match(b["text"].strip()) and \
                        (len(b["text"]) > 80 or b["text"].rstrip().endswith(TERMINAL)):
                    b["type"] = "paragraph"
                    b["note_above_title"] = True
            # front matter: page-1 blocks after the title until abstract / first real heading
            for b in blocks:
                if b["page"] != 1 or b["order"] <= title_idx or b["kind"] != "text":
                    continue
                t = b.get("text", "").strip()
                if b["type"] == "heading" and (KNOWN_HEAD_RE.match(t) or NUMBERED_HEAD_RE.match(t)):
                    break
                if ABSTRACT_RUNIN_RE.match(t):
                    break
                if b["type"] == "paragraph" and L.word_count(t) > 40:
                    break
                if b["type"] in ("paragraph", "heading", "equation") and len(t) < 400 and b["size"] <= big["size"]:
                    b["type"] = "front_matter"
    # abstract
    found_abs = False
    for i, b in enumerate(blocks):
        if b["kind"] != "text":
            continue
        t = b["text"].strip()
        if b["type"] == "heading" and re.match(r"^(abstract|summary|摘\s*要)\s*[:.：]?$", t, re.I):
            for nb in blocks[i + 1:]:
                if nb["kind"] != "text" or nb["type"] in ("heading", "title"):
                    break
                if nb["type"] in ("paragraph", "front_matter"):
                    nb["type"] = "abstract"
                    found_abs = True
                elif nb["type"] not in ("footnote", "caption"):
                    break
            break
        if b["page"] <= 2 and b["type"] in ("paragraph", "front_matter") and ABSTRACT_RUNIN_RE.match(t):
            b["type"] = "abstract"
            b["runin_label"] = ABSTRACT_RUNIN_RE.match(t).group(1)
            found_abs = True
            break
    if not found_abs:
        # an unlabeled abstract comes before the first numbered / known section heading
        first_head = min([b["order"] for b in blocks if b.get("type") == "heading" and
                          (NUMBERED_HEAD_RE.match(b["text"].strip()) or KNOWN_HEAD_RE.match(b["text"].strip()))]
                         or [10 ** 9])
        cands = [b for b in blocks if b["page"] == 1 and b["kind"] == "text" and b["type"] in ("paragraph", "front_matter")
                 and L.word_count(b["text"]) >= 50 and b["order"] < first_head]
        if cands:
            b = cands[0]
            b["type"] = "abstract"
            b["inferred"] = True
    for b in blocks:
        if b.get("kind") == "text" and b["type"] in ("paragraph", "front_matter") and \
                re.match(r"^(keywords|key\s+words|index\s+terms|关键词)\s*[:：—–-]", b["text"].strip(), re.I):
            b["type"] = "keywords"
    # footnotes: small text at the bottom of a column with nothing below
    bycol = defaultdict(list)
    for b in blocks:
        if b["kind"] == "text":
            bycol[(b["page"], b["col"])].append(b)
    abs_sizes = [b["size"] for b in blocks if b.get("type") == "abstract"]
    abs_size = max(abs_sizes) if abs_sizes else None
    for key, lst in bycol.items():
        lst.sort(key=lambda b: b["y0"])
        for b in reversed(lst):
            if b["type"] == "abstract":
                # page-1 author notes / venue line set under the abstract in a smaller size
                if not (abs_size and b["size"] < 0.9 * abs_size):
                    break
            elif b["type"] not in ("paragraph", "front_matter", "list_item"):
                break
            if b["y0"] < 0.55 * b["page_h"] or b["size"] > body * 0.92:
                break
            t = b["text"].strip()
            if re.match(r"^([\d*†‡§¶#]|[a-z]\s)", t) or any(abs(r[3] - b["y0"]) < 8 and (r[2] - r[0]) < 0.6 * b["page_w"]
                                                           for r in b.get("rules", [])) or b["size"] <= body * 0.85:
                b["type"] = "footnote"
            else:
                break
    # heading levels
    heads = [b for b in blocks if b.get("type") == "heading"]
    roman = any(re.match(r"^[IVXLC]+\.\s", b["text"]) for b in heads)
    sizes = sorted(set(round(b["size"]) for b in heads if not NUMBERED_HEAD_RE.match(b["text"])), reverse=True)
    for b in heads:
        t = b["text"].strip()
        m = NUMBERED_HEAD_RE.match(t)
        lvl, num, title = None, None, t
        if m and not KNOWN_HEAD_RE.match(t):
            num = m.group("num").rstrip(".")
            title = m.group("title")
            if re.match(r"^\d+(\.\d+)*$", num):
                lvl = num.count(".") + 1
            elif re.match(r"^[IVXLC]+$", num):
                lvl = 1
            elif re.match(r"^[A-H]$", num):
                lvl = 2 if roman else 1
            elif re.match(r"^[A-H](\.\d+)+$", num):
                lvl = num.count(".") + 1
            elif num.startswith("Appendix") or num.startswith("附录"):
                lvl = 1
            elif num.startswith("第"):
                lvl = 1 if ("章" in num or "部分" in num) else 2
            elif num.startswith("（"):
                lvl = 2
            else:
                lvl = 1
        elif KNOWN_HEAD_RE.match(t):
            lvl = 1
        else:
            r = sizes.index(round(b["size"])) if round(b["size"]) in sizes else len(sizes)
            lvl = min(1 + r + (0 if b["size"] > body * 1.1 else 1), 4)
        b["level"], b["number"], b["title"] = lvl, num, title


FN_MARK_RE = re.compile(r"^\s*(\d{1,3}|[∗*†‡§¶#‖]{1,2})(?=\s*\S)")


def _row_starts_with_sup(r):
    try:
        w = r["segs"][0]["words"][0]
    except (KeyError, IndexError):
        return False
    return bool(w.get("pieces")) and w["pieces"][0][1] == "sup"


def _footnote_items(b):
    """One entry per footnote: a footnote block may stack several notes ('1 ...' / '2 ...'),
    each starting on a new line with a superscript marker. Ids stay the block id plus #n."""
    rows = b.get("rows") or []
    starts = [i for i, r in enumerate(rows) if i == 0 or (_row_starts_with_sup(r) and FN_MARK_RE.match(r["text"]))]
    if len(starts) <= 1:
        m = FN_MARK_RE.match(b["text"])
        return [{"id": b["id"], "block": b["id"], "page": b["page"], "bbox": _bb(b), "text": b["text"],
                 "marker": m.group(1) if m else None}]
    out = []
    for k, st in enumerate(starts):
        grp = rows[st:starts[k + 1] if k + 1 < len(starts) else len(rows)]
        txt = ""
        for r in grp:
            rt = r["text"].strip()
            txt = (txt[:-1] + rt) if txt.endswith("-") and rt[:1].islower() else L.cjk_join(txt, rt)
        m = FN_MARK_RE.match(txt)
        out.append({"id": "%s#%d" % (b["id"], k + 1), "block": b["id"], "page": b["page"],
                    "bbox": [round(min(r["x0"] for r in grp), 1), round(min(r["y0"] for r in grp), 1),
                             round(max(r["x1"] for r in grp), 1), round(max(r["y1"] for r in grp), 1)],
                    "text": txt, "marker": m.group(1) if m else None})
    return out


def _sections(blocks):
    sections, stack = [], []
    for b in blocks:
        if b.get("type") == "heading":
            while stack and stack[-1]["level"] >= b["level"]:
                stack.pop()
            sid = "sec-%d" % (len(sections) + 1)
            sec = {"id": sid, "level": b["level"], "number": b.get("number"), "title": b.get("title", b["text"]),
                   "heading_text": b["text"], "page": b["page"], "block": b["id"], "bbox": _bb(b),
                   "parent": stack[-1]["id"] if stack else None}
            sections.append(sec)
            stack.append(sec)
            b["section"] = sid
        else:
            b["section"] = stack[-1]["id"] if stack else None
    return sections


def _bb(o):
    return [round(o["x0"], 1), round(o["y0"], 1), round(o["x1"], 1), round(o["y1"], 1)]


# --------------------------------------------------------------------------
# references
# --------------------------------------------------------------------------

def _references(blocks, body):
    start = None
    for i, b in enumerate(blocks):
        if b.get("type") == "heading" and REF_HEAD_RE.match(b["text"].strip()):
            start = i
    if start is None:
        # no "References" heading (e.g. revtex letters): find the longest run of
        # text blocks whose lines mostly start with [n] / n. labels
        flags = []
        for b in blocks:
            isref = b.get("kind") == "text" and b.get("type") in ("paragraph", "list_item", "footnote", "heading") and \
                sum(1 for ln in b["lines"] if REF_START_RE.match(ln["plain"])) >= max(1, 0.3 * len(b["lines"]))
            cont = b.get("kind") == "text" and b.get("type") in ("paragraph", "list_item", "footnote") and len(b["lines"]) <= 3
            flags.append(2 if isref else (1 if cont else 0))
        # a continuation block counts only when reference blocks surround it (gap <= 2)
        best, run = None, []
        i = 0
        while i <= len(flags):
            f = flags[i] if i < len(flags) else 0
            if f == 2:
                run.append(i)
                i += 1
                continue
            if f == 1 and run:
                j = i
                while j < len(flags) and flags[j] == 1 and j - i < 2:
                    j += 1
                if j < len(flags) and flags[j] == 2:
                    run.extend(range(i, j))
                    i = j
                    continue
            if run and (best is None or len(run) > len(best)):
                best = run
            run = []
            i += 1
        if not best or sum(len(blocks[i]["lines"]) for i in best) < 6:
            return []
        start = best[0] - 1
        ref_level = 0
        region = [blocks[i] for i in best]
        return _split_refs(region, body)
    ref_level = blocks[start].get("level", 1)
    region = []
    for b in blocks[start + 1:]:
        if b.get("type") == "heading" and b.get("level", 1) <= ref_level and not REF_START_RE.match(b["text"]):
            break
        if b["kind"] == "text" and b["type"] in ("paragraph", "list_item", "footnote", "heading", "front_matter"):
            region.append(b)
    return _split_refs(region, body)


def _split_refs(region, body):
    lines = []
    for b in region:
        b["type"] = "reference"
        for ln in b["lines"]:
            lines.append((b, ln))
    if not lines:
        return []
    numeric = sum(1 for _, ln in lines if REF_START_RE.match(ln["plain"])) >= max(2, 0.15 * len(lines))
    entries = []
    cur = None
    # hanging-indent detection per column
    lefts = defaultdict(list)
    for b, ln in lines:
        lefts[(b["page"], b["col"])].append(ln["x0"])
    colleft = {k: min(v) for k, v in lefts.items()}
    for b, ln in lines:
        left = colleft[(b["page"], b["col"])]
        if numeric:
            starts = bool(REF_START_RE.match(ln["plain"]))
        else:
            starts = ln["x0"] - left < 0.6 * body
        if starts or cur is None:
            if cur:
                entries.append(cur)
            cur = {"lines": [(b, ln)]}
        else:
            cur["lines"].append((b, ln))
    if cur:
        entries.append(cur)
    out = []
    for k, e in enumerate(entries):
        txt = ""
        for _, ln in e["lines"]:
            p = ln["plain"]
            if txt.endswith("-") and p[:1].islower():
                txt = txt[:-1] + p
            elif txt:
                txt = L.cjk_join(txt, p)
            else:
                txt = p
        m = REF_START_RE.match(txt)
        label = m.group(1).strip() if (m and numeric) else str(k + 1)
        year = re.search(r"\b(19|20)\d{2}[a-z]?\b", txt)
        doi = re.search(r"\b10\.\d{4,9}/[^\s,;]+", txt)
        arx = re.search(r"arXiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", txt, re.I)
        b0 = e["lines"][0][0]
        segs_bb = [ln for _, ln in e["lines"]]
        out.append({"id": "ref-%d" % (k + 1), "label": label, "text": txt.strip(),
                    "page": b0["page"], "block": b0["id"],
                    "pages": sorted(set(b["page"] for b, _ in e["lines"])),
                    "bbox": [round(min(l["x0"] for l in segs_bb), 1), round(min(l["y0"] for l in segs_bb), 1),
                             round(max(l["x1"] for l in segs_bb), 1), round(max(l["y1"] for l in segs_bb), 1)],
                    "year": year.group(0) if year else None, "doi": doi.group(0).rstrip(".") if doi else None,
                    "arxiv": arx.group(1) if arx else None,
                    "split_rule": "numbered labels" if numeric else "hanging indent"})
    return out


# --------------------------------------------------------------------------
# continuation across columns / pages
# --------------------------------------------------------------------------

def _continuations(blocks, changes):
    flow = [b for b in blocks if b["kind"] == "text" and b["type"] in ("paragraph", "list_item", "abstract")]
    skip_types = ("figure", "table", "caption", "footnote", "empty")
    for a, b in zip(flow, flow[1:]):
        if (a["page"], a["col"], a.get("band")) == (b["page"], b["col"], b.get("band")):
            continue
        # everything between them in reading order must be non-flow material
        between = blocks[a["order"] + 1:b["order"]]
        if any(x.get("type") not in skip_types for x in between):
            continue
        at = re.sub(r"(?<=[.!?。！？])\s*[\d*†‡§]{1,3}$", "", a["text"].rstrip())
        bt = b["text"].lstrip()
        if not at or not bt:
            continue
        ends_open = not at.endswith(TERMINAL) or at.endswith("-")
        lower_start = bt[:1].islower() or (L.is_cjk(bt[:1]) and not at.endswith(("。", "！", "？")))
        edges = b.get("edges")
        not_indented = edges is not None and (b["segs"][0]["x0"] - edges[0]) < 0.6 * b["segs"][0]["size"]
        if ends_open and (lower_start or (not_indented and not bt[:1].isdigit())):
            b["continues"] = a["id"]
            a["continued_by"] = b["id"]
            changes["merged_paragraphs"].append({"from": a["id"], "to": b["id"],
                                                 "reason": "paragraph runs across %s" % (
                                                     "pages" if a["page"] != b["page"] else "columns"),
                                                 "join": at[-25:] + " ‖ " + bt[:25]})


# --------------------------------------------------------------------------
# outputs: equations, tables, figures, metadata
# --------------------------------------------------------------------------

def _equations(blocks):
    eqs = []
    for b in blocks:
        if b.get("type") != "equation":
            continue
        nums = []
        body_parts = []
        for ln in b["lines"]:
            for s in ln["segs"]:
                if EQNUM_RE.match(s["text"].strip()):
                    nums.append(s["text"].strip())
                else:
                    body_parts.append(L.rich_words(s["words"]))
        b["eq_numbers"] = nums
        eqs.append({"id": "eq-%d" % (len(eqs) + 1), "number": ", ".join(nums) if nums else None,
                    "page": b["page"], "block": b["id"], "bbox": _bb(b), "text": b["text"],
                    "text_rich": " ".join(body_parts),
                    "note": "linearised from the text layer; verify symbols against the rendered crop"})
    return eqs


def _tables_out(tabs, blocks, page_h=None, changes=None):
    """Output tables. Ids: table-N (captioned), table-N-partK (continuation on a later
    page: same number, a 'continued' / 续表 caption, or an uncaptioned ruled table at the
    top of the next page with the same columns), table-uK (no caption)."""
    page_h = page_h or {}
    out = []
    regmap = {id(b["obj"]): b for b in blocks if b["kind"] == "region"}
    seen = Counter()
    tabs = sorted(tabs, key=lambda t: (t["page"], t["y0"], t["x0"]))
    first_on, last_on = {}, {}
    for tb in tabs:
        first_on.setdefault(tb["page"], tb)
        last_on[tb["page"]] = tb
    roots = {}
    unc = 0
    for tb in tabs:
        rb = regmap.get(id(tb))
        cap = tb["caption"]
        num = tb["number"]
        why = None
        if num is None:
            prev = last_on.get(tb["page"] - 1)
            if prev is not None and prev.get("_out") and first_on[tb["page"]] is tb and \
                    tb["y0"] < 0.35 * page_h.get(tb["page"], 792.0) and \
                    prev.get("y1", 0) > 0.55 * page_h.get(prev["page"], 792.0) and \
                    prev.get("ncols") == tb.get("ncols") and prev["_out"].get("number") is not None:
                num = prev["_out"]["number"]
                why = "no caption, at the top of the page after %s with the same %d columns" % (
                    prev["_out"]["id"], tb.get("ncols", 0))
        if num is None:
            unc += 1
            tid, label = "table-u%d" % unc, "Table (no caption, p.%d)" % tb["page"]
        else:
            seen[num] += 1
            tid = "table-%s%s" % (num, "" if seen[num] == 1 else "-part%d" % seen[num])
            label = cap.get("label") if cap.get("label") and not cap.get("synthetic") else None
            if not label:
                root = roots.get(num)
                label = root["label"] if root else "Table " + str(num)
            if label.startswith("续表"):
                label = "表" + str(num)
        o = {"id": tid, "label": label, "number": num, "page": tb["page"], "block": rb["id"] if rb else None,
             "bbox": [round(tb["x0"], 1), round(tb["y0"], 1), round(tb["x1"], 1), round(tb["y1"], 1)],
             "caption": {"text": cap["text"], "bbox": _bb(cap), "position": tb.get("caption_side")},
             "nrows": tb.get("nrows", 0), "ncols": tb.get("ncols", 0), "header_rows": tb.get("header_rows", 0),
             "grid": tb.get("grid", []), "issues": tb.get("issues", []) + tb.get("notes", []),
             "image_only": tb.get("image_only", False), "ruling_lines_used": tb.get("rules_used", False)}
        for k in ("uncaptioned", "body_source", "ocr_mean_conf", "image_bbox"):
            if tb.get(k) is not None:
                o[k] = tb[k]
        if num is not None and seen[num] > 1:
            root = roots[num]
            o["continued_from"] = root["id"]
            o["issues"].append("continuation of %s (%s)" % (root["id"], why or (
                "caption marked as continued" if cap.get("continued") else "same table number seen earlier")))
            root.setdefault("continued_in", []).append(tid)
        elif num is not None:
            roots[num] = o
        o["markdown"] = T.table_markdown(tb) if tb.get("grid") else ""
        o["html"] = T.table_html(tb) if tb.get("grid") else ""
        o["matrix"] = T.table_matrix(tb) if tb.get("grid") else []
        o["header"] = T.flat_header(tb) if tb.get("grid") else []
        tb["_id"] = tid
        tb["_out"] = o
        if changes is not None:
            for r in tb.get("repairs", []):
                changes.setdefault("table_repairs", []).append({"table": tid, "page": tb["page"], "repair": r})
        out.append(o)
    # one merged table for tables split across pages
    byid = {o["id"]: o for o in out}
    for o in out:
        parts = [byid[p] for p in o.get("continued_in", []) if p in byid]
        if not parts or not o.get("grid"):
            continue
        if any(p["ncols"] != o["ncols"] or not p.get("grid") for p in parts):
            o["issues"].append("continued in %s with a different column count: not merged" % ", ".join(o["continued_in"]))
            continue
        pages = [o["page"]] + [p["page"] for p in parts]
        if any(b - a not in (0, 1) for a, b in zip(pages, pages[1:])):
            o["issues"].append("same number again in %s but not on the following page(s): not merged" %
                               ", ".join(o["continued_in"]))
            continue
        hdr = o["matrix"][:o["header_rows"]]
        rows = list(o["matrix"])
        notes = []
        for p in parts:
            pm = p["matrix"]
            k = 0
            while k < len(pm) and k < len(hdr) and [x.strip() for x in pm[k]] == [x.strip() for x in hdr[k]]:
                k += 1
            if k:
                notes.append("%s: repeated header row(s) dropped" % p["id"])
            elif p["header_rows"]:
                notes.append("%s: its first %d row(s) were read as header but differ from %s's header; kept as rows" % (
                    p["id"], p["header_rows"], o["id"]))
            rows += pm[k:]
        o["merged"] = {"parts": [o["id"]] + [p["id"] for p in parts], "nrows": len(rows), "ncols": o["ncols"],
                       "header_rows": o["header_rows"], "matrix": rows, "notes": notes,
                       "markdown": _matrix_markdown(o["header"], rows[o["header_rows"]:])}
    return out


def _matrix_markdown(header, rows):
    esc = lambda t: (t or "").replace("|", "\\|")
    n = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
    hdr = header if header else ["col%d" % (i + 1) for i in range(n)]
    lines = ["| " + " | ".join(esc(h) for h in hdr) + " |", "|" + "---|" * n]
    for r in rows:
        lines.append("| " + " | ".join(esc(x) for x in r) + " |")
    return "\n".join(lines)


def _figures_out(figs, blocks):
    out = []
    seen = Counter()
    regmap = {id(b["obj"]): b for b in blocks if b["kind"] == "region"}
    for fg in figs:
        rb = regmap.get(id(fg))
        cap = fg["caption"]
        fid = "%s-%s" % ("figure" if fg["kind"] == "figure" else fg["kind"], fg["number"])
        seen[fid] += 1
        if seen[fid] > 1:
            fid += "-part%d" % seen[fid]
        fg["_id"] = fid
        out.append({"id": fid,
                    "kind": fg["kind"], "label": cap.get("label") or (cap["text"].split()[0] + " " + fg["number"]),
                    "number": fg["number"],
                    "page": fg["page"], "block": rb["id"] if rb else None,
                    "bbox": [round(fg["x0"], 1), round(fg["y0"], 1), round(fg["x1"], 1), round(fg["y1"], 1)],
                    "caption": {"text": cap["text"], "bbox": _bb(cap)},
                    "text_inside": [s["text"] for s in fg.get("text_inside", [])][:80],
                    "notes": fg.get("notes", [])})
    return out


def _metadata(blocks):
    meta = {"title": None, "title_block": None, "front_matter": [], "abstract": None, "abstract_blocks": [],
            "keywords": None}
    for b in blocks:
        t = b.get("type")
        if t == "title" and not meta["title"]:
            meta["title"], meta["title_block"] = b["text"], b["id"]
        elif t == "front_matter":
            meta["front_matter"].append({"block": b["id"], "text": b["text"]})
        elif t == "abstract":
            txt = b["text"]
            if b.get("runin_label"):
                txt = ABSTRACT_RUNIN_RE.sub("", txt, count=1)
            meta["abstract"] = (meta["abstract"] + " " + txt) if meta["abstract"] else txt
            meta["abstract_blocks"].append(b["id"])
            if b.get("inferred"):
                meta["abstract_inferred"] = True
        elif t == "keywords" and not meta["keywords"]:
            meta["keywords"] = b["text"]
    meta.update(_authors(blocks))
    return meta


AFFIL_RE = re.compile(
    r"(universit|institut|college|school|department|dept\b|faculty|laborator|\blabs?\b|research|cent(er|re)\b|academy|"
    r"hospital|clinic|\binc\b|corp|\bltd\b|\bllc\b|gmbh|foundation|collaboration|observatory|ministry|"
    r"google|microsoft|facebook|\bmeta\b|deepmind|openai|\bibm\b|nvidia|amazon|apple|baidu|alibaba|tencent|huawei|"
    r"大学|学院|研究院|研究所|研究中心|实验室|医院|公司|中心|科学院|学校|系\b)", re.I)
NAME_PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "du", "le", "la", "del", "dos", "bin", "al", "et", "al."}
MARK_TAIL_RE = re.compile(r"[\s∗*†‡§¶#‖\d,，’'`]+$")
MARK_HEAD_RE = re.compile(r"^\s*(\d{1,2}|[∗*†‡§¶#‖a-h])(?=\s*[A-Z一-鿿])\s*\.?\s*")


def _name_like(t):
    t = t.strip()
    if not t or "@" in t or re.search(r"\d{3,}|[()\[\];:]", t) or AFFIL_RE.search(t) or KNOWN_HEAD_RE.match(t):
        return False
    if L.CJK_RE.search(t):
        return bool(re.fullmatch(r"[一-鿿·]{2,5}", t))
    ws = t.split()
    if not 1 <= len(ws) <= 5:
        return False
    return all(re.match(r"^[A-ZÀ-ÖØ-ÞĀ-ſŁ][\w.'’\-]*$", w) or w.lower() in NAME_PARTICLES for w in ws) and \
        any(len(w.strip(".")) >= 2 for w in ws)


def _split_names(text):
    """'A, B and C' / 'A·B' / 'Zhang San  Li Si' -> names with their trailing markers."""
    parts = [x for x in re.split(r"\s*(?:,|，|、|;|\band\b|&)\s*", text) if x and x.strip()]
    out = []
    for p in parts:
        p = p.strip()
        mk = MARK_TAIL_RE.search(p)
        markers = re.findall(r"\d{1,2}|[∗*†‡§¶#‖]", mk.group(0)) if mk else []
        name = MARK_TAIL_RE.sub("", p).strip()
        if L.CJK_RE.search(name) and " " in name and all(re.fullmatch(r"[一-鿿·]{2,5}", x) for x in name.split()):
            out.extend((x, []) for x in name.split())
        elif name:
            out.append((name, markers))
    # a marker-only part ("Gomez∗ †" split on space keeps "†" in the tail already)
    return out


def _authors(blocks):
    """Structured authors / affiliations / e-mails from the page-1 front matter.

    Each item carries page + block + bbox. Association is only made when the layout makes it
    unambiguous (same column position in an author grid, matching superscript markers, or a
    single affiliation for everybody); otherwise ``affiliation`` stays null."""
    fm = [b for b in blocks if b.get("type") == "front_matter" and b["page"] == 1]
    names, affils, emails = [], [], []
    for b in fm:
        for ri, r in enumerate(b["rows"]):
            for sg in r["segs"]:
                t = re.sub(r"\s+", " ", sg["text"]).strip()
                if not t:
                    continue
                bb = [round(sg["x0"], 1), round(r["y0"], 1), round(sg["x1"], 1), round(r["y1"], 1)]
                loc = {"page": b["page"], "block": b["id"], "bbox": bb, "_row": (b["order"], ri)}
                if "@" in t:
                    for m in re.finditer(r"[{(\[]([^})\]]*)[})\]]\s*@\s*(\S+)", t):
                        for u in re.split(r"[,;|\s]+", m.group(1)):
                            if u.strip():
                                emails.append(dict(loc, email="%s@%s" % (u.strip(), m.group(2).rstrip(".,;")), shared=True))
                    rest = re.sub(r"[{(\[]([^})\]]*)[})\]]\s*@\s*(\S+)", " ", t)
                    for tok in rest.split():
                        if "@" in tok:
                            emails.append(dict(loc, email=tok.strip("()<>.,;"), shared=False))
                    continue
                mh = MARK_HEAD_RE.match(t)
                body = t[mh.end():] if mh and (mh.group(1).isdigit() or mh.group(1) in "∗*†‡§¶#‖") else t
                if AFFIL_RE.search(body) and not _name_like(body):
                    affils.append(dict(loc, text=body.strip(" ,;"), marker=mh.group(1) if body is not t else None))
                    continue
                cand = _split_names(t)
                if cand and all(_name_like(n) for n, _ in cand):
                    for n, mk in cand:
                        names.append(dict(loc, name=n, markers=mk))
    if not names:
        return {}
    by_marker = {a["marker"]: a for a in affils if a.get("marker")}

    def overlap(a, b):
        return min(a[2], b[2]) - max(a[0], b[0])
    for n in names:
        n["affiliation"], n["affiliation_rule"] = None, None
        hits = [by_marker[m]["text"] for m in n["markers"] if m in by_marker]
        if hits:
            n["affiliation"], n["affiliation_rule"] = "; ".join(hits), "superscript marker"
            continue
        below = [a for a in affils if a["_row"] > n["_row"] and overlap(a["bbox"], n["bbox"]) > 0.3 * (n["bbox"][2] - n["bbox"][0])]
        if below:
            first = min(below, key=lambda a: a["_row"])
            # nearest grid row only, and the affiliation must not span several author columns
            same = [a for a in below if a["_row"] == first["_row"]]
            if len(same) == 1:
                n["affiliation"], n["affiliation_rule"] = same[0]["text"], "same column, next row"
                continue
        if len(set(a["text"] for a in affils)) == 1 and not by_marker:
            n["affiliation"], n["affiliation_rule"] = affils[0]["text"], "single affiliation for all authors"
    for n in names:
        n["email"] = None
        own = [e for e in emails if not e["shared"] and e["_row"] > n["_row"] and overlap(e["bbox"], n["bbox"]) > 0]
        if own:
            n["email"] = min(own, key=lambda e: (e["_row"], abs((e["bbox"][0] + e["bbox"][2]) - (n["bbox"][0] + n["bbox"][2]))))["email"]
    shared = [e for e in emails if e["shared"]]
    if shared and len(shared) == len(names) and not any(n["email"] for n in names):
        for n, e in zip(names, shared):
            n["email"], n["email_rule"] = e["email"], "brace list, in author order"
    for lst in (names, affils, emails):
        for x in lst:
            x.pop("_row", None)
    seen, aff_out = set(), []
    for a in affils:
        k = (a["text"], a.get("marker"))
        if k not in seen:
            seen.add(k)
            aff_out.append(a)
    return {"authors": names, "affiliations": aff_out, "emails": emails}


def _public_block(b):
    o = {"id": b["id"], "page": b["page"], "type": b.get("type"), "order": b["order"],
         "bbox": _bb(b), "col": b["col"], "section": b.get("section")}
    if b["kind"] == "region":
        o["ref"] = b["obj"].get("_id") or (("table-" if b["region"] == "table" else "figure-") + str(b["obj"]["number"]))
        o["text"] = b["obj"]["caption"]["text"]
        return o
    o["text"] = b["text"]
    if b["rich"] != b["text"]:
        o["text_rich"] = b["rich"]
    o["size"] = b["size"]
    if b.get("bold"):
        o["bold"] = True
    for k in ("level", "number", "title", "continues", "continued_by", "caption_kind", "caption_number",
              "eq_numbers", "inferred", "runin_label", "ocr_conf", "lead"):
        if b.get(k) not in (None, [], ""):
            o[k] = b[k]
    o["lines"] = [{"text": ln["plain"], "bbox": _bb(ln)} for ln in b["lines"]]
    return o

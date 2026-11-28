"""Writers: document.json, document.md, tables, crops, previews, QA report."""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
from collections import Counter

from . import backends as B

TYPE_COLORS = {
    "title": (200, 0, 0), "heading": (220, 40, 40), "front_matter": (150, 80, 0), "abstract": (0, 120, 200),
    "paragraph": (0, 90, 220), "list_item": (0, 140, 160), "equation": (150, 0, 200), "caption": (255, 130, 0),
    "table": (0, 160, 0), "figure": (0, 160, 120), "footnote": (120, 120, 120), "reference": (90, 60, 160),
    "keywords": (0, 120, 200), "empty": (180, 180, 180),
}


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------

def crop_region(pdf, page, bbox, dpi, out_png, pad=4.0):
    """Render a page region; with PyMuPDF the PDF is opened once and kept open (backends.open_cached)."""
    return B.render_clip_png(pdf, page, bbox, dpi, out_png, pad=pad)


def preview_pages(pdf, jobs, dpi=80):
    """Render pages with numbered block boxes (reading order). jobs: [(page, boxes, out_png)].
    Opens the PDF once for all pages (a separate handle: drawing must not touch the cached one)."""
    done = []
    fitz = B._import_pymupdf()
    if fitz:
        try:
            doc = fitz.open(pdf)
            if getattr(doc, "needs_pass", False):
                doc.authenticate("")
            try:
                for page, boxes, out_png in jobs:
                    pg = doc[page - 1]
                    for k, (bb, typ, label) in enumerate(boxes):
                        col = tuple(c / 255.0 for c in TYPE_COLORS.get(typ, (0, 0, 0)))
                        r = fitz.Rect(*bb)
                        pg.draw_rect(r, color=col, width=0.8)
                        pg.insert_text(fitz.Point(r.x0 + 1, r.y0 + 7), label, fontsize=7, color=col)
                    pg.get_pixmap(dpi=dpi).save(out_png)
                    done.append(out_png)
            finally:
                doc.close()
            return done
        except Exception:
            done = []
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:
        return done
    s = dpi / 72.0
    for page, boxes, out_png in jobs:
        tmp = out_png[:-4] + "_raw.png"
        if not B.render_page_png(pdf, page, dpi, tmp):
            continue
        im = Image.open(tmp).convert("RGB")
        dr = ImageDraw.Draw(im)
        for bb, typ, label in boxes:
            col = TYPE_COLORS.get(typ, (0, 0, 0))
            dr.rectangle([bb[0] * s, bb[1] * s, bb[2] * s, bb[3] * s], outline=col, width=1)
            dr.text((bb[0] * s + 1, bb[1] * s), label, fill=col)
        im.save(out_png)
        try:
            os.remove(tmp)
        except OSError:
            pass
        done.append(out_png)
    return done


def preview_page(pdf, page, boxes, out_png, dpi=80):
    """Single-page form of preview_pages (kept for callers of the old API)."""
    return bool(preview_pages(pdf, [(page, boxes, out_png)], dpi))


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------

_TAG = re.compile(r"</?(sup|sub)>")


def md_escape(t):
    parts = re.split(r"(</?(?:sup|sub)>)", t)
    out = []
    for p in parts:
        if _TAG.fullmatch(p or ""):
            out.append(p)
        else:
            p = p.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")
            p = p.replace("<", "&lt;").replace(">", "&gt;")
            out.append(p)
    s = "".join(out)
    return re.sub(r"^(#|>|\d+\.\s|[-+]\s)", lambda m: "\\" + m.group(0), s)


def _anchor(b, extra=""):
    bb = ",".join("%.0f" % v for v in b["bbox"])
    return "<!-- %s p%d [%s] %s%s -->" % (b["id"], b["page"], bb, b["type"], (" " + extra) if extra else "")


def _merge_text(a, b):
    if re.search(r"[A-Za-z]-$", a) and re.match(r"^[a-z]", b):
        return a[:-1] + b
    if a and b and re.match(r"[一-鿿]", a[-1]) and re.match(r"[一-鿿]", b[0]):
        return a + b
    return a + " " + b


def render_markdown(doc, rel_crops):
    blocks = doc["blocks"]
    byid = {b["id"]: b for b in blocks}
    tables = {t["block"]: t for t in doc["tables"] if t.get("block")}
    figs = {f["block"]: f for f in doc["figures"] if f.get("block")}
    eqs = {e["block"]: e for e in doc["equations"]}
    out = []
    src = doc["source"]
    out.append("<!-- parsed by pdfdoc: %s | sha256 %s | pages %s | backend %s -->" % (
        os.path.basename(src["file"]), src["sha256"][:16], _pages(src.get("pages_parsed", [])), doc["engine"]["backend"]))
    out.append("<!-- every block is preceded by an anchor comment: block-id page [x0,y0,x1,y1] type; "
               "coordinates are PDF points from the top-left corner -->")
    done = set()
    refs_written = False
    page = None
    for b in blocks:
        if b["id"] in done:
            continue
        if b["page"] != page:
            page = b["page"]
            out.append("\n<!-- ===== page %d ===== -->" % page)
        t = b["type"]
        text = b.get("text_rich") or b.get("text", "")
        if t == "title":
            out.append(_anchor(b))
            out.append("# " + md_escape(text) + "\n")
        elif t == "heading":
            lvl = min(b.get("level", 2) + 1, 6)
            out.append(_anchor(b, "level=%s" % b.get("level")))
            out.append("#" * lvl + " " + md_escape(b.get("text", "")) + "\n")
        elif t in ("paragraph", "list_item", "abstract", "front_matter", "keywords"):
            ids = [b["id"]]
            cur = b
            while cur.get("continued_by") and cur["continued_by"] in byid:
                nxt = byid[cur["continued_by"]]
                ids.append(nxt["id"])
                text = _merge_text(text, nxt.get("text_rich") or nxt.get("text", ""))
                done.add(nxt["id"])
                cur = nxt
            extra = ("continues across " + " -> ".join(ids)) if len(ids) > 1 else ""
            out.append(_anchor(b, extra))
            body = md_escape(text)
            if b.get("lead") and body.startswith(md_escape(b["lead"])):
                body = "**" + md_escape(b["lead"]) + "**" + body[len(md_escape(b["lead"])):]
            if t == "abstract" and b.get("inferred"):
                out.append("<!-- abstract inferred from layout (no 'Abstract' label) -->")
            if t == "list_item":
                body = "- " + body
            out.append(body + "\n")
        elif t == "equation":
            e = eqs.get(b["id"], {})
            num = e.get("number") or ""
            out.append(_anchor(b, "equation " + num))
            label = "**Eq. %s**" % num if num else "**Eq.**"
            line = "> %s %s" % (label, md_escape(e.get("text_rich") or text))
            crop = rel_crops.get(e.get("id"))
            if crop:
                line += "  \n> ![%s](%s)" % (e.get("id"), crop)
            out.append(line + "\n")
        elif t == "table":
            tb = tables.get(b["id"])
            out.append(_anchor(b))
            if tb:
                out.append("**%s.** %s\n" % (tb["label"], md_escape(_strip_label(tb["caption"]["text"]))))
                if tb.get("markdown"):
                    out.append(tb["markdown"] + "\n")
                files = [x for x in (tb.get("csv"), tb.get("html_file")) if x]
                if files:
                    out.append("<!-- %s: %d rows x %d cols, header rows %d; files: %s -->" % (
                        tb["id"], tb["nrows"], tb["ncols"], tb["header_rows"], ", ".join(files)))
                crop = rel_crops.get(tb["id"])
                if crop:
                    out.append("![%s](%s)\n" % (tb["id"], crop))
                if tb.get("continued_from"):
                    out.append("<!-- %s continues %s -->" % (tb["id"], tb["continued_from"]))
                if tb.get("merged"):
                    out.append("<!-- %s spans pages: merged table (%s) in %s -->" % (
                        tb["id"], " + ".join(tb["merged"]["parts"]), tb["merged"].get("csv") or "document.json"))
                for iss in tb.get("issues", [])[:4]:
                    out.append("<!-- check: %s -->" % iss)
        elif t == "figure":
            fg = figs.get(b["id"])
            out.append(_anchor(b))
            if fg:
                crop = rel_crops.get(fg["id"])
                if crop:
                    out.append("![%s](%s)\n" % (fg["id"], crop))
                out.append("**%s.** %s\n" % (fg["label"], md_escape(_strip_label(fg["caption"]["text"]))))
        elif t == "caption":
            out.append(_anchor(b))
            out.append("*" + md_escape(text) + "*\n")
        elif t == "footnote":
            out.append(_anchor(b))
            out.append("> [footnote] " + md_escape(text) + "\n")
        elif t == "reference":
            if not refs_written:
                refs_written = True
                for r in doc["references"]:
                    out.append("- %s <!-- %s p%s [%s] -->" % (md_escape(r["text"]), r["id"], r["page"],
                                                            ",".join("%.0f" % v for v in r["bbox"])))
                out.append("")
        elif t == "empty":
            continue
        else:
            out.append(_anchor(b))
            out.append(md_escape(text) + "\n")
    return "\n".join(out) + "\n"


def _strip_label(t):
    return re.sub(r"^(Supplementary\s+|Extended\s+Data\s+)?(Table|TABLE|Tab\.|Figure|FIGURE|Fig\.?|FIG\.|Scheme|"
                  r"Algorithm|Chart|图|表)\s*[\w.\-]+\s*[.:：|]?\s*", "", t, count=1)


def _pages(ps):
    if not ps:
        return "-"
    return "%d-%d" % (min(ps), max(ps)) if len(ps) > 1 else str(ps[0])


# --------------------------------------------------------------------------
# QA
# --------------------------------------------------------------------------

def _tokens(text):
    text = B.normalize_text(text)
    text = re.sub(r"(?<=[A-Za-z])-\s*", "", text)  # hyphenation is normalised away on both sides
    return [t for t in re.findall(r"[A-Za-z]{3,}|\d+(?:\.\d+)?|[一-鿿]", text)]


def _runs(pages):
    """Contiguous page runs: [1, 2, 3, 7, 9, 10] -> [(1, 3), (7, 7), (9, 10)]."""
    ps = sorted(set(pages))
    out = []
    for p in ps:
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def reference_text(pdf, pages, backend):
    """Independent extraction used only for cross-checking completeness (same pages as the parse)."""
    if backend != "poppler" and B._which("pdftotext"):
        parts, ok = [], True
        for a, b in _runs(pages):
            res = subprocess.run(["pdftotext", "-f", str(a), "-l", str(b), pdf, "-"], capture_output=True, timeout=180)
            if res.returncode != 0:
                ok = False
                break
            parts.append(res.stdout.decode("utf-8", "replace"))
        if ok:
            return "\n".join(parts), "pdftotext"
    fitz = B._import_pymupdf()
    if backend != "pymupdf" and fitz:
        try:
            doc = fitz.open(pdf)
            if getattr(doc, "needs_pass", False):
                doc.authenticate("")
            try:
                txt = "\n".join(doc[p - 1].get_text() for p in pages)
            finally:
                doc.close()
            return txt, "pymupdf"
        except Exception:
            pass
    pp = B._import("pypdf")
    if pp and backend != "pypdf":
        r = pp.PdfReader(pdf)
        return "\n".join((r.pages[p - 1].extract_text() or "") for p in pages), "pypdf"
    return None, None


def qa_report(doc, pdf):
    qa = {"checks": [], "status": "success"}
    blocks = doc["blocks"]
    # 1. completeness against an independent extractor
    ours = " ".join([b.get("text", "") for b in blocks] +
                    [" ".join(c["text"] for g in t.get("grid", []) for c in g["cells"]) for t in doc["tables"]] +
                    [" ".join(f.get("text_inside", [])) for f in doc["figures"]] +
                    [f["text"] for f in doc["furniture"]] + [r["text"] for r in doc["references"]] +
                    [t["caption"]["text"] for t in doc["tables"]])
    if doc["source"]["kind"] == "pdf":
        ref, refname = reference_text(pdf, doc["source"]["pages_parsed"], doc["engine"]["backend"])
    else:
        ref, refname = None, None
    if ref and len(_tokens(ref)) < 50:
        qa["checks"].append({"check": "completeness vs independent extractor (%s)" % refname,
                             "value": "skipped: reference extractor found almost no text (scanned/OCR document)",
                             "ok": True})
        ref = None
    if ref:
        a = Counter(_tokens(ref))
        o = Counter(_tokens(ours))
        tot = sum(a.values())
        hit = sum(min(v, o.get(k, 0)) for k, v in a.items())
        missing = [(k, v - o.get(k, 0)) for k, v in a.items() if v > o.get(k, 0)]
        missing.sort(key=lambda kv: -kv[1])
        recall = hit / float(tot) if tot else 1.0
        qa["token_recall_vs_" + refname] = round(recall, 4)
        qa["checks"].append({"check": "completeness vs independent extractor (%s)" % refname,
                             "value": "%.2f%% of %d reference tokens found" % (100 * recall, tot),
                             "ok": recall >= 0.97,
                             "missing_examples": [k for k, _ in missing[:25]]})
    # 2. caption numbering continuity
    for kind, items in (("table", doc["tables"]), ("figure", [f for f in doc["figures"] if f["kind"] == "figure"])):
        nums = sorted(set(int(re.sub(r"\D", "", str(x["number"])) or 0) for x in items
                          if re.fullmatch(r"\d+", str(x["number"]))))
        if nums:
            gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in nums]
            qa["checks"].append({"check": "%s numbering" % kind, "value": "%s %s" % (kind, _pages(nums)),
                                 "ok": not gaps, "missing_numbers": gaps})
    # 3. equation numbering continuity
    eqn = []
    for e in doc["equations"]:
        for m in re.findall(r"\((\d+)\)", e.get("number") or ""):
            eqn.append(int(m))
    if eqn:
        gaps = [n for n in range(min(eqn), max(eqn) + 1) if n not in eqn]
        qa["checks"].append({"check": "equation numbering", "value": "%d numbered display equations" % len(eqn),
                             "ok": not gaps, "missing_numbers": gaps[:50],
                             "note": "missing numbers may be equations rendered as images or merged with text"})
    # 4. numbered references
    labels = [int(re.sub(r"\D", "", r["label"])) for r in doc["references"] if re.fullmatch(r"\[?\d+\]?\.?", r["label"] or "")]
    if labels and doc["references"][0].get("split_rule") == "numbered labels":
        gaps = [n for n in range(1, max(labels) + 1) if n not in labels]
        qa["checks"].append({"check": "reference numbering", "value": "%d entries" % len(labels),
                             "ok": not gaps, "missing_numbers": gaps[:50]})
    elif doc["references"]:
        qa["checks"].append({"check": "reference split", "value": "%d entries (split by hanging indent)" % len(doc["references"]),
                             "ok": True})
    # 5. open paragraph ends (possible order problems)
    suspicious = []
    for i, b in enumerate(blocks):
        if b["type"] in ("paragraph", "list_item") and not b.get("continued_by"):
            t = re.sub(r"(?<=[.!?。！？”\"])\s*[\d*†‡§]{1,3}$", "", b.get("text", "").rstrip())
            if t and not re.search(r"[.!?。！？:;)\]\"”]$", t) and len(t) > 60:
                nxt = blocks[i + 1]["type"] if i + 1 < len(blocks) else None
                if nxt not in ("equation",):
                    suspicious.append({"block": b["id"], "page": b["page"], "ends_with": t[-40:]})
    qa["checks"].append({"check": "paragraphs ending mid-sentence without a detected continuation",
                         "value": len(suspicious), "ok": len(suspicious) <= 3, "blocks": suspicious[:15],
                         "note": "usually text before a display equation, a float, or a reading-order problem"})
    # 6. text layer / OCR
    for p in doc["pages"]:
        if p.get("status") not in ("text", "ocr"):
            qa["checks"].append({"check": "page %d text layer" % p["page"], "value": p.get("status"), "ok": False})
        if p.get("bad_chars"):
            qa["checks"].append({"check": "page %d undecodable glyphs" % p["page"], "value": p["bad_chars"],
                                 "ok": p["bad_chars"] < 5})
    if doc.get("ocr") and doc["ocr"].get("pages"):
        for p in doc["ocr"]["pages"]:
            qa["checks"].append({"check": "page %d OCR confidence" % p["page"], "value": p["mean_conf"],
                                 "ok": p["mean_conf"] >= 80, "low_conf_words": len(p["low_conf_words"])})
    # 7. tables
    for t in doc["tables"]:
        if t.get("issues"):
            qa["checks"].append({"check": "%s structure" % t["id"], "value": "%dx%d" % (t["nrows"], t["ncols"]),
                                 "ok": not t.get("image_only"), "issues": t["issues"][:6]})
    bad = [c for c in qa["checks"] if not c["ok"]]
    if any(p.get("status", "").endswith("unreadable") or p.get("status") in ("no_text_layer", "garbled_text_layer")
           for p in doc["pages"]):
        qa["status"] = "partial"
    elif bad:
        qa["status"] = "success_with_warnings"
    qa["failed_checks"] = len(bad)
    return qa


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------

def write_outputs(doc, pdf, outdir, previews=True, crops=True, max_preview_pages=30, log=None):
    log = log or (lambda *a: None)
    os.makedirs(outdir, exist_ok=True)
    tdir = os.path.join(outdir, "tables")
    cdir = os.path.join(outdir, "crops")
    pdir = os.path.join(outdir, "previews")
    is_pdf = doc["source"]["kind"] == "pdf"
    rel_crops = {}
    # tables
    if doc["tables"]:
        os.makedirs(tdir, exist_ok=True)
    for t in doc["tables"]:
        if t.get("grid"):
            csvp = os.path.join(tdir, t["id"] + ".csv")
            with open(csvp, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if t["header_rows"]:
                    w.writerow(t["header"])
                for row in t["matrix"][t["header_rows"]:]:
                    w.writerow(row)
            with open(os.path.join(tdir, t["id"] + ".html"), "w", encoding="utf-8") as fh:
                fh.write("<!-- %s page %d bbox %s -->\n<p>%s</p>\n%s\n" % (t["id"], t["page"], t["bbox"],
                                                                           t["caption"]["text"], t["html"]))
            with open(os.path.join(tdir, t["id"] + ".md"), "w", encoding="utf-8") as fh:
                fh.write("**%s** (page %d)\n\n%s\n" % (t["caption"]["text"], t["page"], t["markdown"]))
            t["csv"] = "tables/%s.csv" % t["id"]
            t["html_file"] = "tables/%s.html" % t["id"]
        if t.get("merged"):
            m = t["merged"]
            base = t["id"] + "-merged"
            with open(os.path.join(tdir, base + ".csv"), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if m["header_rows"]:
                    w.writerow(t["header"])
                for row in m["matrix"][m["header_rows"]:]:
                    w.writerow(row)
            with open(os.path.join(tdir, base + ".md"), "w", encoding="utf-8") as fh:
                fh.write("**%s** (pages %s; merged from %s)\n\n%s\n" % (
                    t["caption"]["text"], ", ".join(str(p) for p in sorted(set(
                        [t["page"]] + [x["page"] for x in doc["tables"] if x["id"] in m["parts"]]))),
                    ", ".join(m["parts"]), m["markdown"]))
            m["csv"] = "tables/%s.csv" % base
    # crops
    if crops and is_pdf:
        os.makedirs(cdir, exist_ok=True)
        def tbox(t):
            if t.get("image_bbox"):
                c, i = t["caption"]["bbox"], t["image_bbox"]
                return [min(c[0], i[0]), min(c[1], i[1]), max(c[2], i[2]), max(c[3], i[3])]
            return t["bbox"] if not t.get("image_only") else t["caption"]["bbox"]
        jobs = [(t["id"], t["page"], tbox(t)) for t in doc["tables"]]
        jobs += [(f["id"], f["page"], f["bbox"]) for f in doc["figures"][:40]]
        jobs += [(e["id"], e["page"], e["bbox"]) for e in doc["equations"][:60]]
        for k, (oid, page, bb) in enumerate(jobs):
            outp = os.path.join(cdir, oid + ".png")
            if crop_region(pdf, page, bb, 150 if not oid.startswith("eq") else 200, outp):
                rel_crops[oid] = "crops/%s.png" % oid
    for coll in (doc["tables"], doc["figures"], doc["equations"]):
        for o in coll:
            if o["id"] in rel_crops:
                o["crop"] = rel_crops[o["id"]]
    # previews
    prev_files = []
    if previews and is_pdf:
        os.makedirs(pdir, exist_ok=True)
        bypage = {}
        for b in doc["blocks"]:
            bypage.setdefault(b["page"], []).append(b)
        for f in doc["furniture"]:
            bypage.setdefault(f["page"], [])
        pjobs = []
        for k, page in enumerate(sorted(bypage)[:max_preview_pages]):
            boxes = [(b["bbox"], b["type"], "%d" % i) for i, b in enumerate(bypage[page])]
            boxes += [(f["bbox"], "empty", "x") for f in doc["furniture"] if f["page"] == page]
            pjobs.append((page, boxes, os.path.join(pdir, "page-%03d.png" % page)))
        for outp in preview_pages(pdf, pjobs):
            prev_files.append("previews/" + os.path.basename(outp))
    if is_pdf:
        B.close_cached(pdf)
    doc["previews"] = prev_files
    # QA
    doc["qa"] = qa_report(doc, pdf)
    doc["_json_path"] = os.path.abspath(os.path.join(outdir, "document.json"))
    doc["_md_path"] = os.path.abspath(os.path.join(outdir, "document.md"))
    doc["_report_path"] = os.path.abspath(os.path.join(outdir, "parse_report.md"))
    # markdown + json
    md = render_markdown(doc, rel_crops)
    with open(os.path.join(outdir, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(os.path.join(outdir, "document.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "parse_report.md"), "w", encoding="utf-8") as fh:
        fh.write(report_markdown(doc))
    return {"document_json": os.path.join(outdir, "document.json"),
            "document_md": os.path.join(outdir, "document.md"),
            "parse_report": os.path.join(outdir, "parse_report.md"),
            "tables_dir": tdir if doc["tables"] else None, "crops": len(rel_crops), "previews": len(prev_files)}


def report_markdown(doc):
    q = doc["qa"]
    src = doc["source"]
    L = ["# Parse report", "",
         "- file: `%s` (sha256 `%s`)" % (os.path.basename(src["file"]), src["sha256"]),
         "- pages parsed: %s of %s; backend: %s; elapsed %.1fs" % (_pages(src.get("pages_parsed", [])), src.get("pages_total"),
                                                                  doc["engine"]["backend"], doc["engine"].get("elapsed_s", 0)),
         "- overall: **%s** (%d failed checks)" % (q["status"], q["failed_checks"]), "",
         "## Pages", "", "| page | status | columns | source | chars |", "|---|---|---|---|---|"]
    for p in doc["pages"]:
        L.append("| %s | %s | %s | %s | %s |" % (p["page"], p.get("status"), p.get("columns"), p.get("source"),
                                                 p.get("text_chars", "")))
    L += ["", "## Structure", "",
          "- sections: %d, paragraphs: %d, tables: %d, figures: %d, equations: %d, footnotes: %d, references: %d" % (
              len(doc["sections"]), sum(1 for b in doc["blocks"] if b["type"] == "paragraph"), len(doc["tables"]),
              len(doc["figures"]), len(doc["equations"]), len(doc["footnotes"]), len(doc["references"])),
          "- page furniture removed from the text flow: %d (running headers/footers, page numbers, margin stamps, line numbers)" % len(doc["furniture"]),
          "- de-hyphenated line breaks: %d; paragraphs joined across columns/pages: %d; ligatures expanded: %s" % (
              len(doc["changes"]["dehyphenation"]), len(doc["changes"]["merged_paragraphs"]), doc["changes"]["ligatures_expanded"]),
          "- symbol-font glyphs remapped (Advent pi fonts: þ→+ ¼→= ð→( Þ→) ½→[ Š→]): %d" % doc["changes"].get("pi_font_glyphs_remapped", 0),
          "", "## Checks", ""]
    for c in q["checks"]:
        mark = "OK " if c["ok"] else "CHECK"
        extra = ""
        for k in ("missing_numbers", "missing_examples", "issues", "blocks"):
            if c.get(k):
                extra = " — %s: %s" % (k, json.dumps(c[k][:8], ensure_ascii=False))
                break
        L.append("- [%s] %s: %s%s" % (mark, c["check"], c["value"], extra))
    if doc["warnings"]:
        L += ["", "## Warnings", ""] + ["- " + w for w in doc["warnings"]]
    L += ["", "## Outline", ""]
    for s in doc["sections"]:
        L.append("%s- %s %s (p.%d, %s)" % ("  " * (s["level"] - 1), s.get("number") or "", s["title"], s["page"], s["block"]))
    return "\n".join(L) + "\n"

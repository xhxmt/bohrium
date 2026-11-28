#!/usr/bin/env python3
"""Look things up in parsed documents (document.json) with exact locators.

  python3 query.py docs                        # list the parsed documents
  python3 query.py overview                    # one call: title/authors/abstract, sections, tables, figures, equations, footnotes, checks
  python3 query.py outline                     # section tree with block ids
  python3 query.py search "words|regex" [--type paragraph,table,...] [--max 15] [--literal]   # at most --max hits
  python3 query.py show p3-b12 [--context 1]   # full text + lines + bbox (+ neighbours); also table-2, eq-3, ref-12
  python3 query.py page 3                      # reading order of one page
  python3 query.py section 2.1                 # all blocks of a section (by number, sec-id or title prefix)
  python3 query.py table 2 [--csv]             # one table (table-2 / 2 / "Table 2" / 表2) with locator
  python3 query.py tables | figures | equations | footnotes | furniture | refs [label]
  python3 query.py checks                      # QA checks and warnings
  python3 query.py ocr 3 --bbox 60,330,300,560  # re-OCR a page region (PDF pt); [--dpi 300] [--lang eng] [--psm 6]
Options: --doc NAME|PATH  pick one document (file name, stem or document.json path).
Default: every document of the current run (/app/work/pdfdoc_state.json), else the newest document.json.
Search patterns are regular expressions (case-insensitive); an invalid pattern, or --literal,
is searched as plain text.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

STATE = "/app/work/pdfdoc_state.json"


def _candidates():
    if os.path.isfile(STATE):
        try:
            st = json.load(open(STATE, encoding="utf-8"))
            docs = [p for p in st.get("docs", []) if os.path.isfile(p)]
            if docs:
                return docs
        except Exception:
            pass
    cands = glob.glob("/app/output/parsed/**/document.json", recursive=True)
    if not cands:
        cands = glob.glob("/app/output/**/document.json", recursive=True) + glob.glob("**/document.json", recursive=True)
    return sorted(set(cands), key=lambda p: -os.path.getmtime(p))


def find_docs(sel):
    """Paths of the documents to query."""
    if sel and os.path.isfile(sel) and sel.endswith(".json"):
        return [sel]
    cands = _candidates()
    if not cands:
        sys.exit("no document.json found; run run_case.py or parse_pdf.py first (or pass --doc PATH)")
    if sel:
        hits = []
        for p in cands:
            try:
                src = json.load(open(p, encoding="utf-8"))["source"]["file"]
            except Exception:
                src = p
            names = {os.path.basename(src), os.path.splitext(os.path.basename(src))[0], p,
                     os.path.basename(os.path.dirname(p))}
            if sel in names or os.path.abspath(sel) == os.path.abspath(src):
                hits.append(p)
        if not hits:
            sys.exit("--doc %s matches none of: %s" % (sel, ", ".join(cands)))
        return hits
    if os.path.isfile(STATE):
        return cands  # all documents of this run
    return cands[:1]


def loc(b):
    return "[%s p%d bbox=%s %s]" % (b["id"], b["page"], ",".join("%.0f" % v for v in b["bbox"]), b.get("type", ""))


def compile_pattern(pat, literal=False):
    if literal:
        return re.compile(re.escape(pat), re.I), "literal"
    try:
        rx = re.compile(pat, re.I)
    except re.error as e:
        return re.compile(re.escape(pat), re.I), "literal (invalid regex: %s)" % e
    # brackets/parentheses in an otherwise plain phrase ("BERT (Large)", "[12]") are meant literally
    if re.search(r"[()\[\]]", pat) and not re.search(r"[|*+?\\{}^$]|\.\*", pat):
        return re.compile(re.escape(pat), re.I), "literal (brackets taken as text)"
    return rx, "regex"


def table_match(t, arg):
    if not arg:
        return True
    a = arg.strip()
    num = re.sub(r"^(table|tab\.?|表)\s*", "", a, flags=re.I).strip()
    return a in (t["id"], t.get("label")) or num == str(t.get("number")) or a == "table-%s" % t.get("number")


def run(cmd, a, d, path, multi):
    blocks = d["blocks"]
    idx = {b["id"]: i for i, b in enumerate(blocks)}
    print("# %s (%s)" % (os.path.basename(d["source"]["file"]), path))
    c = a.cmd
    n = 0
    if c == "docs":
        s = d["source"]
        print("pages %s of %s; %d blocks, %d sections, %d tables, %d figures, %d equations, %d references; title: %s" % (
            s.get("pages_parsed"), s.get("pages_total"), len(blocks), len(d["sections"]), len(d["tables"]),
            len(d["figures"]), len(d["equations"]), len(d["references"]), d["metadata"].get("title")))
    elif c == "outline":
        m = d["metadata"]
        print("title: %s [%s]" % (m["title"], m["title_block"]))
        for s in d["sections"]:
            print("%s%s %s  [p%d %s]" % ("  " * (s["level"] - 1), s.get("number") or "-", s["title"], s["page"], s["block"]))
    elif c == "search":
        if not a.arg:
            sys.exit("search needs a pattern")
        rx, how = compile_pattern(a.arg, a.literal)
        if how != "regex":
            print("(searching %s)" % how)
        types = set(a.type.split(",")) if a.type else None
        hits = []  # (kind, line); paragraphs first, then table rows, figure text, references
        for b in blocks:
            if types and b["type"] not in types:
                continue
            t = b.get("text", "")
            m = rx.search(t)
            if m:
                s0 = max(0, m.start() - 120)
                hits.append("%s §%s: ...%s..." % (loc(b), b.get("section") or "-", t[s0:m.end() + 160].replace("\n", " ")))
        for t in d["tables"]:
            if types and "table" not in types:
                continue
            for ri, g in enumerate(t.get("grid", [])):
                row = " | ".join(x["text"] for x in g["cells"])
                if rx.search(row):
                    hits.append("[%s p%d row %d bbox=%s table] %s" % (
                        t["id"], t["page"], ri, ",".join("%.0f" % v for v in g["bbox"]), row[:300]))
        for f in d["figures"]:
            if (not types or "figure" in types) and f.get("text_inside") and rx.search(" ".join(f["text_inside"])):
                hits.append("[%s p%d figure text] %s" % (f["id"], f["page"], " ".join(f["text_inside"])[:200]))
        for r in d["references"]:
            if (not types or "reference" in types) and rx.search(r["text"]):
                hits.append("[%s p%d reference] %s" % (r["id"], r["page"], r["text"][:200]))
        for h in hits[:a.max]:
            print(h)
        n = len(hits)
        if n > a.max:
            print("... %d more matches (showing %d): narrow the pattern, add --type, or raise --max" % (n - a.max, a.max))
        if n == 0:
            print("no match")
    elif c == "show":
        bid = a.arg
        if bid not in idx:
            obj = None
            for coll in ("tables", "figures", "equations", "references", "sections", "footnotes"):
                obj = next((o for o in d.get(coll, []) if o.get("id") == bid), None)
                if obj:
                    break
            if obj and obj.get("block") in idx:
                print("(%s is block %s)" % (bid, obj["block"]))
                bid = obj["block"]
            else:
                if not multi:
                    sys.exit("unknown block id %s" % a.arg)
                print("(no block %s in this document)" % a.arg)
                return 0
        i = idx[bid]
        n = 1
        for j in range(max(0, i - a.context), min(len(blocks), i + a.context + 1)):
            b = blocks[j]
            print(("> " if j == i else "  ") + loc(b) + " section=%s" % b.get("section"))
            print(b.get("text_rich") or b.get("text", ""))
            if j == i:
                for ln in b.get("lines", []):
                    print("    line bbox=%s: %s" % (",".join("%.0f" % v for v in ln["bbox"]), ln["text"]))
                for k in ("continues", "continued_by", "level", "number", "eq_numbers", "lead", "ref"):
                    if b.get(k):
                        print("    %s: %s" % (k, b[k]))
            print()
    elif c == "page":
        p = int(a.arg)
        for b in blocks:
            if b["page"] == p:
                print("%s %s" % (loc(b), (b.get("text") or "")[:110]))
                n += 1
        fur = [f for f in d["furniture"] if f["page"] == p]
        if fur:
            print("removed page furniture: " + "; ".join("%s '%s'" % (f["role"], f["text"][:40]) for f in fur))
    elif c == "section":
        arg = (a.arg or "").strip()
        sec = next((s for s in d["sections"] if s["id"] == arg or (s.get("number") or "") == arg.rstrip(".")
                    or s["title"].lower().startswith(arg.lower())), None)
        if not sec:
            if not multi:
                sys.exit("section not found")
            print("(section not found in this document)")
            return 0
        ids = {sec["id"]}
        grew = True
        while grew:  # all descendants
            new = {s["id"] for s in d["sections"] if s.get("parent") in ids} - ids
            grew = bool(new)
            ids |= new
        budget = 12000 if not a.all else 10 ** 9
        for b in blocks:
            if b.get("section") in ids:
                line = "%s %s" % (loc(b), (b.get("text") or "")[:400])
                budget -= len(line)
                if budget < 0:
                    print("... section continues: use page N / show BLOCK, or --all")
                    break
                print(line)
                n += 1
    elif c in ("table", "tables"):
        for t in d["tables"]:
            if c == "table" and not table_match(t, a.arg):
                continue
            n += 1
            print("%s %s p%d bbox=%s %dx%d header_rows=%d crop=%s csv=%s" % (t["id"], t["label"], t["page"], t["bbox"],
                  t["nrows"], t["ncols"], t["header_rows"], t.get("crop"), t.get("csv")))
            print("caption: " + t["caption"]["text"])
            for k in ("continued_from", "continued_in", "body_source"):
                if t.get(k):
                    print("%s: %s" % (k, t[k]))
            if c == "table":
                if a.csv:
                    print(",".join(t.get("header", [])))
                    for r in t.get("matrix", [])[t["header_rows"]:]:
                        print(",".join('"%s"' % x if "," in x else x for x in r))
                elif t.get("merged") and t["merged"].get("markdown"):
                    print("table continued across pages: the merged table (parts %s, %d rows) is shown; this part "
                          "alone is in %s" % (", ".join(t["merged"]["parts"]), t["merged"].get("nrows") or 0, t.get("csv")))
                    print(t["merged"]["markdown"])
                else:
                    print(t.get("markdown") or "(no table body in the text layer: see the crop / page preview)")
                if t.get("merged"):
                    print("merged across pages (%s): %s" % (", ".join(t["merged"]["parts"]), t["merged"].get("csv")))
                for iss in t.get("issues", []):
                    print("issue: " + iss)
        if c == "table" and not n and not multi:
            sys.exit("table %s not found (tables: %s)" % (a.arg, ", ".join(t["id"] for t in d["tables"])))
    elif c in ("figures", "figure"):
        for f in d["figures"]:
            if c == "figure" and f["id"] != a.arg and f["number"] != a.arg:
                continue
            print("%s p%d bbox=%s crop=%s: %s" % (f["id"], f["page"], f["bbox"], f.get("crop"), f["caption"]["text"][:300]))
    elif c in ("equations", "eq"):
        for e in d["equations"]:
            print("%s %s p%d bbox=%s crop=%s: %s" % (e["id"], e.get("number") or "", e["page"], e["bbox"], e.get("crop"), e["text"]))
    elif c == "footnotes":
        for f in d["footnotes"]:
            print("[%s p%d] %s" % (f["id"], f["page"], f["text"]))
    elif c == "furniture":
        for f in d["furniture"]:
            print("p%d %s %s: %s" % (f["page"], f["role"], f["bbox"], f["text"]))
    elif c == "refs":
        shown = 0
        for r in d["references"]:
            if a.arg and a.arg not in (r["label"], r["id"], r["label"].strip("[]."), r["label"].strip("[]")):
                continue
            if shown >= 60 and not a.all and not a.arg:
                print("... %d more references: refs LABEL for one, or --all" % (len(d["references"]) - shown))
                break
            print("%s %s p%d: %s" % (r["id"], r["label"], r["page"], r["text"]))
            shown += 1
    elif c == "ocr":
        n = ocr_region(a, d)
    elif c == "overview":
        # everything an extraction task usually needs, in one call (saves model turns)
        m, src = d["metadata"], d["source"]
        print("pages %s of %s | %d blocks | %d sections | %d tables | %d figures | %d equations | %d footnotes | %d references" % (
            src.get("pages_parsed") and "%d-%d" % (min(src["pages_parsed"]), max(src["pages_parsed"])), src.get("pages_total"),
            len(blocks), len(d["sections"]), len(d["tables"]), sum(1 for f in d["figures"] if f.get("kind") == "figure"),
            len(d["equations"]), len(d["footnotes"]), len(d["references"])))
        print("title: %s [%s]" % (m.get("title"), m.get("title_block")))
        if m.get("authors"):
            for au in m["authors"]:
                print("author: %s%s [p%d %s bbox=%s] affiliation=%s%s email=%s" % (
                    au["name"], "".join(au.get("markers") or []), au["page"], au["block"], au["bbox"], au.get("affiliation"),
                    " (%s)" % au["affiliation_rule"] if au.get("affiliation_rule") else "", au.get("email")))
            for af in m.get("affiliations") or []:
                print("affiliation: %s%s [p%d %s]" % ("(%s) " % af["marker"] if af.get("marker") else "", af["text"], af["page"], af["block"]))
        else:
            for x in m.get("front_matter", []):
                print("front matter [%s]: %s" % (x["block"], x["text"][:200]))
        if m.get("abstract"):
            print("abstract %s%s: %s..." % (m.get("abstract_blocks"), " (inferred from layout)" if m.get("abstract_inferred") else "",
                                           m["abstract"][:300]))
        if m.get("keywords"):
            print("keywords: " + m["keywords"])
        print("-- sections")
        for s_ in d["sections"]:
            print("%s%s %s  [p%d %s]" % ("  " * (max(s_["level"], 1) - 1), s_.get("number") or "-", s_["title"], s_["page"], s_["block"]))
        print("-- tables")
        for t in d["tables"]:
            print("%s %s p%d %dx%d hdr=%d%s%s: %s%s" % (t["id"], t["label"], t["page"], t["nrows"], t["ncols"], t["header_rows"],
                  " IMAGE-ONLY" if t.get("image_only") else "", " merged:%s" % ",".join(t["merged"]["parts"]) if t.get("merged") else "",
                  t["caption"]["text"][:110], " | issues: %d" % len(t["issues"]) if t.get("issues") else ""))
        print("-- figures")
        for f in d["figures"]:
            print("%s p%d: %s" % (f["id"], f["page"], f["caption"]["text"][:110]))
        print("-- equations")
        for e in d["equations"]:
            print("%s %s p%d: %s" % (e["id"], e.get("number") or "(unnumbered)", e["page"], e["text"][:90]))
        print("-- footnotes")
        for f in d["footnotes"][:40]:
            print("[%s p%d] %s" % (f["id"], f["page"], f["text"][:110]))
        if d["references"]:
            r0, r1 = d["references"][0], d["references"][-1]
            print("-- references: %d (%s p%d ... %s p%d); full list: query.py refs" % (
                len(d["references"]), r0["id"], r0["page"], r1["id"], r1["page"]))
        print("-- checks: %s" % d["qa"]["status"])
        for k in d["qa"]["checks"]:
            if not k["ok"]:
                print("[CHECK] %s: %s" % (k["check"], k["value"]))
        for w in d["warnings"][:15]:
            print("warning: " + w)
    elif c == "checks":
        print("QA status: %s" % d["qa"]["status"])
        for k in d["qa"]["checks"]:
            print("[%s] %s: %s" % ("ok" if k["ok"] else "CHECK", k["check"], k["value"]))
        for w in d["warnings"]:
            print("warning: " + w)
    else:
        sys.exit("unknown command %s" % c)
    return n


def ocr_region(a, d):
    """Re-OCR one page or one region of it; prints lines with bbox in PDF pt (same frame as the parse).
    Tesseract runs single-threaded with a time limit, so this never stalls the way a bare
    `tesseract` call can inside a CPU-limited container."""
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pdfdoc import backends as B
    if not a.arg or not a.arg.isdigit():
        sys.exit("ocr needs a page number: ocr 3 [--bbox x0,y0,x1,y1] [--dpi 300] [--lang eng] [--psm 6]")
    pno = int(a.arg)
    src = d["source"]["file"]
    if not os.path.isfile(src):
        sys.exit("source file %s not found" % src)
    if a.bbox:
        try:
            bb = [float(v) for v in a.bbox.split(",")]
            assert len(bb) == 4 and bb[2] > bb[0] and bb[3] > bb[1]
        except Exception:
            sys.exit("--bbox must be x0,y0,x1,y1 in PDF points (top-left origin), as printed by show/page")
    else:
        pg = next((p for p in d.get("pages", []) if p.get("page") == pno), None)
        bb = [0.0, 0.0, float((pg or {}).get("width") or 612), float((pg or {}).get("height") or 792)]
    dpi = max(72, min(int(a.dpi), 600))
    lang = a.lang or (d.get("ocr") or {}).get("lang") or "eng"
    if lang.startswith("per page"):
        lang = "chi_sim+eng"
    tmp = tempfile.mkdtemp(prefix="pdfdoc_ocr_")
    png = os.path.join(tmp, "region.png")
    if not B.render_clip_png(src, pno, bb, dpi, png, pad=0.0):
        sys.exit("could not render page %d region %s" % (pno, bb))
    pre, _notes = B._preprocess_for_ocr(png, os.path.join(tmp, "region_pre.png"))
    scale = 72.0 / dpi
    try:
        from PIL import Image  # type: ignore
        with Image.open(pre) as im1, Image.open(png) as im0:
            scale *= float(im0.size[0]) / im1.size[0]
    except Exception:
        pass
    try:
        lines, mean, nwords, low = B.ocr_image_to_lines(pre, scale, lang, {}, psm=a.psm)
    except Exception as e:
        sys.exit("OCR failed: %s" % e)
    print("p%d region %s dpi %d lang %s psm %d: %d words, mean conf %.1f (words < 60 marked ?)" % (
        pno, [round(v, 1) for v in bb], dpi, lang, a.psm, nwords, mean))
    for ln in lines:
        ws = ln["words"]
        x0, y0 = min(w["x0"] for w in ws) + bb[0], min(w["y0"] for w in ws) + bb[1]
        x1, y1 = max(w["x1"] for w in ws) + bb[0], max(w["y1"] for w in ws) + bb[1]
        txt = " ".join(w["t"] + ("?" if 0 <= w.get("conf", 100) < 60 else "") for w in ws)
        print("[%.1f, %.1f, %.1f, %.1f] %s" % (x0, y0, x1, y1, txt))
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--doc")
    ap.add_argument("--type")
    ap.add_argument("--max", type=int, default=15)
    ap.add_argument("--context", type=int, default=0)
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--literal", "-F", action="store_true", help="search the pattern as plain text")
    ap.add_argument("--all", action="store_true", help="no output limit for section / refs")
    ap.add_argument("--bbox", help="ocr: region x0,y0,x1,y1 in PDF points (default: whole page)")
    ap.add_argument("--dpi", type=int, default=300, help="ocr: render resolution (default 300)")
    ap.add_argument("--lang", help="ocr: tesseract language (default: the one the parse used)")
    ap.add_argument("--psm", type=int, default=6, help="ocr: tesseract page segmentation mode (6 = one block)")
    a = ap.parse_args()
    paths = find_docs(a.doc)
    multi = len(paths) > 1
    if multi and a.cmd != "docs":
        print("(%d documents; showing all — use --doc NAME for one)" % len(paths))
    total = 0
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        total += run(a.cmd, a, d, p, multi) or 0
        if multi:
            print()
    if multi and a.cmd in ("show", "table") and not total:
        sys.exit("%s %s not found in any document" % (a.cmd, a.arg))


if __name__ == "__main__":
    main()

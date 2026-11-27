#!/usr/bin/env python3
"""Parse one scientific PDF (or page image) into a verifiable structured result.

Usage:
  python3 parse_pdf.py PAPER.pdf --out OUTDIR [--pages 2-5] [--backend auto|pymupdf|pdfplumber|poppler]
                       [--ocr auto|off|force] [--ocr-lang auto|eng|chi_sim+eng] [--no-previews]
  python3 parse_pdf.py --check-env

Outputs in OUTDIR: document.json, document.md, parse_report.md, tables/, crops/, previews/.
Exit code 0 = parsed (see parse_report.md for warnings), 2 = cannot open (encrypted/damaged), 3 = no engine.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdfdoc import backends as B  # noqa: E402
from pdfdoc import export as E  # noqa: E402
from pdfdoc import structure as S  # noqa: E402


def summary_text(doc, files, max_sections=60):
    q = doc["qa"]
    src = doc["source"]
    L = []
    L.append("PARSED %s | pages %s/%s | backend %s | %.1fs | status %s" % (
        os.path.basename(src["file"]), E._pages(src.get("pages_parsed", [])), src.get("pages_total"),
        doc["engine"]["backend"], doc["engine"].get("elapsed_s", 0), q["status"]))
    cols = sorted(set(p.get("columns") for p in doc["pages"] if p.get("columns")))
    L.append("layout: columns per page %s; furniture removed %d; body font %.1fpt" % (
        cols, len(doc["furniture"]), doc["engine"].get("body_font_size", 0)))
    m = doc["metadata"]
    if m.get("title"):
        L.append("title [%s]: %s" % (m["title_block"], m["title"][:200]))
    if m.get("abstract"):
        L.append("abstract [%s]%s: %s..." % (",".join(m["abstract_blocks"]), " (inferred)" if m.get("abstract_inferred") else "",
                                           m["abstract"][:240]))
    L.append("sections (%d):" % len(doc["sections"]))
    for s in doc["sections"][:max_sections]:
        L.append("  %s%s %s  [p%d %s]" % ("  " * (s["level"] - 1), s.get("number") or "-", s["title"][:80], s["page"], s["block"]))
    if len(doc["sections"]) > max_sections:
        L.append("  ... %d more" % (len(doc["sections"]) - max_sections))
    L.append("tables (%d):" % len(doc["tables"]))
    for t in doc["tables"]:
        flags = ("IMAGE? " if t.get("image_only") else "") + ("OCR-read " if t.get("body_source") else "") + \
            ("continues %s " % t["continued_from"] if t.get("continued_from") else "") + \
            ("merged %s " % "+".join(t["merged"]["parts"]) if t.get("merged") else "") + \
            ("%d repairs " % len([i for i in t.get("issues", []) if i.startswith("repaired")]) if any(
                i.startswith("repaired") for i in t.get("issues", [])) else "")
        L.append("  %s p%d %dx%d hdr=%d %s| %s" % (t["id"], t["page"], t["nrows"], t["ncols"], t["header_rows"],
                                                  flags, t["caption"]["text"][:90] or t["label"]))
    L.append("figures (%d): %s" % (len(doc["figures"]), ", ".join("%s p%d" % (f["id"], f["page"]) for f in doc["figures"][:30])))
    nums = [e["number"] for e in doc["equations"] if e.get("number")]
    L.append("equations: %d display (%d numbered: %s)" % (len(doc["equations"]), len(nums), " ".join(nums[:30])))
    L.append("footnotes: %d | references: %d | paragraphs joined across columns/pages: %d | de-hyphenations: %d" % (
        len(doc["footnotes"]), len(doc["references"]), len(doc["changes"]["merged_paragraphs"]),
        len(doc["changes"]["dehyphenation"])))
    for c in q["checks"]:
        if not c["ok"]:
            L.append("CHECK: %s -> %s" % (c["check"], c["value"]))
    for w in doc["warnings"][:10]:
        L.append("WARNING: " + w)
    L.append("files: " + ", ".join("%s=%s" % (k, v) for k, v in files.items() if v))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--out", default=None)
    ap.add_argument("--pages", default=None, help="e.g. 1-3,5 (default: all)")
    ap.add_argument("--backend", default="auto")
    ap.add_argument("--ocr", default="auto", choices=["auto", "off", "force"])
    ap.add_argument("--ocr-lang", default="auto")
    ap.add_argument("--ocr-budget", type=float, default=420.0, help="seconds")
    ap.add_argument("--no-previews", action="store_true")
    ap.add_argument("--no-crops", action="store_true")
    ap.add_argument("--check-env", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.check_env or not a.pdf:
        t = B.detect_tools()
        t["chosen_backend"] = B.choose_backend()
        print(json.dumps(t, indent=1, ensure_ascii=False))
        return 0 if t["chosen_backend"] else 3
    out = a.out or (os.path.splitext(os.path.basename(a.pdf))[0] + "_parsed")
    os.makedirs(out, exist_ok=True)
    log = (lambda *x: None) if a.quiet else (lambda *x: print("[parse]", *x, file=sys.stderr))
    t0 = time.time()
    try:
        doc = S.parse_document(a.pdf, a.pages, backend=a.backend, ocr=a.ocr if a.ocr != "force" else "auto",
                               ocr_lang=a.ocr_lang, workdir=os.path.join(out, "_work"), ocr_budget_s=a.ocr_budget,
                               log=log, force_ocr=(a.ocr == "force"))
    except B.PDFOpenError as e:
        err = {"status": "failed", "error_kind": e.kind, "error": str(e), "file": a.pdf,
               "advice": {"encrypted": "PDF needs a password: ask the user / owner; do not guess content.",
                          "damaged": "File is damaged or not a PDF: try another copy; report as unreadable.",
                          "unsupported": "No PDF engine installed: install PyMuPDF or poppler-utils."}.get(e.kind, "")}
        with open(os.path.join(out, "parse_error.json"), "w", encoding="utf-8") as fh:
            json.dump(err, fh, ensure_ascii=False, indent=1)
        print(json.dumps(err, ensure_ascii=False))
        return 2 if e.kind != "unsupported" else 3
    files = E.write_outputs(doc, a.pdf, out, previews=not a.no_previews, crops=not a.no_crops, log=log)
    print(summary_text(doc, files))
    log("done in %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

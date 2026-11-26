#!/usr/bin/env python3
"""Look things up in a parsed document (document.json) with exact locators.

  python3 query.py outline                     # section tree with block ids
  python3 query.py search "regex|words" [--type paragraph,table,...] [--max 15]
  python3 query.py show p3-b12 [--context 1]   # full text + lines + bbox (+ neighbours)
  python3 query.py page 3                      # reading order of one page
  python3 query.py section 2.1                 # all blocks of a section (by number or sec-id)
  python3 query.py table table-2 [--csv]       # one table (markdown or csv) with locator
  python3 query.py tables | figures | equations | footnotes | furniture | refs [label]
  python3 query.py checks                      # QA checks and warnings
Options: --doc PATH (default: newest /app/output/parsed/**/document.json)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys


def find_doc(path):
    if path:
        return path
    cands = glob.glob("/app/output/parsed/**/document.json", recursive=True) + \
        glob.glob("/app/output/**/document.json", recursive=True) + glob.glob("**/document.json", recursive=True)
    cands = sorted(set(cands), key=lambda p: -os.path.getmtime(p))
    if not cands:
        sys.exit("no document.json found; run run_case.py or parse_pdf.py first (or pass --doc)")
    return cands[0]


def loc(b):
    return "[%s p%d bbox=%s %s]" % (b["id"], b["page"], ",".join("%.0f" % v for v in b["bbox"]), b.get("type", ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--doc")
    ap.add_argument("--type")
    ap.add_argument("--max", type=int, default=15)
    ap.add_argument("--context", type=int, default=0)
    ap.add_argument("--csv", action="store_true")
    a = ap.parse_args()
    path = find_doc(a.doc)
    d = json.load(open(path, encoding="utf-8"))
    blocks = d["blocks"]
    idx = {b["id"]: i for i, b in enumerate(blocks)}
    print("# %s (%s)" % (os.path.basename(d["source"]["file"]), path))
    c = a.cmd
    if c == "outline":
        m = d["metadata"]
        print("title: %s [%s]" % (m["title"], m["title_block"]))
        for s in d["sections"]:
            print("%s%s %s  [p%d %s]" % ("  " * (s["level"] - 1), s.get("number") or "-", s["title"], s["page"], s["block"]))
    elif c == "search":
        if not a.arg:
            sys.exit("search needs a pattern")
        rx = re.compile(a.arg, re.I)
        types = set(a.type.split(",")) if a.type else None
        n = 0
        for b in blocks:
            if types and b["type"] not in types:
                continue
            t = b.get("text", "")
            m = rx.search(t)
            if m:
                s0 = max(0, m.start() - 120)
                print("%s §%s: ...%s..." % (loc(b), b.get("section") or "-", t[s0:m.end() + 160].replace("\n", " ")))
                n += 1
                if n >= a.max:
                    break
        for t in d["tables"]:
            if types and "table" not in types:
                continue
            for ri, g in enumerate(t.get("grid", [])):
                row = " | ".join(x["text"] for x in g["cells"])
                if rx.search(row):
                    print("[%s p%d row %d bbox=%s table] %s" % (t["id"], t["page"], ri, ",".join("%.0f" % v for v in g["bbox"]), row))
                    n += 1
        for r in d["references"]:
            if (not types or "reference" in types) and rx.search(r["text"]) and n < a.max * 2:
                print("[%s p%d reference] %s" % (r["id"], r["page"], r["text"][:200]))
                n += 1
        if n == 0:
            print("no match")
    elif c == "show":
        if a.arg not in idx:
            sys.exit("unknown block id %s" % a.arg)
        i = idx[a.arg]
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
        fur = [f for f in d["furniture"] if f["page"] == p]
        if fur:
            print("removed page furniture: " + "; ".join("%s '%s'" % (f["role"], f["text"][:40]) for f in fur))
    elif c == "section":
        sec = next((s for s in d["sections"] if s["id"] == a.arg or (s.get("number") or "") == a.arg
                    or s["title"].lower().startswith((a.arg or "").lower())), None)
        if not sec:
            sys.exit("section not found")
        ids = {sec["id"]} | {s["id"] for s in d["sections"] if s.get("parent") == sec["id"]}
        for b in blocks:
            if b.get("section") in ids:
                print("%s %s" % (loc(b), (b.get("text") or "")[:400]))
    elif c in ("table", "tables"):
        for t in d["tables"]:
            if c == "table" and t["id"] != a.arg and t["number"] != a.arg:
                continue
            print("%s %s p%d bbox=%s %dx%d header_rows=%d crop=%s csv=%s" % (t["id"], t["label"], t["page"], t["bbox"],
                  t["nrows"], t["ncols"], t["header_rows"], t.get("crop"), t.get("csv")))
            print("caption: " + t["caption"]["text"])
            if c == "table":
                if a.csv:
                    print(",".join(t.get("header", [])))
                    for r in t.get("matrix", [])[t["header_rows"]:]:
                        print(",".join('"%s"' % x if "," in x else x for x in r))
                else:
                    print(t.get("markdown"))
                for iss in t.get("issues", []):
                    print("issue: " + iss)
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
        for r in d["references"]:
            if a.arg and a.arg not in (r["label"], r["id"], r["label"].strip("[]")):
                continue
            print("%s %s p%d: %s" % (r["id"], r["label"], r["page"], r["text"]))
    elif c == "checks":
        print("QA status: %s" % d["qa"]["status"])
        for k in d["qa"]["checks"]:
            print("[%s] %s: %s" % ("ok" if k["ok"] else "CHECK", k["check"], k["value"]))
        for w in d["warnings"]:
            print("warning: " + w)
    else:
        sys.exit("unknown command %s" % c)


if __name__ == "__main__":
    main()

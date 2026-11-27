#!/usr/bin/env python3
"""Score PDF-to-text methods against the generated ground truth.

  python3 score_bench.py out/bench_zh.pdf out/bench_zh.gt.json [--skill-scripts ../scripts]

Methods: pdftotext (default), pdftotext -layout, PyMuPDF get_text() (if installed),
and this skill (pdfdoc -> document.md body text + structured tables/sections).
Metrics (all computed from the ground truth, no manual judgement):
  para_intact   share of GT paragraphs that appear contiguously (whitespace/hyphenation ignored)
  order_ok      share of consecutive GT paragraph pairs found in the right order
  furniture     running header / page-footer strings leaking into the text (lower is better)
  headings      GT headings recovered as headings with the right level (skill only)
  table_cells   GT table body cells recovered at the right (row, column) (skill only);
                for plain text: share of GT table rows whose values appear in order on one line
  eq_numbers    numbered display equations recovered (skill only)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile


def norm(t):
    t = t.replace("­", "")
    t = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", t)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"</?su[pb]>", "", t)
    t = t.replace("\\", "")
    t = re.sub(r"\s+", "", t)
    return t


def para_metrics(text, gt):
    nt = norm(text)
    intact = 0
    pos = []
    for p in gt["paragraphs"]:
        np_ = norm(p)
        if np_ in nt:
            intact += 1
        anchor = np_[:14]
        pos.append(nt.find(anchor))
    pairs = [(a, b) for a, b in zip(pos, pos[1:]) if a >= 0 and b >= 0]
    order = sum(1 for a, b in pairs if a < b) / float(len(pos) - 1)
    return intact / float(len(gt["paragraphs"])), order


def furniture(text, gt):
    n = text.count(gt["header"])
    n += len(re.findall(gt["footer_regex"], text))
    return n


def table_rows_on_line(text, gt):
    lines = text.splitlines()
    ok = 0
    for r in gt["table"]["rows"]:
        if any(all(v in ln for v in r) and _in_order(ln, r) for ln in lines):
            ok += 1
    return ok / float(len(gt["table"]["rows"]))


def _in_order(ln, vals):
    p = -1
    for v in vals:
        q = ln.find(v, p + 1)
        if q < 0:
            return False
        p = q
    return True


def skill_structured(doc, gt):
    heads = {(s.get("number"), s["level"]) for s in doc["sections"]}
    h_ok = sum(1 for h in gt["headings"] if (h["number"], h["level"]) in heads) / float(len(gt["headings"]))
    cells = 0
    tot = 0
    tab = next((t for t in doc["tables"] if t["number"] == gt["table"]["number"]), None)
    for r in gt["table"]["rows"]:
        tot += len(r)
        if not tab:
            continue
        row = next((m for m in tab["matrix"][tab["header_rows"]:] if m and m[0] == r[0]), None)
        if row:
            cells += sum(1 for i, v in enumerate(r) if i < len(row) and row[i] == v)
    nums = set()
    for e in doc["equations"]:
        nums.update(re.findall(r"\((\d+)\)", e.get("number") or ""))
    eq = sum(1 for n in gt["equations"] if n in nums) / float(len(gt["equations"]))
    return h_ok, cells / float(tot), eq


def run(cmd):
    return subprocess.run(cmd, capture_output=True, timeout=300).stdout.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("gt")
    ap.add_argument("--skill-scripts", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--backend", default="auto")
    a = ap.parse_args()
    gt = json.load(open(a.gt, encoding="utf-8"))
    rows = []
    texts = {"pdftotext": run(["pdftotext", a.pdf, "-"]),
             "pdftotext -layout": run(["pdftotext", "-layout", a.pdf, "-"])}
    try:
        import pymupdf  # noqa
        texts["PyMuPDF get_text()"] = run([a.python, "-c", "import pymupdf,sys; d=pymupdf.open(sys.argv[1]); "
                                           "print(''.join(p.get_text() for p in d))", a.pdf])
    except Exception:
        pass
    for name, t in texts.items():
        pi, po = para_metrics(t, gt)
        rows.append((name, pi, po, furniture(t, gt), None, table_rows_on_line(t, gt), None))
    out = tempfile.mkdtemp(prefix="bench_")
    subprocess.run([a.python, os.path.join(a.skill_scripts, "parse_pdf.py"), a.pdf, "--out", out, "--quiet",
                    "--no-previews", "--no-crops", "--backend", a.backend], capture_output=True, timeout=600)
    doc = json.load(open(os.path.join(out, "document.json"), encoding="utf-8"))
    md = open(os.path.join(out, "document.md"), encoding="utf-8").read()
    body = "\n".join(b["text"] for b in doc["blocks"] if b["type"] in ("paragraph", "list_item", "abstract"))
    # paragraphs that continue across columns/pages are merged in document.md
    pi, po = para_metrics(md, gt)
    h, c, e = skill_structured(doc, gt)
    rows.append(("this skill (%s)" % doc["engine"]["backend"], pi, po, furniture(body, gt), h, c, e))
    print("| method | para_intact | order_ok | furniture leaks | headings | table cells | eq numbers |")
    print("|---|---|---|---|---|---|---|")
    f = lambda v: "—" if v is None else ("%.0f%%" % (100 * v) if isinstance(v, float) else str(v))
    for r in rows:
        print("| %s | %s | %s | %s | %s | %s | %s |" % (r[0], f(r[1]), f(r[2]), r[3], f(r[4]), f(r[5]), f(r[6])))


if __name__ == "__main__":
    main()

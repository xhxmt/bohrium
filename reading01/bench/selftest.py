#!/usr/bin/env python3
"""Self-test of the skill against the hard cases (make_hard_cases.py) and the
result.json contract logic.

  python3 make_hard_cases.py out_hard && python3 selftest.py out_hard [--scripts ../scripts]

Prints one PASS / FAIL / SKIP line per check and exits non-zero if any check fails.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def skip(name, why):
    RESULTS.append((name, "SKIP", why))


def parse(scripts, pdf, out, extra=()):
    r = subprocess.run([sys.executable, os.path.join(scripts, "parse_pdf.py"), pdf, "--out", out, "--quiet",
                        "--no-previews"] + list(extra), capture_output=True, text=True, timeout=900)
    p = os.path.join(out, "document.json")
    if not os.path.exists(p):
        raise RuntimeError("parse failed: %s" % (r.stderr[-600:] or r.stdout[-600:]))
    return json.load(open(p, encoding="utf-8"))


def norm(t):
    return re.sub(r"\s+", "", (t or "")).replace("−", "-").lower()


def body(t):
    return [[norm(x) for x in r] for r in t["matrix"][t["header_rows"]:]]


def find_table(doc, caption_part):
    return next((t for t in doc["tables"] if caption_part.lower() in t["caption"]["text"].lower()
                 and not t.get("continued_from")), None)


def test_tables(scripts, hard, tmp):
    pdf = os.path.join(hard, "tables_hard.pdf")
    gt = json.load(open(os.path.join(hard, "tables_hard.gt.json"), encoding="utf-8"))
    d = parse(scripts, pdf, os.path.join(tmp, "tables"))
    t = find_table(d, gt["rowspan"]["caption"])
    want = [[norm(x) for x in r] for r in gt["rowspan"]["rows"]]
    check("table: row-spanning labels (\\multirow centred, between rows, top-aligned)",
          t is not None and body(t) == want, "" if t is None else "got %s" % body(t)[:4])
    if t:
        spans = sorted(c.get("rowspan", 1) for g in t["grid"] for c in g["cells"] if c.get("rowspan", 1) > 1)
        check("table: rowspan attributes in the grid / HTML", spans == sorted(n for _, n, _ in gt["rowspan"]["spans"])
              and "rowspan=" in t["html"], "spans %s" % spans)
    t = find_table(d, gt["wrap"]["caption"])
    want = [[norm(x) for x in r] for r in gt["wrap"]["rows"]]
    check("table: wrapped cell text joined into one cell", t is not None and body(t) == want,
          "" if t is None else "got %s" % body(t))
    t = next((x for x in d["tables"] if x.get("uncaptioned")), None)
    want = [[norm(x) for x in gt["uncaptioned"]["header"]]] + [[norm(x) for x in r] for r in gt["uncaptioned"]["rows"]]
    got = [[norm(x) for x in r] for r in t["matrix"]] if t else None
    if d["engine"]["backend"] in ("poppler", "pypdf"):
        skip("table: uncaptioned ruled table detected with its cells",
             "backend %s reports no ruling lines (known limit; install PyMuPDF or pdfplumber)" % d["engine"]["backend"])
    else:
        check("table: uncaptioned ruled table detected with its cells", got == want, "got %s" % got)
    t = find_table(d, gt["long"]["caption"])
    want = [[norm(x) for x in r] for r in gt["long"]["rows"]]
    merged = t and t.get("merged")
    got = [[norm(x) for x in r] for r in merged["matrix"][merged["header_rows"]:]] if merged else None
    check("table: continued table merged across pages (repeated header dropped)", got == want,
          "parts %s, rows %s" % (merged and merged["parts"], got and len(got)))
    t = find_table(d, gt["image"]["caption"])
    tools = subprocess.run(["tesseract", "--version"], capture_output=True).returncode == 0 if shutil.which("tesseract") else False
    if not tools:
        skip("table: image-only table read by OCR", "tesseract not installed")
    else:
        cells = [norm(x) for r in gt["image"]["rows"] for x in r]
        got = [norm(x) for r in (t["matrix"] if t else []) for x in r]
        hit = sum(1 for c in cells if c in got)
        check("table: image-only table read by OCR (>= 80% of body cells)",
              t is not None and (t.get("body_source") or "").startswith("ocr") and hit >= 0.8 * len(cells),
              "%d/%d cells; source %s" % (hit, len(cells), t and t.get("body_source")))


def test_zh(scripts, hard, tmp):
    gt = json.load(open(os.path.join(hard, "zh_noabstract.gt.json"), encoding="utf-8"))
    d = parse(scripts, os.path.join(hard, "zh_noabstract.pdf"), os.path.join(tmp, "zh"))
    check("zh: title", norm(d["metadata"]["title"]) == norm(gt["title"]), d["metadata"]["title"])
    check("zh: unlabeled Chinese abstract inferred", norm(d["metadata"]["abstract"]) == norm(gt["abstract"]),
          (d["metadata"]["abstract"] or "")[:40])
    b = next((x for x in d["blocks"] if norm(x.get("text", "")).startswith(norm(gt["first_body_paragraph_starts"]))), None)
    check("zh: first body paragraph is not front matter", b is not None and b["type"] == "paragraph",
          b and b["type"])


def test_short(scripts, hard, tmp):
    gt = json.load(open(os.path.join(hard, "short_furniture.gt.json"), encoding="utf-8"))
    d = parse(scripts, os.path.join(hard, "short_furniture.pdf"), os.path.join(tmp, "short"))
    fur = " ".join(norm(f["text"]) for f in d["furniture"])
    flow = " ".join(norm(b.get("text")) for b in d["blocks"] if b["type"] not in ("figure", "table"))
    for k in ("journal", "doi", "copyright", "page_number"):
        v = norm(gt[k])
        check("furniture (1-page document): %s removed from the text flow" % k,
              v[:25] in fur and v[:25] not in flow, gt[k][:40])


def test_scan(scripts, hard, tmp):
    gt = json.load(open(os.path.join(hard, "scan_mixed.gt.json"), encoding="utf-8"))
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout if shutil.which("tesseract") else ""
    if "chi_sim" not in langs:
        skip("OCR: language chosen per page (English page 1, Chinese pages 2-3)", "tesseract chi_sim not installed")
        return
    d = parse(scripts, os.path.join(hard, "scan_mixed.pdf"), os.path.join(tmp, "scan"))
    got = {str(p["page"]): p["lang"] for p in (d.get("ocr") or {}).get("pages", [])}
    check("OCR: language chosen per page (English page 1, Chinese pages 2-3)", got == gt["page_lang"], "got %s" % got)


def test_contract(scripts, hard, tmp):
    """result.json assembly: agent values win, types follow the contract, quotes are checked."""
    work = os.path.join(tmp, "case")
    for sub in ("input", "out", "work"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)
    shutil.copy(os.path.join(hard, "bench_en.pdf"), os.path.join(work, "input", "paper.pdf"))
    case = {"case_id": "selftest-1", "output": "/app/output", "materials": [{"path": "paper.pdf"}],
            "task": "What is the 60 °C conductivity of PEM-10?",
            "output_contract": {"required": ["case_id", "status", "answers", "uncertainties", "title", "page_count"],
                                "properties": {"answers": {"type": "object"}, "title": {"type": "string"},
                                               "uncertainties": {"type": "array", "items": {"type": "string"}},
                                               "page_count": {"type": "integer"}}}}
    cp = os.path.join(work, "input", "case.json")
    json.dump(case, open(cp, "w"))
    args = ["--case", cp, "--out", os.path.join(work, "out"), "--work", os.path.join(work, "work")]
    subprocess.run([sys.executable, os.path.join(scripts, "run_case.py")] + args + ["--no-previews"],
                   capture_output=True, text=True, timeout=600)
    draft = json.load(open(os.path.join(work, "out", "result.json"), encoding="utf-8"))
    check("contract: draft leaves a wrongly-typed built-in value unfilled (answers must be an object)",
          "answers" in draft["contract"]["unfilled"], draft["contract"]["unfilled"])
    check("contract: automatic values follow the declared types",
          isinstance(draft.get("title"), str) and isinstance(draft.get("page_count"), int)
          and all(isinstance(x, str) for x in draft.get("uncertainties", [])),
          "title %s, page_count %s" % (type(draft.get("title")).__name__, type(draft.get("page_count")).__name__))
    ans = {"status": "success", "fields": {"answers": {"q1": "3.48"}},
           "answers": [{"question": "q1", "answer": "3.48",
                        "evidence": [{"block": "table-1", "quote": "PEM-10 | 1.37 | 3.48"},
                                     {"quote": "this study examines ... crosslinked polymer electrolyte membranes"},
                                     {"quote": "a sentence that is not in the paper"}]}]}
    ap = os.path.join(work, "work", "answers.json")
    json.dump(ans, open(ap, "w"))
    r = subprocess.run([sys.executable, os.path.join(scripts, "finalize.py"), "--answers", ap,
                        "--state", os.path.join(work, "work", "pdfdoc_state.json")], capture_output=True, text=True,
                       timeout=300)
    res = json.load(open(os.path.join(work, "out", "result.json"), encoding="utf-8"))
    check("contract: agent value of a contract field is never overwritten", res.get("answers") == {"q1": "3.48"},
          res.get("answers"))
    ev = (res.get("skill_details") or {}).get("answers", [{}])[0].get("evidence", [])
    flags = [e.get("quote_check", {}).get("verified") for e in ev]
    check("quotes: table row and '...' quote verified, invented quote rejected", flags == [True, True, False], flags)
    check("finalize: reports the unverified quote", "NOT found" in r.stdout, r.stdout[-300:])


def test_contract_shapes(scripts, hard, tmp):
    """Flat contracts keep title/notes; output files named in the contract are not missing inputs;
    evidence with the same block id in two PDFs goes to the PDF that holds the quote."""
    work = os.path.join(tmp, "case2")
    for sub in ("input", "out", "work"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)
    shutil.copy(os.path.join(hard, "bench_en.pdf"), os.path.join(work, "input", "en.pdf"))
    shutil.copy(os.path.join(hard, "bench_zh.pdf"), os.path.join(work, "input", "zh.pdf"))
    case = {"case_id": "selftest-2", "materials": ["en.pdf", "zh.pdf"],
            "output_contract": {"case_id": "string", "title": "string", "notes": "string",
                                "preview": "rendered page image `preview.png`"}}
    cp = os.path.join(work, "input", "case.json")
    json.dump(case, open(cp, "w"))
    args = ["--case", cp, "--out", os.path.join(work, "out"), "--work", os.path.join(work, "work")]
    subprocess.run([sys.executable, os.path.join(scripts, "run_case.py")] + args + ["--no-previews"],
                   capture_output=True, text=True, timeout=600)
    draft = json.load(open(os.path.join(work, "out", "result.json"), encoding="utf-8"))
    flds = draft["contract"]["fields"]
    check("contract: flat contract keeps 'title' and 'notes' as fields", "title" in flds and "notes" in flds, flds)
    check("contract: output file named in the contract is not reported as a missing input",
          not any("preview.png" in json.dumps(x) for x in draft["not_reported"]), draft["not_reported"])
    ans = {"answers": [{"question": "q", "answer": "a",
                        "evidence": [{"block": "p1-b2", "quote": "适度交联可同时提高两类性能"}]}]}
    ap = os.path.join(work, "work", "answers.json")
    json.dump(ans, open(ap, "w"), ensure_ascii=False)
    subprocess.run([sys.executable, os.path.join(scripts, "finalize.py"), "--answers", ap,
                    "--state", os.path.join(work, "work", "pdfdoc_state.json")], capture_output=True, text=True,
                   timeout=300)
    res = json.load(open(os.path.join(work, "out", "result.json"), encoding="utf-8"))
    ev = res["answers"][0]["evidence"][0]
    check("evidence: block id present in two PDFs is resolved to the PDF holding the quote",
          ev.get("file") == "zh.pdf" and ev.get("quote_check", {}).get("file") == "zh.pdf",
          "file %s, quote in %s" % (ev.get("file"), ev.get("quote_check", {}).get("file")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hard_dir")
    ap.add_argument("--scripts", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    a = ap.parse_args()
    scripts = os.path.abspath(a.scripts)
    tmp = tempfile.mkdtemp(prefix="pdfdoc_selftest_")
    for fn in (test_tables, test_zh, test_short, test_scan, test_contract, test_contract_shapes):
        try:
            fn(scripts, a.hard_dir, tmp)
        except Exception as e:  # a crash is a failed check, not a crashed test run
            check(fn.__name__, False, "crashed: %r" % e)
    w = max(len(n) for n, _, _ in RESULTS)
    for n, st, det in RESULTS:
        print("%-4s  %-*s  %s" % (st, w, n, str(det)[:160] if st != "PASS" else ""))
    nf = sum(1 for _, st, _ in RESULTS if st == "FAIL")
    print("\n%d checks: %d pass, %d fail, %d skipped" % (len(RESULTS), sum(1 for _, s, _ in RESULTS if s == "PASS"), nf,
                                                       sum(1 for _, s, _ in RESULTS if s == "SKIP")))
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if nf else 0


if __name__ == "__main__":
    sys.exit(main())

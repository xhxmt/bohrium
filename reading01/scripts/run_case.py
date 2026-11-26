#!/usr/bin/env python3
"""Step 1 of the skill: read the case, parse every PDF/image it provides, write a
draft result.json immediately, and print a compact map of the document.

Usage (defaults match the evaluation runtime):
  python3 run_case.py [--case /input/case.json] [--task /input/task.md]
                      [--out /app/output] [--work /app/work]
                      [--pages 3-7] [--backend auto] [--ocr auto|off|force] [--ocr-lang auto]
Then: query.py (find evidence) -> write answers.json -> finalize.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pdfdoc import backends as B  # noqa: E402
from pdfdoc import caseio as C  # noqa: E402
from pdfdoc import export as E  # noqa: E402
from pdfdoc import structure as S  # noqa: E402
from parse_pdf import summary_text  # noqa: E402


def _first_existing(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", default=None)
    ap.add_argument("--task", default=None)
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--pages", default=None, help="restrict parsing, e.g. 2-5 (default: case.json 'pages' or all)")
    ap.add_argument("--backend", default="auto")
    ap.add_argument("--ocr", default="auto", choices=["auto", "off", "force"])
    ap.add_argument("--ocr-lang", default="auto")
    ap.add_argument("--ocr-budget", type=float, default=360.0)
    ap.add_argument("--no-previews", action="store_true")
    ap.add_argument("--material", action="append", default=[], help="extra PDF/image path(s)")
    a = ap.parse_args(argv)
    t0 = time.time()
    case_path = _first_existing([a.case, "/input/case.json", os.path.join(os.getcwd(), "case.json")])
    task_path = _first_existing([a.task, "/input/task.md",
                                 os.path.join(os.path.dirname(case_path), "task.md") if case_path else None])
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    notes = []
    if case_path:
        case, err = C.load_case(case_path)
        if err:
            notes.append(err)
    else:
        case, case_path = {}, os.path.join(a.work, "case.json")
        notes.append("no case.json found; running on --material files only")
    cid = C.case_id(case, case_path)
    ttxt = C.task_text(case, task_path)
    contract, fields = C.find_contract(case)
    mats, missing, urls = C.find_materials(case, case_path)
    for m in a.material:
        if os.path.isfile(m) and m not in mats:
            mats.append(m)
    pages, page_src = (a.pages, "--pages") if a.pages else C.page_hint(case, ttxt)
    if page_src and "suggestion" in (page_src or ""):
        notes.append("task text mentions pages %s; NOT applied automatically (re-run with --pages %s if the task is "
                     "restricted to them)" % (pages, pages))
        pages = None
    print("=" * 78)
    print("CASE %s   (case.json: %s; task.md: %s)" % (cid, case_path, task_path or "-"))
    print("-" * 78)
    print("TASK:\n" + (ttxt.strip() or "(no task text found; read case.json directly)"))
    print("-" * 78)
    print("OUTPUT CONTRACT fields: %s" % (fields or "(none declared -> use the default result.json layout)"))
    if contract:
        cj = json.dumps(contract, ensure_ascii=False)
        print("contract raw: " + (cj[:1500] + " ...") if len(cj) > 1500 else "contract raw: " + cj)
    print("MATERIALS: %s" % ([os.path.relpath(m) if not os.path.isabs(m) else m for m in mats] or "NONE"))
    for m in missing:
        print("MISSING material named in case.json: %s" % m["reference"])
    for u in urls:
        print("REMOTE material (not fetched): %s" % u)
    if pages:
        print("PAGES restricted to %s (%s)" % (pages, page_src))
    for n in notes:
        print("NOTE: " + n)
    print("=" * 78)
    docs, failures = [], []
    for i, m in enumerate(mats):
        stem = os.path.splitext(os.path.basename(m))[0]
        sub = os.path.join(a.out, "parsed", stem if len(mats) > 1 else "")
        os.makedirs(sub, exist_ok=True)
        try:
            doc = S.parse_document(m, pages, backend=a.backend, ocr="off" if a.ocr == "off" else "auto",
                                   ocr_lang=a.ocr_lang, workdir=os.path.join(a.work, "pdfdoc_" + stem),
                                   ocr_budget_s=a.ocr_budget / max(1, len(mats)), force_ocr=(a.ocr == "force"),
                                   log=lambda *x: print("[parse]", *x))
            files = E.write_outputs(doc, m, sub, previews=not a.no_previews)
            docs.append({"doc": doc, "files": files})
            print(summary_text(doc, {k: v for k, v in files.items() if k in ("document_md", "parse_report", "tables_dir")}))
        except B.PDFOpenError as e:
            adv = {"encrypted": "password-protected PDF: ask the owner for the password; do not guess its content",
                   "damaged": "damaged / not a PDF: report as unreadable, try another copy",
                   "unsupported": "no PDF engine installed"}.get(e.kind, "")
            failures.append({"file": m, "error": "%s: %s" % (e.kind, e), "advice": adv})
            print("FAILED %s -> %s (%s)" % (m, e, adv))
        except Exception as e:  # keep going: partial results are better than none
            import traceback
            traceback.print_exc()
            failures.append({"file": m, "error": "parser crash: %r" % e, "advice": "see traceback in the run log"})
        print("-" * 78)
    state = {"case_path": os.path.abspath(case_path), "task_path": task_path, "case_id": cid, "contract": contract,
             "fields": fields, "materials": mats, "missing": missing, "urls": urls, "failures": failures,
             "docs": [d["doc"]["_json_path"] for d in docs], "files": [d["files"] for d in docs],
             "out": os.path.abspath(a.out), "pages": pages}
    with open(os.path.join(a.work, "pdfdoc_state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    res = C.build_result(case, case_path, cid, docs, failures, answers=None, task_txt=ttxt, contract=contract,
                         fields=fields, missing_materials=missing, urls=urls, draft=True)
    with open(os.path.join(a.out, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("DRAFT result.json written: %s (status=%s). Contract fields auto-filled: %s; still unfilled: %s" % (
        os.path.join(a.out, "result.json"), res["status"], res["contract"]["auto_filled"], res["contract"]["unfilled"]))
    print("elapsed %.1fs" % (time.time() - t0))
    print("NEXT: answer the task from %s/parsed (query.py search/show/table/page), write %s/answers.json, "
          "then run finalize.py." % (a.out, a.work))
    return 0 if docs else 1


if __name__ == "__main__":
    sys.exit(main())

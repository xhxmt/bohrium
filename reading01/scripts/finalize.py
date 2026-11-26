#!/usr/bin/env python3
"""Step 3 of the skill: merge the agent's answers into result.json, verify every
quoted evidence string against the parsed text, fill all output-contract fields
and validate the result.

  python3 finalize.py [--answers /app/work/answers.json] [--state /app/work/pdfdoc_state.json]
                      [--out /app/output/result.json]

answers.json (all keys optional):
{
  "status": "success | partial | cannot_determine | refused | failed",
  "status_reason": "why",
  "fields": {"<contract field>": <value>, ...},          # overrides auto-filled values
  "answers": [{"question": "...", "answer": "...",
               "evidence": [{"block": "p3-b12", "quote": "exact words from the PDF"}],
               "confidence": "high|medium|low"}],
  "uncertainties": ["..."], "not_reported": ["..."], "manual_takeover": [], "change_records": []
}
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pdfdoc import caseio as C  # noqa: E402

ALLOWED_STATUS = ("success", "partial", "cannot_determine", "refused", "failed")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answers", default="/app/work/answers.json")
    ap.add_argument("--state", default="/app/work/pdfdoc_state.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if not os.path.isfile(a.state):
        sys.exit("state file %s missing: run run_case.py first" % a.state)
    st = json.load(open(a.state, encoding="utf-8"))
    out = a.out or os.path.join(st["out"], "result.json")
    answers = {}
    problems = []
    if os.path.isfile(a.answers):
        try:
            answers = json.load(open(a.answers, encoding="utf-8"))
        except Exception as e:
            problems.append("answers.json is not valid JSON (%s); finalized without it" % e)
    else:
        problems.append("no answers.json at %s; result contains the automatic parse only" % a.answers)
    if answers.get("status") and answers["status"] not in ALLOWED_STATUS:
        problems.append("status '%s' is not one of %s" % (answers["status"], ALLOWED_STATUS))
    case, _ = C.load_case(st["case_path"]) if os.path.isfile(st["case_path"]) else ({}, None)
    docs = []
    for p, files in zip(st["docs"], st["files"]):
        docs.append({"doc": json.load(open(p, encoding="utf-8")), "files": files})
    res = C.build_result(case, st["case_path"], st["case_id"], docs, st["failures"], answers=answers,
                         contract=st["contract"], fields=st["fields"], missing_materials=st["missing"],
                         urls=st["urls"], draft=False)
    if problems:
        res.setdefault("uncertainties", []).extend({"issue": p} for p in problems)
    # validation
    report = []
    for f in st["fields"] or []:
        if f not in res:
            report.append("MISSING contract field: %s" % f)
        elif res[f] is None:
            report.append("EMPTY contract field: %s (fill it in answers.json 'fields' or explain in uncertainties)" % f)
    nq = sum(1 for x in res["answers"] for e in x.get("evidence", []) if e.get("quote"))
    bad = [e for x in res["answers"] for e in x.get("evidence", []) if e.get("quote_check") and not e["quote_check"]["verified"]]
    noev = [x.get("question", x.get("id")) for x in res["answers"] if not x.get("evidence")]
    if bad:
        report.append("%d of %d quotes NOT found in the PDF text: fix the quote (copy it from query.py show) "
                      "or mark the answer uncertain" % (len(bad), nq))
    if noev:
        report.append("answers without evidence: %s" % noev[:10])
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    md = os.path.join(os.path.dirname(out), "result_summary.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(summary_md(res))
    print("result.json -> %s | status=%s | answers=%d | quotes verified %d/%d" % (
        out, res["status"], len(res["answers"]), nq - len(bad), nq))
    for r in report + problems:
        print("CHECK: " + r)
    if not report:
        print("validation OK: all contract fields present%s" % ("" if not st["fields"] else " (%d)" % len(st["fields"])))
    return 0


def summary_md(res):
    L = ["# Result summary — case %s" % res["case_id"], "",
         "- status: **%s** — %s" % (res["status"], res["status_reason"]), ""]
    for s in res.get("structure_summary", []):
        L.append("- %s: title “%s”; %d sections, %d tables, %d figures, %d equations, %d references; columns/page %s" % (
            s["file"], s["title"], s["sections"], s["tables"], s["figures"], s["equations"], s["references"],
            s["columns_per_page"]))
    for m in res.get("materials", []):
        L.append("- outputs: %s, %s, %s" % (m.get("document_md"), m.get("document_json"), m.get("parse_report")))
    if res.get("answers"):
        L += ["", "## Answers", ""]
        for x in res["answers"]:
            L.append("- **%s** — %s" % (x.get("question", x.get("id", "")), x.get("answer")))
            for e in x.get("evidence", []):
                ok = e.get("quote_check", {}).get("verified")
                L.append("  - evidence %s p%s %s %s%s" % (e.get("block", ""), e.get("page", "?"), e.get("bbox", ""),
                                                          ("“%s”" % e["quote"][:160]) if e.get("quote") else "",
                                                          "" if ok is None else (" ✔ verified" if ok else " ✘ NOT verified")))
    if res.get("uncertainties"):
        L += ["", "## Uncertainties", ""] + ["- " + json.dumps(u, ensure_ascii=False)[:300] for u in res["uncertainties"][:40]]
    if res.get("not_reported"):
        L += ["", "## Not reported / not determinable", ""] + ["- " + json.dumps(u, ensure_ascii=False)[:300] for u in res["not_reported"]]
    if res.get("manual_takeover"):
        L += ["", "## Manual takeover points", ""] + ["- " + json.dumps(u, ensure_ascii=False) for u in res["manual_takeover"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())

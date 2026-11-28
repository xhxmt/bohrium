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
    if answers and not isinstance(answers, dict):
        problems.append("answers.json must be a JSON object; finalized without it")
        answers = {}
    if "fields" in answers and not isinstance(answers["fields"], dict):
        problems.append("answers.json 'fields' must be an object {contract field: value}; it was ignored")
    if "answers" in answers and not isinstance(answers["answers"], (list, dict)):
        problems.append("answers.json 'answers' must be a list of {question, answer, evidence}; it was ignored")
        answers = dict(answers, answers=[])
    meta = {}
    res = C.build_result(case, st["case_path"], st["case_id"], docs, st["failures"], answers=answers,
                         contract=st["contract"], fields=st["fields"], missing_materials=st["missing"],
                         urls=st["urls"], draft=False, meta=meta, soft_fields=st.get("soft_fields"),
                         extra_uncertainties=problems)
    # validation
    report, review = [], []
    for f in st["fields"] or []:
        if f not in res:
            report.append("MISSING contract field: %s" % f)
        elif res[f] is None:
            report.append("EMPTY contract field: %s (fill it in answers.json 'fields' or explain in uncertainties)" % f)
    for tp in meta.get("type_problems", []):
        report.append("TYPE of contract field %s is %s but the contract asks for %s: fix it in answers.json 'fields'" % (
            tp["field"], tp["got"], tp["expected"]))
    for f in (st["fields"] or []) + (st.get("soft_fields") or []):
        r = meta.get("report", {}).get(f, {})
        if (r.get("source") or "").startswith("auto"):
            review.append("%s <- %s (%s)" % (f, r["source"][6:], r.get("type_got")))
    outdir = os.path.dirname(out)
    inputs = {os.path.basename(m) for m in st.get("materials", [])}
    for fn in st.get("contract_files") or C.contract_files(st["contract"]):
        if os.path.basename(fn) in inputs:
            continue  # an input file mentioned in the contract, not an output to produce
        if not os.path.exists(os.path.join(outdir, fn)) and not os.path.exists(os.path.join(outdir, os.path.basename(fn))):
            report.append("CONTRACT FILE missing: %s/%s (copy/convert it from %s/parsed/ or explain why not)" % (
                outdir, fn, outdir))
    ans = meta.get("answers", [])
    nq = sum(1 for x in ans for e in x.get("evidence", []) if e.get("quote"))
    bad = [e for x in ans for e in x.get("evidence", []) if e.get("quote_check") and not e["quote_check"]["verified"]]
    loose = [e for x in ans for e in x.get("evidence", []) if (e.get("quote_check") or {}).get("match") in ("loose", "ellipsis")]
    noev = [x.get("question", x.get("id")) for x in ans if not x.get("evidence")]
    unres = [e.get("block") for x in ans for e in x.get("evidence", []) if e.get("resolved") is False]
    if bad:
        report.append("%d of %d quotes NOT found in the PDF text: fix the quote (copy it from query.py show) "
                      "or mark the answer uncertain" % (len(bad), nq))
        for e in bad[:5]:
            report.append("  not found: \"%s\" (cited %s)" % (e["quote"][:120], e.get("block")))
    if unres:
        report.append("evidence block ids not found in document.json: %s" % unres[:10])
    if noev:
        report.append("answers without evidence: %s" % noev[:10])
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    full = res
    if meta.get("details"):
        with open(os.path.join(os.path.dirname(out), "result_details.json"), "w", encoding="utf-8") as fh:
            json.dump(meta["details"], fh, ensure_ascii=False, indent=1)
        full = dict(meta["details"], **res)
        print("contract forbids extra keys: built-in sections written to result_details.json")
    md = os.path.join(os.path.dirname(out), "result_summary.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(summary_md(full))
    print("result.json -> %s | status=%s | answers=%d | quotes verified %d/%d%s" % (
        out, res["status"], len(ans), nq - len(bad), nq,
        (" (%d matched ignoring spacing/punctuation or around '...')" % len(loose)) if loose else ""))
    if report or problems:
        print("=== ACTION REQUIRED: %d issue(s) below; fix answers.json and run finalize.py again "
              "(result.json is already written, so stopping now still leaves a valid deliverable) ===" % len(report + problems))
    for r in report + problems:
        print("CHECK: " + r)
    if review:
        print("REVIEW (filled automatically from the parse; confirm they answer what the contract asks): " +
              "; ".join(review))
    if not report:
        print("validation OK: all %d contract fields present with the declared types" % len(st["fields"] or [])
              if st["fields"] else "validation OK (no output_contract in case.json; default result.json layout)")
    return 0


def summary_md(res):
    L = ["# Result summary — case %s" % res["case_id"], "",
         "- status: **%s** — %s" % (res["status"], res["status_reason"]), ""]
    for s in res.get("structure_summary", []) if isinstance(res.get("structure_summary"), list) else []:
        L.append("- %s: title “%s”; %d sections, %d tables, %d figures, %d equations, %d references; columns/page %s" % (
            s["file"], s["title"], s["sections"], s["tables"], s["figures"], s["equations"], s["references"],
            s["columns_per_page"]))
    for m in res.get("materials", []) if isinstance(res.get("materials"), list) else []:
        L.append("- outputs: %s, %s, %s" % (m.get("document_md"), m.get("document_json"), m.get("parse_report")))
    ans = (res.get("skill_details") or {}).get("answers") or res.get("answers")
    if ans and isinstance(ans, list):
        L += ["", "## Answers", ""]
        for x in ans:
            if not isinstance(x, dict):
                L.append("- %s" % x)
                continue
            L.append("- **%s** — %s" % (x.get("question", x.get("id", "")), x.get("answer")))
            for e in (x.get("evidence") if isinstance(x.get("evidence"), list) else []):
                if not isinstance(e, dict):
                    continue
                ok = (e.get("quote_check") or {}).get("verified")
                L.append("  - evidence %s p%s %s %s%s" % (e.get("block", ""), e.get("page", "?"), e.get("bbox", ""),
                                                          ("“%s”" % e["quote"][:160]) if e.get("quote") else "",
                                                          "" if ok is None else (" ✔ verified" if ok else " ✘ NOT verified")))
    for key, title in (("uncertainties", "Uncertainties"), ("not_reported", "Not reported / not determinable"),
                       ("manual_takeover", "Manual takeover points")):
        v = res.get(key)
        if v is None or (key in (res.get("skill_details") or {})):
            v = (res.get("skill_details") or {}).get(key, v)
        if isinstance(v, list) and v:
            L += ["", "## " + title, ""] + ["- " + json.dumps(u, ensure_ascii=False)[:300] for u in v[:40]]
        elif v:
            L += ["", "## " + title, "", "- " + json.dumps(v, ensure_ascii=False)[:1000]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())

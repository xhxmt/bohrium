#!/usr/bin/env python3
"""Rough content checks for the mock cases (our own ground truth, not the official whiteboard).

Usage: python3 check.py runs/            # walks every run dir with a summary.json
Prints one row per run plus per-(case, condition) averages.
"""
import json, os, re, sys
from collections import defaultdict


def norm(s):
    return re.sub(r"\s+", " ", s.replace("×", "x").replace("−", "-")).lower()


def blob(run):
    out = os.path.join(run, "output")
    parts = []
    p = os.path.join(out, "result.json")
    if os.path.exists(p):
        parts.append(open(p, encoding="utf-8", errors="replace").read())
    return norm("\n".join(parts))


def has(b, *pats):
    return all(re.search(p, b) for p in pats)


CHECKS = {
    "sim-a-attention-structure": [
        ("title", lambda b, r: has(b, r"attention is all you need")),
        ("subsection 3.2.1", lambda b, r: has(b, r"3\.2\.1", r"scaled dot-product attention")),
        ("section 6.2 / model variations", lambda b, r: has(b, r"model variations")),
        ("tables>=4", lambda b, r: has(b, r"table 4") or has(b, r"english constituency parsing")),
        ("eq numbers", lambda b, r: has(b, r"multihead|multi-head") and has(b, r"\(3\)|\"3\"|eq-?3|equation 3")),
        ("refs=40", lambda b, r: re.search(r"\b40\b", b) is not None and "reference" in b),
        ("page locators", lambda b, r: re.search(r"\"page\"\s*:\s*\d", b) is not None),
        ("document.md", lambda b, r: os.path.exists(os.path.join(r, "output", "document.md"))),
    ],
    "sim-b-resnet-tables-qa": [
        ("Q1 1x1,128/3x3,128/1x1,512 x8", lambda b, r: has(b, r"1 ?x ?1, ?128", r"1 ?x ?1, ?512") and re.search(r"x ?8\b|8 ?(times|blocks|次)|\b8\b", b)),
        ("Q2 21.43 / 5.71", lambda b, r: has(b, r"21\.43", r"5\.71")),
        ("Q3 6.43", lambda b, r: has(b, r"6\.43")),
        ("Q4 GPU not reported", lambda b, r: has(b, r"gpu") and has(b, r"not reported|未报告|not specified|未说明|未提及")),
        ("Table 4 values 19.38/4.49", lambda b, r: has(b, r"19\.38", r"4\.49")),
        ("page locators", lambda b, r: re.search(r"\"page\"\s*:\s*\d", b) is not None),
    ],
    "sim-c-bert-scanned": [
        ("title", lambda b, r: has(b, r"pre-training of deep bidirectional transformers")),
        ("abstract", lambda b, r: has(b, r"bidirectional encoder representations from transformers")),
        ("GLUE 80.5 / 7.7", lambda b, r: has(b, r"80\.5", r"7\.7")),
        ("section headings", lambda b, r: has(b, r"related work") and has(b, r"introduction")),
        ("ocr quality reported", lambda b, r: has(b, r"ocr|confidence|置信")),
        ("page locators", lambda b, r: re.search(r"\"page\"\s*:\s*\d", b) is not None),
    ],
}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "runs"
    rows = []
    for dp, dn, fn in os.walk(root):
        if "summary.json" in fn:
            s = json.load(open(os.path.join(dp, "summary.json")))
            b = blob(dp)
            checks = CHECKS.get(s["case_id"], [])
            passed = [n for n, f in checks if b and f(b, dp)]
            rows.append((dp, s, passed, [n for n, _ in checks if n not in passed]))
    agg = defaultdict(list)
    for dp, s, ok, bad in sorted(rows):
        r = s["result"]
        print(f"{os.path.relpath(dp, root):45s} {s['condition']:5s} end={s['end_reason']:15s} "
              f"req={s['requests']:2d} t={s['wall_seconds']:6.1f}s first_result={s['first_result_json_at']} "
              f"json={'ok' if r.get('valid_json') else 'NO'} missing={r.get('missing_fields')} "
              f"status={r.get('status')} checks={len(ok)}/{len(ok) + len(bad)}")
        if bad:
            print(f"{'':45s} failed: {', '.join(bad)}")
        agg[(s["case_id"], s["condition"])].append((len(ok) / max(1, len(ok) + len(bad)), s["requests"], s["wall_seconds"]))
    print("\n== averages")
    for (cid, cond), v in sorted(agg.items()):
        n = len(v)
        print(f"{cid:28s} {cond:5s} n={n} checks={sum(x[0] for x in v) / n:.0%} "
              f"req={sum(x[1] for x in v) / n:.1f} t={sum(x[2] for x in v) / n:.0f}s")


if __name__ == "__main__":
    main()

"""Case input discovery, output-contract handling and result.json assembly."""
from __future__ import annotations

import glob
import json
import os
import re
import time

MATERIAL_RE = re.compile(r"\.(pdf|png|jpe?g|tiff?|bmp|webp)$", re.I)
TASK_KEYS = ("task", "tasks", "instruction", "instructions", "prompt", "question", "questions", "query", "queries",
             "requirement", "requirements", "goal", "objective", "description", "request", "subtasks", "items")
CONTRACT_KEYS = ("output_contract", "outputContract", "output_schema", "result_schema", "outputs", "output",
                 "deliverables", "required_fields", "required_outputs", "expected_output", "response_format")


# --------------------------------------------------------------------------
# reading the case
# --------------------------------------------------------------------------

def load_case(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    txt = raw.decode("utf-8", "replace")
    try:
        return json.loads(txt), None
    except Exception as e:
        return {"_raw_text": txt}, "case.json is not valid JSON (%s); treated as plain text" % e


def _walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            for x in _walk(v, path + "/" + str(k)):
                yield x
    elif isinstance(o, list):
        for i, v in enumerate(o):
            for x in _walk(v, path + "[%d]" % i):
                yield x
    else:
        yield path, o


def find_key(o, names):
    """First value whose key matches one of names (case-insensitive), depth-first."""
    low = [n.lower() for n in names]
    if isinstance(o, dict):
        for k, v in o.items():
            if str(k).lower() in low:
                return k, v
        for v in o.values():
            r = find_key(v, names)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = find_key(v, names)
            if r:
                return r
    return None


def case_id(case, path):
    for k in ("case_id", "caseId", "case", "id", "uid", "name"):
        r = find_key(case, [k])
        if r and isinstance(r[1], (str, int)) and str(r[1]).strip():
            return str(r[1])
    return os.path.splitext(os.path.basename(os.path.dirname(os.path.abspath(path)) or "case"))[0] or "case"


def find_materials(case, case_path, extra_dirs=("/input",)):
    found, missing, urls = _find_materials(case, case_path, extra_dirs)
    if not found:
        # fallback: look in other common mount points (never our own output / skill dirs)
        for d in ("/app/input", "/app/data", "/app/materials", "/data", "/mnt/data", "/workspace", "/app/work", os.getcwd()):
            if not os.path.isdir(d):
                continue
            for f in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True))[:5000]:
                if os.path.isfile(f) and MATERIAL_RE.search(f) and "/app/output" not in f and "/skills/" not in f \
                        and "pdfdoc_" not in f and f not in found:
                    found.append(f)
            if found:
                break
    return found, missing, urls


def _find_materials(case, case_path, extra_dirs=("/input",)):
    base_dirs = [os.path.dirname(os.path.abspath(case_path))] + [d for d in extra_dirs if os.path.isdir(d)]
    found, missing, urls = [], [], []
    for p, v in _walk(case):
        if not isinstance(v, str):
            continue
        s = v.strip()
        if not MATERIAL_RE.search(s.split("?")[0]):
            continue
        if s.startswith(("http://", "https://")):
            urls.append(s)
            continue
        cands = [s] if os.path.isabs(s) else []
        for d in base_dirs:
            cands += [os.path.join(d, s), os.path.join(d, os.path.basename(s))]
        hit = next((c for c in cands if os.path.isfile(c)), None)
        if hit:
            if os.path.abspath(hit) not in [os.path.abspath(f) for f in found]:
                found.append(hit)
        else:
            missing.append({"reference": s, "json_path": p})
    # files present in the input directories even if case.json does not name them
    for d in base_dirs:
        for f in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
            if os.path.isfile(f) and MATERIAL_RE.search(f) and "/app/output" not in f:
                if os.path.abspath(f) not in [os.path.abspath(x) for x in found]:
                    found.append(f)
    return found, missing, urls


def find_contract(case):
    r = find_key(case, CONTRACT_KEYS)
    if not r:
        return None, []
    key, val = r
    return {key: val}, contract_fields(val)


def contract_fields(val):
    """Field names required by an output contract in any common shape."""
    fields = []
    if isinstance(val, dict):
        props = val.get("properties") if isinstance(val.get("properties"), dict) else None
        req = val.get("required") if isinstance(val.get("required"), list) else None
        flds = None
        for k in ("fields", "required_fields", "keys", "result_fields", "result.json", "result_json", "result"):
            if k in val:
                flds = val[k]
                break
        if props:
            fields = list(props.keys())
            if req:
                fields = list(dict.fromkeys(list(req) + fields))
        elif req:
            fields = list(req)
        elif flds is not None:
            fields = contract_fields(flds)
        else:
            fields = [k for k in val.keys() if k not in ("description", "format", "type", "notes", "note",
                                                          "file", "path", "filename")]
    elif isinstance(val, list):
        for x in val:
            if isinstance(x, str):
                fields.append(x.split(":")[0].strip())
            elif isinstance(x, dict):
                n = x.get("name") or x.get("field") or x.get("key") or x.get("id")
                if n:
                    fields.append(str(n))
    elif isinstance(val, str):
        fields = re.findall(r"`([A-Za-z_][\w.]*)`", val) or re.findall(r"\b([a-z]+_[a-z_]+)\b", val)
    out = []
    for f in fields:
        f = str(f).strip()
        if f and f not in out:
            out.append(f)
    return out


def task_text(case, task_md_path=None, limit=6000):
    parts = []
    for k in TASK_KEYS:
        r = find_key(case, [k])
        if r:
            v = r[1]
            parts.append("%s: %s" % (r[0], v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)))
    if "_raw_text" in case:
        parts.append(case["_raw_text"])
    if task_md_path and os.path.isfile(task_md_path):
        with open(task_md_path, encoding="utf-8", errors="replace") as fh:
            parts.append("task.md:\n" + fh.read())
    t = "\n".join(parts)
    return t[:limit] + ("\n...[truncated]" if len(t) > limit else "")


def page_hint(case, text):
    r = find_key(case, ["pages", "page_range", "pageRange", "page_ranges"])
    if r and isinstance(r[1], (str, int, list)):
        v = r[1]
        if isinstance(v, list):
            v = ",".join(str(x) for x in v)
        return str(v), "case.json:%s" % r[0]
    m = re.search(r"(?:pages?|pp\.?|第)\s*(\d+)\s*(?:-|–|—|to|至|到)\s*(\d+)\s*页?", text or "", re.I)
    if m:
        return "%s-%s" % (m.group(1), m.group(2)), "task text (suggestion only)"
    return None, None


# --------------------------------------------------------------------------
# result assembly
# --------------------------------------------------------------------------

def _loc(doc, b):
    return {"file": os.path.basename(doc["source"]["file"]), "page": b["page"], "block": b["id"],
            "bbox": b["bbox"]}


def auto_value(field, docs, case_id_, status, reason):
    """Best-effort value for a contract field from the parse results."""
    f = field.lower()
    d = docs[0]["doc"] if docs else None
    if f in ("case_id", "caseid", "case", "id"):
        return case_id_
    if f in ("status", "run_status", "state"):
        return status
    if "reason" in f or f in ("message",):
        return reason
    if not d:
        return None
    one = len(docs) == 1

    def per_doc(fn):
        vals = [fn(x["doc"]) for x in docs]
        return vals[0] if one else [{"file": os.path.basename(x["doc"]["source"]["file"]), "value": v}
                                    for x, v in zip(docs, vals)]
    if "title" in f:
        return per_doc(lambda dd: {"text": dd["metadata"]["title"], "block": dd["metadata"]["title_block"]})
    if "author" in f or "affiliation" in f or "front" in f:
        return per_doc(lambda dd: dd["metadata"]["front_matter"])
    if "abstract" in f:
        return per_doc(lambda dd: {"text": dd["metadata"]["abstract"], "blocks": dd["metadata"]["abstract_blocks"],
                                   "inferred": bool(dd["metadata"].get("abstract_inferred"))})
    if "keyword" in f:
        return per_doc(lambda dd: dd["metadata"]["keywords"])
    if any(k in f for k in ("section", "outline", "heading", "hierarch", "structure", "toc", "chapter")):
        return per_doc(lambda dd: [{"id": s["id"], "level": s["level"], "number": s["number"], "title": s["title"],
                                    "page": s["page"], "block": s["block"], "parent": s["parent"]}
                                   for s in dd["sections"]])
    if "table" in f:
        return per_doc(lambda dd: [table_summary(dd, t) for t in dd["tables"]])
    if any(k in f for k in ("figure", "image", "chart", "graphic")):
        return per_doc(lambda dd: [{"id": x["id"], "label": x["label"], "caption": x["caption"]["text"],
                                    "page": x["page"], "bbox": x["bbox"], "crop": x.get("crop")} for x in dd["figures"]])
    if any(k in f for k in ("equation", "formula", "math")):
        return per_doc(lambda dd: [{"id": e["id"], "number": e["number"], "text": e["text"], "page": e["page"],
                                    "bbox": e["bbox"], "crop": e.get("crop")} for e in dd["equations"]])
    if any(k in f for k in ("reference", "bibliograph", "citation")):
        return per_doc(lambda dd: [{"id": r["id"], "label": r["label"], "text": r["text"], "page": r["page"],
                                    "bbox": r["bbox"], "year": r["year"], "doi": r["doi"]} for r in dd["references"]])
    if "footnote" in f:
        return per_doc(lambda dd: dd["footnotes"])
    if any(k in f for k in ("header", "footer", "furniture", "page_number")):
        return per_doc(lambda dd: dd["furniture"])
    if "reading_order" in f or "order" == f:
        return per_doc(lambda dd: [{"block": b["id"], "page": b["page"], "type": b["type"]} for b in dd["blocks"]])
    if any(k in f for k in ("uncertain", "limitation", "warning", "issue", "unknown", "risk")):
        return None  # filled from the uncertainty list by build_result
    if any(k in f for k in ("page",)):
        return per_doc(lambda dd: dd["pages"])
    if any(k in f for k in ("text", "content", "markdown", "body", "parsed", "document", "full", "result",
                            "main", "output", "paragraph", "parse")):
        return per_doc(lambda dd: {"markdown": dd.get("_md_path"), "json": dd.get("_json_path"),
                                   "blocks": len(dd["blocks"]),
                                   "paragraphs": sum(1 for b in dd["blocks"] if b["type"] == "paragraph")})
    return None


def table_summary(doc, t, max_rows=60):
    return {"id": t["id"], "label": t["label"], "caption": t["caption"]["text"], "page": t["page"], "bbox": t["bbox"],
            "nrows": t["nrows"], "ncols": t["ncols"], "header_rows": t["header_rows"], "header": t.get("header"),
            "rows": t.get("matrix", [])[t.get("header_rows", 0):][:max_rows], "csv": t.get("csv"),
            "html": t.get("html_file"), "crop": t.get("crop"), "issues": t.get("issues", []),
            "image_only": t.get("image_only", False)}


def _norm(s):
    s = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("|", " ")  # table rows are shown as "a | b | c" by query.py but stored cell-joined
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_quote(docs, quote, block_id=None, page=None):
    """Check that a quoted passage really occurs in the parsed document."""
    q = _norm(quote)
    if not q:
        return {"verified": False, "how": "empty quote"}
    for x in docs:
        d = x["doc"]
        pools = []
        for b in d["blocks"]:
            pools.append((b, b.get("text", "")))
        for t in d["tables"]:
            for g in t.get("grid", []):
                pools.append(({"id": t["block"] or t["id"], "page": t["page"], "bbox": g["bbox"], "type": "table"},
                              " ".join(c["text"] for c in g["cells"])))
            pools.append(({"id": t["block"] or t["id"], "page": t["page"], "bbox": t["bbox"], "type": "table"},
                          t["caption"]["text"]))
        for r in d["references"]:
            pools.append(({"id": r["block"], "page": r["page"], "bbox": r["bbox"], "type": "reference"}, r["text"]))
        hits = [(b, t) for b, t in pools if q in _norm(t)]
        if hits:
            exact = [h for h in hits if block_id and h[0]["id"] == block_id]
            b = (exact or [h for h in hits if page and h[0]["page"] == page] or hits)[0][0]
            how = "exact match in cited block" if exact else (
                "found in %s (cited %s)" % (b["id"], block_id) if block_id else "found in %s" % b["id"])
            return {"verified": True, "how": how, "file": os.path.basename(d["source"]["file"]),
                    "page": b["page"], "block": b["id"], "bbox": b["bbox"]}
    return {"verified": False, "how": "quote not found in the parsed text (paraphrase, OCR error or not in source)"}


def block_lookup(docs, bid):
    for x in docs:
        for b in x["doc"]["blocks"]:
            if b["id"] == bid:
                return x["doc"], b
        for coll in ("tables", "figures", "equations", "references"):
            for o in x["doc"][coll]:
                if o["id"] == bid:
                    return x["doc"], {"id": o.get("block") or o["id"], "page": o["page"], "bbox": o["bbox"],
                                      "type": coll[:-1], "text": o.get("text") or (o.get("caption") or {}).get("text", "")}
    return None, None


def build_result(case, case_path, cid, docs, parse_failures, answers=None, task_txt="", contract=None,
                 fields=None, missing_materials=None, urls=None, draft=False):
    answers = answers or {}
    unc, not_rep, changes, manual = [], [], [], []
    # status from the parse
    if not docs and not parse_failures:
        status, reason = "failed", "no PDF/image material found in the case input"
    elif not docs:
        status, reason = "failed", "; ".join("%s: %s" % (os.path.basename(f["file"]), f["error"]) for f in parse_failures)
    else:
        parts = [x["doc"]["qa"]["status"] for x in docs]
        status = "partial" if ("partial" in parts or parse_failures) else "success"
        reason = "parsed %d document(s); QA: %s" % (len(docs), ", ".join(parts))
    if draft:
        status = "partial" if status == "success" else status
        reason = "automatic parse finished; task-specific review by the agent pending. " + reason
    for x in docs:
        d = x["doc"]
        fn = os.path.basename(d["source"]["file"])
        for w in d["warnings"]:
            unc.append({"file": fn, "issue": w})
        for c in d["qa"]["checks"]:
            if not c["ok"]:
                unc.append({"file": fn, "issue": "QA check '%s' -> %s" % (c["check"], c["value"]),
                            "detail": {k: c[k] for k in c if k not in ("check", "value", "ok")}})
        for t in d["tables"]:
            if t.get("image_only"):
                not_rep.append({"file": fn, "item": t["label"], "page": t["page"],
                                "reason": "table body has no text layer near the caption (image table?)",
                                "takeover": "inspect %s / page preview" % (t.get("crop") or "crop")})
        for p in d["pages"]:
            if p.get("status") not in ("text", "ocr"):
                manual.append({"file": fn, "page": p["page"], "why": p.get("status")})
        ch = d["changes"]
        changes.append({"file": fn,
                        "page_furniture_removed": len(d["furniture"]),
                        "furniture_examples": [f["text"][:60] for f in d["furniture"][:5]],
                        "dehyphenated_line_breaks": len(ch["dehyphenation"]),
                        "dehyphenation_examples": ch["dehyphenation"][:5],
                        "paragraphs_joined_across_columns_or_pages": ch["merged_paragraphs"],
                        "ligatures_expanded": ch["ligatures_expanded"],
                        "ocr_numeric_corrections": ch.get("ocr_numeric_corrections", [])[:20],
                        "note": "original line text and coordinates are kept in document.json blocks[].lines"})
        if d.get("ocr") and d["ocr"].get("pages"):
            for p in d["ocr"]["pages"]:
                if p["low_conf_words"]:
                    unc.append({"file": fn, "issue": "page %d OCR: %d low-confidence words (mean conf %.0f)" % (
                        p["page"], len(p["low_conf_words"]), p["mean_conf"]),
                        "examples": p["low_conf_words"][:10]})
    for f in parse_failures:
        not_rep.append({"file": os.path.basename(f["file"]), "item": "whole document", "reason": f["error"],
                        "takeover": f.get("advice", "")})
    for m in (missing_materials or []):
        not_rep.append({"item": m["reference"], "reason": "file named in case.json not found in input"})
    for u in (urls or []):
        not_rep.append({"item": u, "reason": "remote URL; not downloaded (offline run)"})
    # agent answers and evidence verification
    ans_out = []
    for a in answers.get("answers", []):
        a = dict(a)
        ev_out = []
        evs = a.get("evidence") or []
        if isinstance(evs, (str, dict)):
            evs = [evs]
        for ev in evs:
            if isinstance(ev, str):
                ev = {"block": ev}
            bid = ev.get("block") or ev.get("block_id") or ev.get("id")
            dd, b = block_lookup(docs, bid) if bid else (None, None)
            item = dict(ev)
            if b:
                item.update({"file": os.path.basename(dd["source"]["file"]), "page": b["page"], "bbox": b["bbox"],
                             "block_type": b.get("type")})
                if not item.get("quote"):
                    item["source_text"] = (b.get("text") or "")[:300]
            elif bid:
                item["resolved"] = False
            if item.get("quote"):
                v = verify_quote(docs, item["quote"], bid, ev.get("page"))
                item["quote_check"] = v
                if not v["verified"]:
                    unc.append({"issue": "unverified quote for answer '%s'" % str(a.get("question", a.get("id", "")))[:80],
                                "quote": item["quote"][:200]})
            ev_out.append(item)
        a["evidence"] = ev_out
        ans_out.append(a)
    unc += [u if isinstance(u, dict) else {"issue": u} for u in answers.get("uncertainties", answers.get("uncertainty", []))]
    not_rep += [n if isinstance(n, dict) else {"item": n} for n in answers.get("not_reported", [])]
    if answers.get("status"):
        status = answers["status"]
        reason = answers.get("status_reason", reason)
    elif answers and not draft:
        reason = "agent reviewed the parse. " + reason
    # contract fields
    res = {"case_id": cid, "status": status, "status_reason": reason}
    given = answers.get("fields", {})
    unfilled = []
    for f in (fields or []):
        if f in given:
            res[f] = given[f]
        elif f in res:
            continue
        else:
            v = auto_value(f, docs, cid, status, reason)
            if v is None and any(k in f.lower() for k in ("uncertain", "limitation", "warning", "issue", "unknown", "risk")):
                v = unc
            if v is None and "not_reported" in f.lower():
                v = not_rep
            if v is None and any(k in f.lower() for k in ("evidence", "source", "locat", "provenance", "citation_loc")):
                v = [e for a in ans_out for e in a.get("evidence", [])] or \
                    [{"file": os.path.basename(x["doc"]["source"]["file"]), "document_json": x["doc"].get("_json_path"),
                      "note": "every block in document.json has page + bbox"} for x in docs]
            if v is None and any(k in f.lower() for k in ("answer", "finding", "conclusion")):
                v = ans_out or None
            if v is None and any(k in f.lower() for k in ("change", "edit", "correction", "modif")):
                v = changes
            if v is None:
                unfilled.append(f)
            res[f] = v
    for k, v in given.items():
        res.setdefault(k, v)
    if unfilled and status == "success":
        res["status"] = "partial"
        res["status_reason"] = reason + "; contract fields not filled: " + ", ".join(unfilled)
    res.update({
        "answers": ans_out,
        "materials": [{"file": x["doc"]["source"]["file"], "sha256": x["doc"]["source"]["sha256"],
                       "pages_total": x["doc"]["source"].get("pages_total"),
                       "pages_parsed": x["doc"]["source"].get("pages_parsed"),
                       "backend": x["doc"]["engine"]["backend"], "document_json": x["doc"].get("_json_path"),
                       "document_md": x["doc"].get("_md_path"), "parse_report": x["doc"].get("_report_path"),
                       "tables_dir": x["files"].get("tables_dir"), "previews": x["doc"].get("previews", [])[:3],
                       "qa_status": x["doc"]["qa"]["status"],
                       "token_recall_vs_independent_extractor": next(
                           (v for k, v in x["doc"]["qa"].items() if k.startswith("token_recall")), None)}
                      for x in docs],
        "structure_summary": [{"file": os.path.basename(x["doc"]["source"]["file"]),
                               "title": x["doc"]["metadata"]["title"],
                               "sections": len(x["doc"]["sections"]), "tables": len(x["doc"]["tables"]),
                               "figures": len(x["doc"]["figures"]), "equations": len(x["doc"]["equations"]),
                               "footnotes": len(x["doc"]["footnotes"]), "references": len(x["doc"]["references"]),
                               "columns_per_page": sorted(set(p.get("columns") for p in x["doc"]["pages"] if p.get("columns")))}
                              for x in docs],
        "uncertainties": unc,
        "not_reported": not_rep,
        "manual_takeover": manual + answers.get("manual_takeover", []),
        "change_records": changes + answers.get("change_records", []),
        "contract": {"source": contract, "fields": fields or [], "auto_filled": [f for f in (fields or []) if f not in given and f not in unfilled],
                     "agent_filled": [f for f in (fields or []) if f in given], "unfilled": unfilled},
        "run": {"tool": "weekly-skill pdfdoc", "draft": draft, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "case_json": os.path.abspath(case_path)},
    })
    return res

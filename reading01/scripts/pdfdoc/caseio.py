"""Case input discovery, output-contract handling and result.json assembly."""
from __future__ import annotations

import glob
import json
import os
import re
import time
import unicodedata

MATERIAL_RE = re.compile(r"\.(pdf|png|jpe?g|tiff?|bmp|webp)$", re.I)
PDF_RE = re.compile(r"\.pdf$", re.I)
TASK_KEYS = ("task", "tasks", "instruction", "instructions", "prompt", "question", "questions", "query", "queries",
             "requirement", "requirements", "goal", "objective", "description", "request", "subtasks", "items")
# searched in this order; the first key found anywhere in case.json wins
CONTRACT_KEYS_STRONG = ("output_contract", "outputContract", "output_schema", "outputSchema", "result_schema",
                        "resultSchema", "required_fields", "requiredFields", "required_outputs", "expected_output",
                        "expectedOutput", "response_format", "deliverables")
# generic names: only accepted when the value really describes fields (not a path like "/app/output")
CONTRACT_KEYS_WEAK = ("outputs", "output")
FILE_RE = re.compile(r"^[\w\-./]+\.(json|md|markdown|csv|tsv|html?|txt|png|jpe?g|svg|pdf|tex|docx|xlsx|pptx|xml|yaml|yml)$",
                     re.I)
TYPE_WORDS = {"string": "string", "str": "string", "text": "string", "markdown": "string",
              "integer": "integer", "int": "integer", "number": "number", "float": "number",
              "boolean": "boolean", "bool": "boolean",
              "array": "array", "list": "array", "object": "object", "dict": "object", "map": "object"}
STANDARD_KEYS = ("answers", "sources", "uncertainties", "not_reported", "change_records", "manual_takeover",
                 "materials", "structure_summary", "contract", "run")


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


def find_all_keys(o, name, path=""):
    """Every (path, key, value) whose key equals name (case-insensitive)."""
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if str(k).lower() == name.lower():
                out.append((path + "/" + str(k), k, v))
            out += find_all_keys(v, name, path + "/" + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out += find_all_keys(v, name, path + "[%d]" % i)
    return out


def case_id(case, path):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "case", "id", "uid", "name"):
            for kk, v in case.items():
                if str(kk).lower() == k.lower() and isinstance(v, (str, int)) and str(v).strip():
                    return str(v)
        for k in ("case_id", "caseId"):
            r = find_key(case, [k])
            if r and isinstance(r[1], (str, int)) and str(r[1]).strip():
                return str(r[1])
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem and stem.lower() not in ("case", "input"):
        return stem
    return os.path.basename(os.path.dirname(os.path.abspath(path))) or "case"


def find_materials(case, case_path, extra_dirs=("/input",)):
    """Materials to parse.

    Files named in case.json are always used. Other files lying in the input
    directories are parsed only when case.json names none (then: every PDF, or
    every image if there is no PDF); otherwise they are reported in `others`
    so the agent can add them with --material."""
    named, missing, urls, present = _find_materials(case, case_path, extra_dirs)
    others = [f for f in present if os.path.abspath(f) not in [os.path.abspath(x) for x in named]]
    if named:
        return named, missing, urls, others
    pdfs = [f for f in present if PDF_RE.search(f)]
    found = pdfs or present
    if not found:
        # fallback: look in other common mount points (never our own output / skill dirs)
        for d in ("/app/input", "/app/data", "/app/materials", "/data", "/mnt/data", "/workspace", "/app/work", os.getcwd()):
            if not os.path.isdir(d):
                continue
            hits = []
            for f in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True))[:5000]:
                if os.path.isfile(f) and MATERIAL_RE.search(f) and "/app/output" not in f and "/skills/" not in f \
                        and "pdfdoc_" not in f:
                    hits.append(f)
            if hits:
                found = [f for f in hits if PDF_RE.search(f)] or hits
                break
    others = [f for f in present if f not in found]
    return found, missing, urls, others


def _in_contract(path):
    keys = [k.lower() for k in re.findall(r"/([^/\[\]]+)", path)]
    return any(k in [c.lower() for c in CONTRACT_KEYS_STRONG + CONTRACT_KEYS_WEAK] for k in keys)


def _find_materials(case, case_path, extra_dirs=("/input",)):
    base_dirs = [os.path.dirname(os.path.abspath(case_path))] + [d for d in extra_dirs if os.path.isdir(d)]
    named, missing, urls, present = [], [], [], []
    seen = set()
    for p, v in _walk(case):
        if not isinstance(v, str) or _in_contract(p):
            continue  # file names inside the output contract are outputs, not inputs
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
            if os.path.abspath(hit) not in seen:
                seen.add(os.path.abspath(hit))
                named.append(hit)
        else:
            missing.append({"reference": s, "json_path": p})
    # files present in the input directories even if case.json does not name them
    pseen = set()
    for d in base_dirs:
        for f in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
            if os.path.isfile(f) and MATERIAL_RE.search(f) and "/app/output" not in f and "pdfdoc_" not in f:
                if os.path.abspath(f) not in pseen:
                    pseen.add(os.path.abspath(f))
                    present.append(f)
    return named, missing, urls, present


# --------------------------------------------------------------------------
# output contract
# --------------------------------------------------------------------------

def _describes_fields(v):
    """True when a generic "output"/"outputs" value lists result fields (not e.g. a directory path)."""
    if isinstance(v, (dict, list)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith(("/", "./", "~")) or FILE_RE.match(s):
            return False
        return bool(re.search(r"`[A-Za-z_][\w.]*`", s)) or len(re.findall(r"\b[a-z]+_[a-z_]+\b", s)) >= 2
    return False


def find_contract(case):
    """(contract dict {key: value} or None, required field names)."""
    for k in CONTRACT_KEYS_STRONG:
        r = find_key(case, [k])
        if r and r[1] not in (None, "", [], {}):
            return {r[0]: r[1]}, contract_fields(r[1])
    for k in CONTRACT_KEYS_WEAK:
        for _p, key, v in find_all_keys(case, k):
            if _describes_fields(v):
                return {key: v}, contract_fields(v)
    return None, []


SCHEMA_META = ("description", "format", "type", "notes", "note", "title", "$schema", "$id", "required",
               "additionalProperties", "examples", "definitions", "$defs")


def _is_meta_key(d, k):
    """Keys of a contract dict that describe the contract itself rather than a result field.
    A flat contract ({"case_id": "string", "title": "string", "notes": "..."}) keeps "title",
    "notes", "description" as fields; they are meta keys only in a JSON-Schema-like dict."""
    v = d[k]
    if k in ("$schema", "$id"):
        return True
    if FILE_RE.match(str(k)):
        return True
    if k in ("file", "path", "filename") and isinstance(v, str) and FILE_RE.match(v.strip()):
        return True
    if k == "files" and isinstance(v, list) and all(isinstance(x, str) for x in v):
        return True
    schema_like = d.get("type") in ("object", "array") or "required" in d or "additionalProperties" in d \
        or "$schema" in d
    return schema_like and k in SCHEMA_META


def contract_fields(val):
    """Field names required by an output contract in any common shape."""
    fields = []
    if isinstance(val, dict):
        props = val.get("properties") if isinstance(val.get("properties"), dict) else None
        req = val.get("required") if isinstance(val.get("required"), list) else None
        flds = None
        for k in ("fields", "required_fields", "keys", "result_fields", "result.json", "result_json", "result",
                  "schema"):
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
            fields = [k for k in val.keys() if not _is_meta_key(val, k)]
    elif isinstance(val, list):
        for x in val:
            if isinstance(x, str):
                f = x.split(":")[0].strip()
                if not FILE_RE.match(f):
                    fields.append(f)
            elif isinstance(x, dict):
                n = x.get("name") or x.get("field") or x.get("key") or x.get("id")
                if n:
                    fields.append(str(n))
    elif isinstance(val, str):
        fields = re.findall(r"`([A-Za-z_][\w.]*)`", val) or re.findall(r"\b([a-z]+_[a-z_]+)\b", val)
        fields = [f for f in fields if not FILE_RE.match(f)]
    out = []
    for f in fields:
        f = str(f).strip()
        if f and f not in out and len(f) <= 80:
            out.append(f)
    return out


def contract_files(contract):
    """File names (other than result.json) that the contract asks for."""
    out = []
    if not contract:
        return out

    def add(s):
        s = s.strip()
        if FILE_RE.match(s) and os.path.basename(s) != "result.json" and s not in out:
            out.append(s)
    for p, v in _walk(contract):
        for part in re.split(r"[/\[\]]", p):
            if part:
                add(part)
        if isinstance(v, str):
            for m in re.findall(r"`([^`\s]+)`", v):
                add(m)
            if FILE_RE.match(v.strip()):
                add(v)
    return out


def _type_of(spec):
    """Expected JSON type of a contract field spec, or None if not stated."""
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), None)
        if isinstance(t, str) and t.lower() in TYPE_WORDS:
            return TYPE_WORDS[t.lower()]
        if "items" in spec:
            return "array"
        if "properties" in spec:
            return "object"
        return None
    if isinstance(spec, list):
        return "array"
    if isinstance(spec, str):
        s = spec.strip().lower()
        m = re.match(r"^(list|array|\[)", s)
        if m:
            return "array"
        m = re.match(r"^(dict|object|map|\{)", s)
        if m:
            return "object"
        w = re.match(r"^([a-z]+)\b", s)
        if w and w.group(1) in TYPE_WORDS and (len(s.split()) <= 3 or re.match(r"^[a-z]+\s*[(:,\-—]", s)):
            return TYPE_WORDS[w.group(1)]
    return None


def _items_of(spec):
    """Item type of a list spec given as text: "list of strings", "array[string]", "list[str]"."""
    if isinstance(spec, str):
        m = re.match(r"^\s*(?:list|array)\s*(?:of\s+|\[|<)\s*([a-z]+)", spec.strip().lower())
        if m:
            w = m.group(1).rstrip("s") if m.group(1) not in ("bools",) else "bool"
            return TYPE_WORDS.get(m.group(1)) or TYPE_WORDS.get(w)
    if isinstance(spec, list) and spec and all(isinstance(x, str) for x in spec):
        return None
    return None


def contract_specs(contract):
    """{field: {"type": json type or None, "description": str or None}} from the contract."""
    if not contract:
        return {}
    val = list(contract.values())[0]
    specs = {}

    def from_dict(d):
        props = d.get("properties") if isinstance(d.get("properties"), dict) else None
        if props:
            for k, v in props.items():
                specs[k] = {"type": _type_of(v), "description": v.get("description") if isinstance(v, dict) else None}
                if isinstance(v, dict) and isinstance(v.get("items"), dict):
                    specs[k]["items"] = _type_of(v["items"])
            return
        for k in ("fields", "required_fields", "keys", "result_fields", "result.json", "result_json", "result", "schema"):
            if k in d:
                walk(d[k])
                return
        for k, v in d.items():
            if _is_meta_key(d, k):
                continue
            specs[k] = {"type": _type_of(v), "description": v if isinstance(v, str) else (
                v.get("description") if isinstance(v, dict) else None), "items": _items_of(v)}
            if isinstance(v, dict) and isinstance(v.get("items"), dict):
                specs[k]["items"] = _type_of(v["items"])

    def walk(v):
        if isinstance(v, dict):
            from_dict(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    n = x.get("name") or x.get("field") or x.get("key") or x.get("id")
                    if n:
                        specs[str(n)] = {"type": _type_of(x), "description": x.get("description")}
                elif isinstance(x, str) and ":" in x:
                    n, desc = x.split(":", 1)
                    specs[n.strip()] = {"type": _type_of(desc), "description": desc.strip(), "items": _items_of(desc)}
    walk(val)
    return specs


GENERIC_TASK_MD_RE = re.compile(r"SKILL\.md|/app/submission|评分维度|Skill authoring task|交付要求")


def task_text(case, task_md_path=None, limit=16000, display=False):
    """Task wording from case.json (task keys first, then every other top-level key such as
    constraints / evidence scope / target language) plus task.md.

    display=True: what run_case prints. A task.md that is the generic topic statement for Skill
    authors (it mentions SKILL.md, /app/submission, the scoring table ...) is shortened to its
    opening lines when case.json has its own task, so it does not crowd every later turn."""
    parts = []
    nc = {k: v for k, v in case.items() if not _in_contract("/" + str(k))} if isinstance(case, dict) else case
    seen = set()
    for k in TASK_KEYS:
        r = find_key(nc, [k])
        if r:
            v = r[1]
            seen.add(str(r[0]).split("/")[-1].split(".")[-1])
            parts.append("%s: %s" % (r[0], v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)))
    has_task = bool(parts)
    if isinstance(nc, dict):
        extra = {k: v for k, v in nc.items() if k not in seen and k not in TASK_KEYS and not str(k).startswith("_")
                 and k not in ("case_id", "caseId", "id")}
        if extra:
            ej = json.dumps(extra, ensure_ascii=False)
            parts.append("other case.json fields (constraints, materials, scope ...): " +
                         (ej if len(ej) <= 4000 else ej[:4000] + " ...[read case.json for the rest]"))
    if "_raw_text" in case:
        parts.append(case["_raw_text"])
    if task_md_path and os.path.isfile(task_md_path):
        with open(task_md_path, encoding="utf-8", errors="replace") as fh:
            md = fh.read()
        if display and has_task and len(md) > 1500 and GENERIC_TASK_MD_RE.search(md):
            parts.append("task.md (%d chars) is the general topic statement written for Skill authors; the case "
                         "task is the case.json text above. Opening lines:\n%s\n...[full text: %s - its "
                         "'create SKILL.md / submit to /app/submission' parts are not this case's job]" % (
                             len(md), md[:900].rstrip(), task_md_path))
        else:
            parts.append("task.md:\n" + md)
    t = "\n".join(parts)
    if len(t) > limit:
        return t[:limit] + "\n...[TRUNCATED at %d characters: read %s and case.json in full before answering]" % (
            limit, task_md_path or "task.md")
    return t


def fields_in_text(text):
    """Field names that task.md mentions for result.json (`name` inside lines about the output)."""
    out = []
    for line in (text or "").splitlines():
        if not re.search(r"result\.json|output_contract|输出|字段|field|key", line, re.I):
            continue
        for f in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", line):
            if f not in out and not FILE_RE.match(f) and f not in ("result", "json"):
                out.append(f)
    return out


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
# automatic values for contract fields
# --------------------------------------------------------------------------

def _loc(doc, b):
    return {"file": os.path.basename(doc["source"]["file"]), "page": b["page"], "block": b["id"],
            "bbox": b["bbox"]}


def field_tokens(field):
    f = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(field))
    toks = re.findall(r"[a-z]+|\d+|[一-鿿]+", f.lower())
    return toks


ZH_MAP = (("标题", "title"), ("题目", "title"), ("作者", "authors"), ("单位", "affiliations"), ("摘要", "abstract"),
          ("关键词", "keywords"), ("章节", "sections"), ("目录", "sections"), ("层级", "sections"), ("表格", "tables"),
          ("表", "tables"), ("图", "figures"), ("公式", "equations"), ("参考文献", "references"), ("文献", "references"),
          ("脚注", "footnotes"), ("页眉", "furniture"), ("页脚", "furniture"), ("页码", "page"), ("证据", "evidence"),
          ("来源", "sources"), ("依据", "evidence"), ("答案", "answers"), ("回答", "answers"), ("结论", "conclusion"),
          ("不确定", "uncertainties"), ("未报告", "not_reported"), ("状态", "status"), ("原因", "reason"),
          ("修改", "change"), ("变更", "change"), ("段落", "paragraphs"), ("全文", "full_text"), ("正文", "full_text"),
          ("语言", "language"), ("翻译", "translation"), ("译文", "translation"), ("中译", "translation"),
          ("英译", "translation"), ("总结", "summary"), ("概括", "summary"), ("综述", "summary"), ("知识", "knowledge"),
          ("实体", "entity"), ("关系", "relation"), ("分析", "analysis"), ("比较", "comparison"), ("对比", "comparison"),
          ("核验", "verification"), ("冲突", "conflict"), ("解释", "explanation"), ("贡献", "contribution"))

# Field names that ask for the agent's own work on the paper (translation, summary, knowledge
# structure, comparison, verification ...). The parse holds only the original text, so such a
# field is never filled automatically: "translated_title" must not receive the English title.
TASK_DERIVED_PREFIXES = ("translat", "summar", "analy", "compar", "knowledge", "entit", "relation", "triple", "claim",
                         "judg", "verdict", "verif", "conflict", "explan", "interpret", "insight", "highlight",
                         "glossar", "bilingual", "contribution", "critique", "review", "recommend", "takeaway")
TASK_DERIVED_WORDS = {"zh", "cn", "chinese", "english", "en", "kg", "graph", "concept", "concepts", "mindmap", "gist"}


def task_derived(field):
    toks = _norm_tokens(field)
    return any(t.startswith(TASK_DERIVED_PREFIXES) for t in toks) or bool(set(toks) & TASK_DERIVED_WORDS)


def _norm_tokens(field):
    toks = field_tokens(field)
    out = []
    for t in toks:
        if re.match(r"[一-鿿]", t):
            for zh, en in ZH_MAP:
                if zh in t:
                    out += field_tokens(en)
                    break
        else:
            out.append(t)
    return out


def _sing(t):
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def _lang_of(docs):
    txt = " ".join(b.get("text", "") for x in docs for b in x["doc"]["blocks"][:200])
    cjk = len(re.findall(r"[一-鿿]", txt))
    lat = len(re.findall(r"[A-Za-z]", txt))
    if cjk and lat:
        return "zh" if cjk * 2 >= lat else ("en" if cjk * 10 < lat else "zh+en")
    return "zh" if cjk else ("en" if lat else None)


def _doc_text(d, limit=400000):
    p = d.get("_md_path")
    if p and os.path.isfile(p):
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()[:limit]
    return "\n\n".join(b.get("text", "") for b in d["blocks"] if b.get("type") not in ("empty",))[:limit]


def auto_value(field, docs, case_id_, status, reason):
    """Best-effort (value, source note, locator) for a contract field from the parse.

    Matching is on whole tokens of the field name (``page_count`` -> page + count),
    never on substrings; a field with no clear meaning returns (None, ...) and is
    left for the agent to fill."""
    toks = _norm_tokens(field)
    ts = set(_sing(t) for t in toks)
    f = str(field).lower()
    if f in ("case_id", "caseid", "case", "id", "case_name") or ts == {"case", "id"}:
        return case_id_, "case id", None
    if f in ("status", "run_status", "state", "run_state") or ts == {"status"} or ts == {"run", "status"}:
        return status, "run status", None
    if ts & {"reason", "message"} and ts <= {"status", "reason", "message", "run", "failure", "error"}:
        return reason, "status reason", None
    if task_derived(field):
        return None, None, None
    if not docs:
        return None, None, None
    one = len(docs) == 1

    def per_doc(fn):
        vals = [fn(x["doc"]) for x in docs]
        return vals[0] if one else [{"file": os.path.basename(x["doc"]["source"]["file"]), "value": v}
                                    for x, v in zip(docs, vals)]

    def locs(fn):
        out = []
        for x in docs:
            for bid in fn(x["doc"]):
                b = next((bb for bb in x["doc"]["blocks"] if bb["id"] == bid), None)
                if b:
                    out.append(_loc(x["doc"], b))
        return out
    count = bool(ts & {"count", "num", "total", "n"}) or f.startswith(("number_of", "num_", "n_")) or \
        bool(re.match(r"^n[A-Z]", str(field)))
    objects = (("page", lambda d: len(d["source"].get("pages_parsed") or [])), ("table", lambda d: len(d["tables"])),
               ("figure", lambda d: sum(1 for x in d["figures"] if x["kind"] == "figure")),
               ("equation", lambda d: len(d["equations"])), ("formula", lambda d: len(d["equations"])),
               ("reference", lambda d: len(d["references"])), ("section", lambda d: len(d["sections"])),
               ("heading", lambda d: len(d["sections"])), ("footnote", lambda d: len(d["footnotes"])),
               ("paragraph", lambda d: sum(1 for b in d["blocks"] if b["type"] == "paragraph")),
               ("block", lambda d: len(d["blocks"])), ("column", lambda d: max([p.get("columns") or 0 for p in d["pages"]] or [0])))
    if count:
        for name, fn in objects:
            if name in ts:
                if name == "page" and "total" in ts:
                    return per_doc(lambda d: d["source"].get("pages_total")), "pages in the PDF", None
                return per_doc(fn), "number of %ss in the parse" % name, None
        return None, None, None
    if "language" in ts or "lang" in ts:
        return _lang_of(docs), "script detected in the text layer", None
    if "title" in ts and not ts & {"section", "table", "figure", "chapter", "heading", "reference", "caption"}:
        return (per_doc(lambda d: d["metadata"]["title"]), "title block",
                locs(lambda d: [d["metadata"]["title_block"]] if d["metadata"]["title_block"] else []))
    if ts & {"metadata", "meta", "bibliographic", "front"} and not ts & {"page", "table", "figure"}:
        return per_doc(_metadata_value), "title / authors / affiliations / abstract blocks with locators", None
    if ts & {"affiliation", "institution", "organization", "organisation"} and not ts & {"author"}:
        def affs(d):
            m = d["metadata"]
            if m.get("affiliations"):
                return m["affiliations"]
            return [x["text"] for x in m["front_matter"]]
        return per_doc(affs), "affiliations (page-1 front matter)", None
    if ts & {"email", "mail", "contact"}:
        return per_doc(lambda d: d["metadata"].get("emails") or []), "e-mail addresses (page-1 front matter)", None
    if ts & {"author", "front"}:
        def auths(d):
            m = d["metadata"]
            if m.get("authors"):
                return m["authors"]
            return [x["text"] for x in m["front_matter"]]
        return (per_doc(auths), "authors with affiliation / e-mail where the layout makes it unambiguous "
                "(affiliation_rule tells how); otherwise front-matter blocks", locs(lambda d: [x["block"] for x in d["metadata"]["front_matter"]]))
    if "ocr" in ts or (ts & {"recognition"} and ts & {"quality", "confidence"}):
        return per_doc(_ocr_value), "OCR engine, per-page language / mean confidence / low-confidence words", None
    if ts & {"block", "chunk", "segment", "span"} and (ts & {"text", "content"} or ts <= {"block", "chunk", "segment", "span"}):
        return (per_doc(lambda dd: [{"id": b["id"], "page": b["page"], "type": b["type"], "bbox": b["bbox"],
                                     "section": b.get("section"), "text": b["text"]}
                                    for b in dd["blocks"] if b["type"] not in ("empty",)]),
                "text blocks in reading order (id, page, bbox, type)", None)
    if "abstract" in ts:
        return (per_doc(lambda d: d["metadata"]["abstract"]), "abstract block(s)%s" % (
            " (inferred from layout)" if any(x["doc"]["metadata"].get("abstract_inferred") for x in docs) else ""),
            locs(lambda d: d["metadata"]["abstract_blocks"]))
    if "keyword" in ts or ts >= {"index", "term"}:
        def kws(d):
            k = d["metadata"]["keywords"]
            if not k:
                return None
            k = re.sub(r"^(keywords|key\s+words|index\s+terms|关键词)\s*[:：—–-]?\s*", "", k, flags=re.I)
            return [x.strip() for x in re.split(r"[;；,，、·]", k) if x.strip()]
        return per_doc(kws), "keywords line", None
    if ts & {"section", "outline", "heading", "hierarchy", "toc", "chapter", "structure"}:
        return (per_doc(lambda dd: [{"id": s["id"], "level": s["level"], "number": s["number"], "title": s["title"],
                                     "page": s["page"], "block": s["block"], "bbox": s.get("bbox"), "parent": s["parent"]}
                                    for s in dd["sections"]]), "section tree", None)
    if "table" in ts:
        return per_doc(lambda dd: [table_summary(dd, t) for t in dd["tables"]]), "tables (cells)", None
    if ts & {"figure", "image", "chart", "graphic", "fig"}:
        return (per_doc(lambda dd: [{"id": x["id"], "label": x["label"], "caption": x["caption"]["text"],
                                     "page": x["page"], "bbox": x["bbox"], "crop": x.get("crop")} for x in dd["figures"]]),
                "figures (caption + region)", None)
    if ts & {"equation", "formula", "math"}:
        return (per_doc(lambda dd: [{"id": e["id"], "number": e["number"], "text": e["text"], "page": e["page"],
                                     "bbox": e["bbox"], "crop": e.get("crop")} for e in dd["equations"]]),
                "display equations", None)
    if ts & {"reference", "bibliography"} or ts == {"citation"}:
        return (per_doc(lambda dd: [{"id": r["id"], "label": r["label"], "text": r["text"], "page": r["page"],
                                     "bbox": r["bbox"], "year": r["year"], "doi": r["doi"]} for r in dd["references"]]),
                "reference list", None)
    if "footnote" in ts:
        return per_doc(lambda dd: dd["footnotes"]), "footnotes", None
    if ts & {"furniture"} or ts >= {"running", "header"} or ts >= {"page", "header"} or ts >= {"page", "footer"} \
            or ts == {"header"} or ts == {"footer"} or ts >= {"header", "footer"}:
        return per_doc(lambda dd: dd["furniture"]), "removed page furniture", None
    if ("reading" in ts and "order" in ts) or f == "order":
        return (per_doc(lambda dd: [{"block": b["id"], "page": b["page"], "type": b["type"]} for b in dd["blocks"]]),
                "reading order", None)
    if ts & {"uncertain", "uncertainty", "limitation", "warning", "issue", "unknown", "risk"}:
        return None, None, None  # filled from the uncertainty list by build_result
    if ts == {"page"} or ts <= {"page", "info", "status", "summary", "detail", "quality"} and "page" in ts:
        return per_doc(lambda dd: dd["pages"]), "per-page status", None
    if "page" in ts and ts & {"parsed", "range", "processed", "covered"}:
        return per_doc(lambda dd: dd["source"].get("pages_parsed")), "pages parsed", None
    if "paragraph" in ts:
        return (per_doc(lambda dd: [{"block": b["id"], "page": b["page"], "bbox": b["bbox"], "text": b["text"]}
                                    for b in dd["blocks"] if b["type"] == "paragraph"]), "paragraph blocks", None)
    if ts & {"path", "file", "location", "uri", "url"} and ts & {"markdown", "md", "parsed", "document", "text", "json"}:
        if "json" in ts:
            return per_doc(lambda dd: dd.get("_json_path")), "document.json path", None
        return per_doc(lambda dd: dd.get("_md_path")), "document.md path", None
    text_words = {"text", "content", "body", "full", "main", "document", "parsed", "plain", "raw", "markdown", "md",
                  "paper", "pdf", "extracted", "extraction", "reading", "order", "fulltext"}
    if ts & {"markdown", "md", "text", "content", "body", "fulltext"} and ts <= text_words:
        return per_doc(_doc_text), "document.md (reading-order text with block anchors)", None
    if ts <= {"parsed", "document", "parse", "output"} and ts & {"parsed", "document", "parse"}:
        return (per_doc(lambda dd: {"markdown": dd.get("_md_path"), "json": dd.get("_json_path"),
                                    "blocks": len(dd["blocks"]),
                                    "paragraphs": sum(1 for b in dd["blocks"] if b["type"] == "paragraph")}),
                "pointer to the parse outputs", None)
    return None, None, None


def _metadata_value(d):
    m = d["metadata"]
    byid = {b["id"]: b for b in d["blocks"]}

    def loc(bid):
        b = byid.get(bid)
        return {"page": b["page"], "block": bid, "bbox": b["bbox"]} if b else None
    out = {"file": os.path.basename(d["source"]["file"]), "pages_total": d["source"].get("pages_total"),
           "title": {"text": m.get("title"), "locator": loc(m.get("title_block"))} if m.get("title") else None,
           "authors": m.get("authors") or [x["text"] for x in m["front_matter"]],
           "affiliations": m.get("affiliations") or [],
           "emails": [e["email"] for e in m.get("emails") or []],
           "front_matter_blocks": [loc(x["block"]) for x in m["front_matter"]],
           "abstract": {"text": m.get("abstract"), "locators": [loc(b) for b in m.get("abstract_blocks", [])],
                        "inferred_from_layout": bool(m.get("abstract_inferred"))} if m.get("abstract") else None,
           "keywords": m.get("keywords")}
    return out


def _ocr_value(d):
    o = d.get("ocr") or {}
    pages = [p for p in d["pages"] if p.get("status") == "ocr" or str(p.get("source", "")).startswith("ocr")]
    if not o and not pages:
        return {"ocr_used": False, "note": "all parsed pages have a text layer; no OCR was needed"}
    per = []
    for p in o.get("pages", []):
        per.append({"page": p["page"], "lang": p.get("lang"), "mean_conf": p.get("mean_conf"), "words": p.get("words"),
                    "low_conf_words": [{"text": w[0], "conf": w[1], "bbox": w[2]} for w in p.get("low_conf_words", [])],
                    "preprocess": p.get("preprocess")})
    confs = [p["mean_conf"] for p in per if p.get("mean_conf") is not None]
    return {"ocr_used": True, "engine": o.get("engine"), "pages": per,
            "mean_conf": round(sum(confs) / len(confs), 1) if confs else None,
            "low_conf_word_count": sum(len(p["low_conf_words"]) for p in per),
            "numeric_corrections": (d.get("changes") or {}).get("ocr_numeric_corrections", [])[:50],
            "note": "tesseract word confidence 0-100; words below the threshold are listed with page bbox (PDF pt)"}


def json_type(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def _item_text(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for k in ("issue", "item", "text", "answer", "reason", "note", "quote"):
            if isinstance(x.get(k), str):
                extra = ["%s: %s" % (kk, x[kk]) for kk in ("file", "page", "block") if x.get(kk) is not None]
                return x[k] + (" (%s)" % ", ".join(extra) if extra else "")
    return json.dumps(x, ensure_ascii=False)


def fit_items(v, item_type):
    """Make list items match a declared item type (e.g. strings); None if impossible."""
    if not isinstance(v, list) or not item_type:
        return v
    if all(type_ok(x, item_type) for x in v):
        return v
    if item_type == "string":
        return [_item_text(x) for x in v]
    return None


def type_ok(v, expected):
    if not expected or v is None:
        return True
    t = json_type(v)
    return t == expected or (expected == "number" and t == "integer")


def coerce(v, expected):
    """Fit an automatic value to the contract's declared JSON type; None if it cannot be done safely."""
    if v is None or type_ok(v, expected):
        return v
    t = json_type(v)
    if expected == "string":
        if t == "object" and isinstance(v.get("text"), str):
            return v["text"]
        if t == "array" and all(isinstance(x, str) for x in v):
            return "; ".join(v)
        if t in ("integer", "number", "boolean"):
            return str(v)
        return None
    if expected == "array":
        return [v]
    if expected in ("integer", "number"):
        if t == "string" and re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
            return int(v) if expected == "integer" and "." not in v else float(v)
        return None
    if expected == "object":
        return None
    return None


def table_summary(doc, t, max_rows=150):
    body = t.get("matrix", [])[t.get("header_rows", 0):]
    o = {"id": t["id"], "label": t["label"], "caption": t["caption"]["text"], "page": t["page"], "bbox": t["bbox"],
         "nrows": t["nrows"], "ncols": t["ncols"], "header_rows": t["header_rows"], "header": t.get("header"),
         "rows": body[:max_rows], "csv": t.get("csv"),
         "html": t.get("html_file"), "crop": t.get("crop"), "issues": t.get("issues", []),
         "image_only": t.get("image_only", False)}
    if len(body) > max_rows:
        o["rows_truncated"] = {"shown": max_rows, "total": len(body), "full_table": t.get("csv")}
    for k in ("body_source", "continued_from", "continued_in", "uncaptioned"):
        if t.get(k):
            o[k] = t[k]
    if t.get("merged"):
        o["merged"] = {k: t["merged"][k] for k in ("parts", "nrows", "csv") if k in t["merged"]}
        o["merged"]["rows"] = t["merged"].get("matrix", [])[t.get("header_rows", 0):][:max_rows * 2]
    return o


# --------------------------------------------------------------------------
# quote verification
# --------------------------------------------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"',
                         "„": '"', "′": "'", "″": '"', "–": "-", "—": "-", "−": "-",
                         "‐": "-", "‑": "-", "­": None, "​": None, " ": " "})
_CJK = r"　-〿㐀-䶿一-鿿豈-﫿＀-￯"


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "").translate(_QUOTES)
    s = re.sub(r"\\([_*`#\[\]|<>~])", r"\1", s)  # Markdown escapes copied from document.md (conv3\_x)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", s)
    s = s.replace("|", " ")  # table rows are shown as "a | b | c" by query.py but stored cell-joined
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"(?<=[%s]) (?=\S)|(?<=\S) (?=[%s])" % (_CJK, _CJK), "", s)
    return s


def _loose(s):
    """Letters, digits and CJK only: tolerant of spacing, punctuation and line-break differences."""
    return re.sub(r"[^0-9a-z%s]+" % _CJK, "", _norm(s).replace("-", ""))


def _quote_pools(d):
    pools = []
    byid = {b["id"]: b for b in d["blocks"]}
    for b in d["blocks"]:
        pools.append(({"id": b["id"], "page": b["page"], "bbox": b["bbox"], "type": b.get("type"),
                       "obj": b.get("ref")}, b.get("text", "")))
    # paragraphs that run across columns/pages, joined as in document.md
    for b in d["blocks"]:
        if b.get("continued_by") and not b.get("continues"):
            ids, txt, cur = [b["id"]], b.get("text", ""), b
            while cur.get("continued_by") in byid:
                cur = byid[cur["continued_by"]]
                ids.append(cur["id"])
                txt = (txt[:-1] if re.search(r"[A-Za-z]-$", txt) and re.match(r"^[a-z]", cur.get("text", ""))
                       else txt + " ") + cur.get("text", "")
            pools.append(({"id": b["id"], "page": b["page"], "bbox": b["bbox"], "type": b.get("type"),
                           "chain": ids}, txt))
    for t in d["tables"]:
        tid = t.get("block") or t["id"]
        for g in t.get("grid", []):
            pools.append(({"id": tid, "obj": t["id"], "page": t["page"], "bbox": g["bbox"], "type": "table"},
                          " ".join(c["text"] for c in g["cells"])))
        # rows as query.py table / the CSV show them: rowspan labels repeated, header joined
        grid = t.get("grid", [])
        for ri, row in enumerate(t.get("matrix") or []):
            bb = grid[ri]["bbox"] if ri < len(grid) and len(grid) == len(t.get("matrix") or []) else t["bbox"]
            pools.append(({"id": tid, "obj": t["id"], "page": t["page"], "bbox": bb, "type": "table"},
                          " ".join(str(c) for c in row)))
        if t.get("header"):
            pools.append(({"id": tid, "obj": t["id"], "page": t["page"], "bbox": t["bbox"], "type": "table"},
                          " ".join(str(c) for c in t["header"])))
        for row in (t.get("merged") or {}).get("matrix") or []:
            pools.append(({"id": tid, "obj": t["id"], "page": t["page"], "bbox": t["bbox"], "type": "table"},
                          " ".join(str(c) for c in row)))
        pools.append(({"id": tid, "obj": t["id"], "page": t["page"], "bbox": t["bbox"], "type": "table"},
                      t["caption"]["text"]))
    for f in d["figures"]:
        if f.get("text_inside"):
            pools.append(({"id": f.get("block") or f["id"], "obj": f["id"], "page": f["page"], "bbox": f["bbox"],
                           "type": "figure"}, " ".join(f["text_inside"])))
    for e in d["equations"]:
        if e.get("text_rich"):
            pools.append(({"id": e["block"], "obj": e["id"], "page": e["page"], "bbox": e["bbox"], "type": "equation"},
                          e["text_rich"]))
    for r in d["references"]:
        pools.append(({"id": r["block"], "obj": r["id"], "page": r["page"], "bbox": r["bbox"], "type": "reference"},
                      r["text"]))
    for k, f in enumerate(d.get("furniture", [])):
        pools.append(({"id": "furniture-p%d-%d" % (f["page"], k), "page": f["page"], "bbox": f["bbox"],
                       "type": "furniture"}, f["text"]))
    return pools


_POOL_CACHE = {}


def _pools_normalised(d):
    key = id(d)
    hit = _POOL_CACHE.get(key)
    if hit is None or hit[0] is not d:
        pools = [(m, _norm(t), _loose(t)) for m, t in _quote_pools(d)]
        hit = (d, pools)
        _POOL_CACHE[key] = hit
    return hit[1]


def docs_for(docs, file_hint):
    """Documents matching a file hint (name, stem or path); all documents when there is no hint."""
    if not file_hint:
        return docs
    fh = os.path.basename(str(file_hint))
    stem = os.path.splitext(fh)[0]
    sel = [x for x in docs if os.path.basename(x["doc"]["source"]["file"]) == fh
           or os.path.splitext(os.path.basename(x["doc"]["source"]["file"]))[0] == stem]
    return sel or docs


def verify_quote(docs, quote, block_id=None, page=None, file_hint=None):
    """Check that a quoted passage really occurs in the parsed document(s).

    Match levels: exact (after Unicode/whitespace normalisation), loose (ignoring
    spacing and punctuation), and ellipsis (every part of a quote with "..." / "…"
    found in order inside one block). With several documents, a hit in the cited
    block (of the hinted file) wins over hits elsewhere."""
    quote = quote if isinstance(quote, str) else json.dumps(quote, ensure_ascii=False)
    q = _norm(quote)
    if not q:
        return {"verified": False, "how": "empty quote"}
    docs = docs_for(docs, file_hint)
    parts = [p for p in re.split(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*", q) if p.strip()]
    ql = _loose(quote)
    for level in ("exact", "loose", "ellipsis"):
        hits = []
        for x in docs:
            d = x["doc"]
            resolved = None
            if block_id:
                _dd, bb = block_lookup([x], block_id)
                resolved = bb["id"] if bb else None
            for meta, ntext, ltext in _pools_normalised(d):
                if level == "exact":
                    ok = q in ntext
                elif level == "loose":
                    ok = len(ql) >= 4 and ql in ltext
                else:
                    if len(parts) < 2:
                        continue
                    pos, ok = 0, True
                    for p in parts:
                        kk = ntext.find(p, pos)
                        if kk < 0:
                            ok = False
                            break
                        pos = kk + len(p)
                if ok:
                    cited = bool(block_id) and (meta["id"] in (block_id, resolved) or meta.get("obj") == block_id
                                                or block_id in (meta.get("chain") or []))
                    hits.append((d, meta, cited))
        if not hits:
            continue
        d, b, cited = (next((h for h in hits if h[2]), None) or
                       next((h for h in hits if page and h[1]["page"] == page), None) or hits[0])
        if cited:
            how = "found in the cited block"
        elif block_id:
            how = "found in %s (cited %s)" % (b.get("obj") or b["id"], block_id)
        else:
            how = "found in %s" % (b.get("obj") or b["id"])
        if len(docs) > 1:
            how += " of %s" % os.path.basename(d["source"]["file"])
        if b.get("chain"):
            how += " (paragraph continues across %s)" % " -> ".join(b["chain"])
        if level != "exact":
            how += {"loose": "; matched ignoring spacing/punctuation",
                    "ellipsis": "; matched part by part around the ellipsis"}[level]
        out = {"verified": True, "match": level, "how": how, "file": os.path.basename(d["source"]["file"]),
               "page": b["page"], "block": b["id"], "bbox": b["bbox"]}
        if b.get("obj") and b["obj"] != b["id"]:
            out["object"] = b["obj"]
        return out
    return {"verified": False, "how": "quote not found in the parsed text (paraphrase, OCR error or not in source)"}


def block_lookup(docs, bid):
    """Resolve a block id or an object id (table-2, eq-3, figure-1, ref-12, sec-4) to a locator."""
    if not bid:
        return None, None
    for x in docs:
        for b in x["doc"]["blocks"]:
            if b["id"] == bid:
                return x["doc"], b
        for coll in ("tables", "figures", "equations", "references", "footnotes", "sections"):
            for o in x["doc"].get(coll, []):
                if o.get("id") == bid:
                    text = o.get("text") or (o.get("caption") or {}).get("text", "") or o.get("title", "")
                    return x["doc"], {"id": o.get("block") or o["id"], "object": o["id"], "page": o["page"],
                                      "bbox": o["bbox"], "type": coll[:-1], "text": text}
    return None, None


# --------------------------------------------------------------------------
# result assembly
# --------------------------------------------------------------------------

def strict_schema(contract):
    val = list(contract.values())[0] if isinstance(contract, dict) and contract else None
    return isinstance(val, dict) and val.get("additionalProperties") is False


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def build_result(case, case_path, cid, docs, parse_failures, answers=None, task_txt="", contract=None,
                 fields=None, missing_materials=None, urls=None, draft=False, meta=None, soft_fields=None,
                 extra_uncertainties=None):
    """Assemble result.json.

    Precedence for every output-contract field: the agent's value in
    answers.json "fields" > a built-in section of the same name > an automatic
    value from the parse.  Built-in sections (answers, sources, uncertainties,
    ...) never overwrite an agent-provided contract value: when both exist the
    built-in copy goes to "skill_details".  `meta` (a dict) receives the checked
    answers and the per-field fill report for finalize.py."""
    answers = answers or {}
    meta = {} if meta is None else meta
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
            if t.get("image_only") and not t.get("grid"):
                not_rep.append({"file": fn, "item": t["label"], "page": t["page"],
                                "reason": "table body has no text layer near the caption (image table?)",
                                "takeover": "inspect %s / page preview" % (t.get("crop") or "crop")})
            elif t.get("body_source", "").startswith("ocr"):
                unc.append({"file": fn, "issue": "%s (page %d) was read by OCR from an embedded image; verify the "
                                                 "cell values against %s" % (t["label"], t["page"], t.get("crop") or "the crop")})
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
                        "symbol_font_glyphs_remapped": {"count": ch.get("pi_font_glyphs_remapped", 0), "fonts": ch.get("pi_fonts", []),
                                                        "map": "þ→+ ¼→= ð→( Þ→) ½→[ Š→] (Advent pi fonts without ToUnicode)"}
                        if ch.get("pi_font_glyphs_remapped") else None,
                        "ocr_numeric_corrections": ch.get("ocr_numeric_corrections", [])[:20],
                        "table_cell_repairs": ch.get("table_repairs", [])[:30],
                        "note": "original line text and coordinates are kept in document.json blocks[].lines"})
        if d.get("ocr") and d["ocr"].get("pages"):
            for p in d["ocr"]["pages"]:
                if p["low_conf_words"]:
                    unc.append({"file": fn, "issue": "page %d OCR (%s): %d low-confidence words (mean conf %.0f)" % (
                        p["page"], p.get("lang"), len(p["low_conf_words"]), p["mean_conf"]),
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
    raw_answers = answers.get("answers", [])
    if isinstance(raw_answers, dict):
        raw_answers = [dict(v, id=k) if isinstance(v, dict) else {"id": k, "answer": v} for k, v in raw_answers.items()]
    for a in _as_list(raw_answers):
        a = dict(a) if isinstance(a, dict) else {"answer": a}
        ev_out = []
        evs = a.get("evidence") or []
        if isinstance(evs, (str, dict)):
            evs = [evs]
        for ev in evs:
            if isinstance(ev, str):
                ev = {"block": ev}
            if not isinstance(ev, dict):
                continue
            bid = ev.get("block") or ev.get("block_id") or ev.get("id") or ev.get("object")
            fh = ev.get("file") or ev.get("document") or ev.get("doc")
            cands = docs_for(docs, fh)
            found = [r for r in (block_lookup([x], bid) for x in cands) if r[1]] if bid else []
            dd, b = found[0] if found else (None, None)
            item = dict(ev)
            v = verify_quote(cands, item["quote"], bid, ev.get("page")) if item.get("quote") else None
            if len(found) > 1:
                same = [r for r in found if v and v["verified"] and
                        os.path.basename(r[0]["source"]["file"]) == v["file"]]
                if same:
                    dd, b = same[0]
                else:
                    item["ambiguous_files"] = [os.path.basename(r[0]["source"]["file"]) for r in found]
                    unc.append({"issue": "evidence %s exists in several documents (%s); add \"file\" to the evidence" % (
                        bid, ", ".join(item["ambiguous_files"]))})
            if b:
                item.update({"file": os.path.basename(dd["source"]["file"]), "page": b["page"], "bbox": b["bbox"],
                             "block_type": b.get("type")})
                if b.get("object"):
                    item["object"], item["block"] = b["object"], b["id"]
                if not item.get("quote"):
                    item["source_text"] = (b.get("text") or "")[:300]
            elif bid:
                item["resolved"] = False
            if item.get("quote"):
                item["quote_check"] = v
                if v["verified"] and not b:
                    item.update({"file": v["file"], "page": v["page"], "bbox": v["bbox"], "block": v["block"]})
                elif v["verified"] and b and "(cited " in (v.get("how") or ""):
                    # the quote is real but sits elsewhere: point the locator at where it was found
                    item["cited_block"] = bid
                    item.update({"file": v["file"], "page": v["page"], "bbox": v["bbox"], "block": v["block"],
                                 "locator_corrected": True})
                    item.pop("object", None)
                if not v["verified"]:
                    unc.append({"issue": "unverified quote for answer '%s'" % str(a.get("question", a.get("id", "")))[:80],
                                "quote": item["quote"][:200]})
            ev_out.append(item)
        a["evidence"] = ev_out
        ans_out.append(a)
    unc += [u if isinstance(u, dict) else {"issue": u}
            for u in _as_list(answers.get("uncertainties", answers.get("uncertainty", [])))]
    not_rep += [n if isinstance(n, dict) else {"item": n} for n in _as_list(answers.get("not_reported", []))]
    unc += [{"issue": p} for p in (extra_uncertainties or [])]
    if answers.get("status"):
        status = answers["status"]
        reason = answers.get("status_reason", reason)
    elif answers and not draft:
        reason = "agent reviewed the parse. " + reason
    # built-in sections
    evidence_flat = []
    for a in ans_out:
        for e in a.get("evidence", []):
            item = {k: e.get(k) for k in ("file", "page", "block", "object", "bbox", "quote") if e.get(k) is not None}
            if e.get("quote_check"):
                item["verified"] = e["quote_check"]["verified"]
            item["for"] = str(a.get("question", a.get("id", "")))[:120]
            evidence_flat.append(item)
    std = {
        "answers": ans_out,
        "sources": {"materials": [{"file": os.path.basename(x["doc"]["source"]["file"]),
                                   "path": x["doc"]["source"]["file"], "sha256": x["doc"]["source"]["sha256"],
                                   "pages_total": x["doc"]["source"].get("pages_total"),
                                   "pages_parsed": x["doc"]["source"].get("pages_parsed")} for x in docs],
                    "evidence": evidence_flat,
                    "locator_convention": "page = 1-based PDF page; bbox = [x0, y0, x1, y1] in PDF points, top-left "
                                          "origin; block ids index document.json blocks"},
        "uncertainties": unc,
        "not_reported": not_rep,
        "change_records": changes + _as_list(answers.get("change_records", [])),
        "manual_takeover": manual + _as_list(answers.get("manual_takeover", [])),
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
    }
    # contract fields
    given = answers.get("fields", {}) if isinstance(answers.get("fields"), dict) else {}
    specs = contract_specs(contract)
    cvals, report = {}, {}
    unfilled, type_problems = [], []
    req = list(fields or [])
    soft = [f for f in (soft_fields or []) if f not in req]
    for f in req + soft:
        exp = (specs.get(f) or {}).get("type")
        item_t = (specs.get(f) or {}).get("items")
        desc = (specs.get(f) or {}).get("description") or ""
        rep = {"type_expected": exp}
        if f in given:
            v, rep["source"] = given[f], "agent (answers.json fields)"
        elif f in ("case_id",):
            v, rep["source"] = cid, "case id"
        elif f in ("status",):
            v, rep["source"] = status, "run status"
        elif f in ("status_reason",):
            v, rep["source"] = reason, "status reason"
        elif f in std:
            v, rep["source"] = std[f], "built-in section"
            if f == "sources" and exp == "array":
                v = evidence_flat or std["sources"]["materials"]
            if exp and not type_ok(v, exp):
                rep["coerced_from"] = json_type(v)
                v = coerce(v, exp)
                if v is None:
                    rep["source"] = None
                    rep["note"] = "built-in section has another shape than the contract asks for; fill it in answers.json"
        else:
            v, note, locs = auto_value(f, docs, cid, status, reason)
            toks = set(_sing(t) for t in _norm_tokens(f))
            named = [m for m in re.findall(r"`([^`\s]+)`", desc) if FILE_RE.match(m)]
            if docs and (named or re.search(r"\b(file|path|filename)\b|文件|路径", desc, re.I)) and \
                    toks & {"markdown", "md", "text", "content", "body", "fulltext", "json", "csv", "table", "html", "file", "path"}:
                v = named[0] if named else (docs[0]["doc"].get("_json_path") if "json" in toks else docs[0]["doc"].get("_md_path"))
                note = "file the contract describes (%s); make sure it exists in the output directory" % v
                locs = None
            if v is None and toks & {"uncertain", "uncertainty", "limitation", "warning", "issue", "unknown", "risk"}:
                v, note = unc, "uncertainty list"
            if v is None and ("not_reported" in f.lower() or toks >= {"not", "reported"} or "missing" in toks):
                v, note = not_rep, "not-reported list"
            if v is None and toks & {"evidence", "source", "location", "locator", "provenance", "citation", "support"}:
                v = [e for a in ans_out for e in a.get("evidence", [])] or \
                    [{"file": os.path.basename(x["doc"]["source"]["file"]), "document_json": x["doc"].get("_json_path"),
                      "note": "every block in document.json has page + bbox"} for x in docs]
                note = "evidence of the answers" if ans_out else "pointer to document.json locators"
            if v is None and toks & {"answer", "finding", "conclusion", "result", "qa"} and ans_out:
                v, note = ans_out, "answers"
            if v is None and toks & {"change", "edit", "correction", "modification", "modif", "log"}:
                v, note = std["change_records"], "change records"
            rep["source"] = ("auto: " + note) if v is not None else None
            if locs:
                rep["locators"] = locs
            if v is not None and exp and not type_ok(v, exp):
                v2 = coerce(v, exp)
                rep["coerced_from"] = json_type(v)
                v = v2
                if v is None:
                    rep["source"] = None
                    rep["note"] = "automatic value had the wrong type for the contract; fill it in answers.json"
        if isinstance(v, list) and item_t and not (rep.get("source") or "").startswith("agent"):
            v2 = fit_items(v, item_t)
            if v2 is not v:
                rep["items_converted_to"] = item_t
            v = v2
        if v is None and f in req:
            unfilled.append(f)
        if v is not None and exp and not type_ok(v, exp):
            type_problems.append({"field": f, "expected": exp, "got": json_type(v)})
        elif isinstance(v, list) and item_t and not all(type_ok(x, item_t) for x in v):
            type_problems.append({"field": f, "expected": "array of %s" % item_t,
                                  "got": "array of %s" % "/".join(sorted(set(json_type(x) for x in v)))})
        rep["type_got"] = json_type(v)
        report[f] = rep
        if v is not None or f in req:
            cvals[f] = v
    for k, v in given.items():
        if k not in cvals:
            cvals[k] = v
            report[k] = {"source": "agent (answers.json fields)", "type_got": json_type(v), "extra": True}
    if unfilled and status == "success":
        status = "partial"
        reason = reason + "; contract fields not filled: " + ", ".join(unfilled)
    for k, rep in report.items():  # automatic status fields follow the final status
        if rep.get("source") in ("run status", "auto: run status"):
            cvals[k] = status
        elif rep.get("source") in ("status reason", "auto: status reason"):
            cvals[k] = reason
    # assemble: identity + contract fields first, built-in sections after
    res = {"case_id": cid, "status": status, "status_reason": reason}
    for k, v in cvals.items():
        res[k] = v
    collisions = {}
    for k, v in std.items():
        if k in res:
            if res[k] != v:
                collisions[k] = v
            continue
        res[k] = v
    ckey = "contract" if "contract" not in cvals else "skill_contract_report"
    rkey = "run" if "run" not in cvals else "skill_run"
    res[ckey] = {"source": contract, "fields": req, "soft_fields": soft,
                       "files": contract_files(contract),
                       "auto_filled": [f for f in req + soft if (report.get(f, {}).get("source") or "").startswith(("auto", "built-in", "case id", "run status", "status reason"))],
                       "agent_filled": [f for f in req + soft if (report.get(f, {}).get("source") or "").startswith("agent")],
                       "unfilled": unfilled, "type_problems": type_problems,
                       "fill_report": report}
    res[rkey] = {"tool": "weekly-skill pdfdoc", "draft": draft, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "case_json": os.path.abspath(case_path)}
    if collisions:
        res["skill_details"] = dict(collisions, note="built-in sections whose names collide with agent-provided "
                                                      "contract fields; the contract values are kept at the top level")
    if strict_schema(contract):
        # the contract forbids extra keys: result.json keeps the contract fields (+ case_id and
        # status, required by the runtime); the built-in sections go to result_details.json
        allowed = set(req) | set(soft) | set(specs) | {"case_id", "status", "status_reason"}
        meta["details"] = {k: res.pop(k) for k in list(res) if k not in allowed}
    meta.update({"answers": ans_out, "unfilled": unfilled, "type_problems": type_problems, "report": report,
                 "specs": specs, "status": status, "contract_key": ckey, "files": contract_files(contract)})
    return res

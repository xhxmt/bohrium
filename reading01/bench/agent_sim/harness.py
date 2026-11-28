#!/usr/bin/env python3
"""Local stand-in for the evaluator's "official DeepSeek Harness".

The real harness is not published. This reproduces what the task contract states:
  * Skill installed read-only at /skills/weekly-skill (condition=skill) or absent (base)
  * input at /input/case.json + /input/task.md, work in /app/work, results in /app/output
  * budget: 64 model requests, 900 s wall clock, 16384 tokens per response (incl. reasoning)
and records a native-style JSONL trace plus a summary of whether result.json was produced.

Usage:
  DEEPSEEK_API_KEY=... python3 harness.py --case cases/sim-a-... --condition skill \
      --skill-dir ../.. --out runs/<name>   (../.. = the Skill root when run from bench/agent_sim)
"""
import argparse, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.request

API_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com") + "/chat/completions"
IMAGE = "reading01-sim:latest"
MAX_TOOL_OUTPUT = 30000

TOOLS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a bash command in the task container (cwd /app/work). Returns stdout+stderr and exit code.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds, default 120"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file in the container. Optional 1-based line offset and line limit.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a text file in the container.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
]

SYSTEM_BASE = """You are an autonomous agent working inside a Linux container. Use the tools to inspect files and run commands; you cannot ask the user questions.
Budget: at most {max_req} model requests and {max_sec} seconds of wall-clock time in total; each response is limited to {max_tok} tokens including reasoning. Save honest partial deliverables early and improve incrementally.
When the task is complete, reply with a short final message and no tool calls."""

SKILL_LISTING = """
## Skills
The following skill is installed. When the task matches its description, read its SKILL.md first and follow it.
- name: {name}
  description: {description}
  location: /skills/weekly-skill/SKILL.md"""

SKILL_INLINE = """
## Skill: {name} (installed at /skills/weekly-skill)
{body}"""

USER_PROMPT = """Your task input is /input/case.json, with the topic description in /input/task.md. Read both, then complete the case.
Work under /app/work. Put the final actual results and editable artifacts under /app/output, including /app/output/result.json with all output_contract fields from case.json and the case_id. Include honest status, sources, uncertainty and change records. A partial result must identify its limits. Do not fabricate data."""


def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta


class Container:
    def __init__(self, case_dir, skill_dir, run_dir, network):
        self.name = f"r01sim-{os.getpid()}-{int(time.time())}"
        os.makedirs(os.path.join(run_dir, "output"), exist_ok=True)
        os.makedirs(os.path.join(run_dir, "work"), exist_ok=True)
        cmd = ["docker", "run", "-d", "--rm", "--name", self.name,
               "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
               "--cpus", os.environ.get("SIM_CPUS", "4"), "--memory", "8g",
               "-v", f"{os.path.abspath(case_dir)}:/input:ro",
               "-v", f"{os.path.abspath(run_dir)}/output:/app/output",
               "-v", f"{os.path.abspath(run_dir)}/work:/app/work"]
        if not network:
            cmd += ["--network", "none"]
        if skill_dir:
            cmd += ["-v", f"{os.path.abspath(skill_dir)}:/skills/weekly-skill:ro"]
        cmd += [IMAGE]
        subprocess.run(cmd, check=True, capture_output=True)

    def exec(self, command, timeout, stdin=None):
        try:
            p = subprocess.run(["docker", "exec", "-i", "-w", "/app/work", self.name, "bash", "-lc", command],
                               input=stdin, capture_output=True, timeout=timeout)
            out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
            return out, p.returncode
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "exec", self.name, "bash", "-c", "pkill -f -- '-lc' || true"],
                           capture_output=True)
            return f"[timeout after {timeout}s]", 124

    def stop(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


def truncate(s):
    if len(s) <= MAX_TOOL_OUTPUT:
        return s
    half = MAX_TOOL_OUTPUT // 2
    return s[:half] + f"\n...[{len(s) - MAX_TOOL_OUTPUT} chars truncated]...\n" + s[-half:]


def run_tool(c, name, args, remaining):
    if name == "bash":
        t = max(1, min(int(args.get("timeout") or 120), 600, int(remaining)))
        out, rc = c.exec(args["command"], t)
        return truncate(out) + f"\n[exit code {rc}]"
    if name == "read_file":
        off = max(1, int(args.get("offset") or 1))
        lim = int(args.get("limit") or 2000)
        q = args["path"].replace("'", "'\\''")
        out, rc = c.exec(f"sed -n '{off},{off + lim - 1}p' '{q}' | cat -n | sed 's/^/ /'", 60)
        return truncate(out) if rc == 0 else f"[error] {out}"
    if name == "write_file":
        q = args["path"].replace("'", "'\\''")
        out, rc = c.exec(f"mkdir -p \"$(dirname '{q}')\" && cat > '{q}'", 60,
                         stdin=args.get("content", "").encode("utf-8"))
        return f"wrote {len(args.get('content', ''))} chars to {args['path']}" if rc == 0 else f"[error] {out}"
    return f"[error] unknown tool {name}"


def call_api(key, body, timeout):
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:500]!r}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:  # network
            last = repr(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(last)


def check_result(run_dir, case):
    p = os.path.join(run_dir, "output", "result.json")
    info = {"exists": os.path.exists(p), "valid_json": False, "missing_fields": None, "status": None}
    if not info["exists"]:
        return info
    try:
        r = json.load(open(p, encoding="utf-8"))
        info["valid_json"] = True
    except Exception as e:
        info["error"] = str(e)
        return info
    contract = case.get("output_contract", {})
    fields = contract.get("fields", []) if isinstance(contract, dict) else contract
    info["missing_fields"] = [f for f in fields if f not in r]
    info["empty_fields"] = [f for f in fields if f in r and r[f] in (None, "", [], {})]
    info["case_id_ok"] = r.get("case_id") == case.get("case_id")
    info["status"] = r.get("status") if not isinstance(r.get("status"), dict) else r["status"].get("state", r["status"])
    files = contract.get("files", []) if isinstance(contract, dict) else []
    info["missing_files"] = [f for f in files if not os.path.exists(os.path.join(run_dir, "output", f))]
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--condition", choices=["skill", "base"], default="skill")
    ap.add_argument("--skill-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap.add_argument("--skill-mode", choices=["listing", "inline"], default="listing")
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-requests", type=int, default=64)
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--network", action="store_true", help="allow container network (default: none)")
    a = ap.parse_args()

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("DEEPSEEK_API_KEY not set")
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    os.makedirs(a.out)
    case = json.load(open(os.path.join(a.case, "case.json"), encoding="utf-8"))

    system = SYSTEM_BASE.format(max_req=a.max_requests, max_sec=a.max_seconds, max_tok=a.max_tokens)
    skill_dir = None
    if a.condition == "skill":
        skill_dir = a.skill_dir
        body = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
        meta = parse_frontmatter(body)
        system += (SKILL_LISTING if a.skill_mode == "listing" else SKILL_INLINE).format(
            name=meta.get("name", "weekly-skill"), description=meta.get("description", ""), body=body)

    messages = [{"role": "system", "content": system}, {"role": "user", "content": USER_PROMPT}]
    trace = open(os.path.join(a.out, "trace.jsonl"), "w", encoding="utf-8")
    c = Container(a.case, skill_dir, a.out, a.network)
    t0 = time.time()
    n_req, end_reason, first_result_at = 0, None, None
    usage_tot = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    tool_counts = {}
    try:
        while True:
            el = time.time() - t0
            if n_req >= a.max_requests:
                end_reason = "request_budget"; break
            if el >= a.max_seconds:
                end_reason = "time_budget"; break
            body = {"model": a.model, "messages": messages, "tools": TOOLS, "max_tokens": a.max_tokens}
            try:
                resp = call_api(key, body, timeout=max(30, a.max_seconds - el))
            except Exception as e:
                end_reason = f"api_error: {e}"; break
            n_req += 1
            msg = resp["choices"][0]["message"]
            fin = resp["choices"][0].get("finish_reason")
            u = resp.get("usage", {})
            usage_tot["prompt_tokens"] += u.get("prompt_tokens", 0)
            usage_tot["completion_tokens"] += u.get("completion_tokens", 0)
            usage_tot["reasoning_tokens"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            am = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("reasoning_content"):
                am["reasoning_content"] = msg["reasoning_content"]
            if msg.get("tool_calls"):
                am["tool_calls"] = msg["tool_calls"]
            messages.append(am)
            trace.write(json.dumps({"t": round(time.time() - t0, 1), "req": n_req, "type": "assistant",
                                    "finish_reason": fin, "usage": u, "message": am}, ensure_ascii=False) + "\n")
            trace.flush()
            if not msg.get("tool_calls"):
                end_reason = "length_no_tools" if fin == "length" else "agent_done"; break
            for tc in msg["tool_calls"]:
                fn = tc["function"]["name"]
                tool_counts[fn] = tool_counts.get(fn, 0) + 1
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                    out = run_tool(c, fn, args, a.max_seconds - (time.time() - t0))
                except Exception as e:
                    out = f"[error] {e!r}"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
                trace.write(json.dumps({"t": round(time.time() - t0, 1), "type": "tool", "name": fn,
                                        "output": out[:4000]}, ensure_ascii=False) + "\n")
                trace.flush()
            if first_result_at is None and os.path.exists(os.path.join(a.out, "output", "result.json")):
                first_result_at = round(time.time() - t0, 1)
    finally:
        c.stop()
        trace.close()

    summary = {
        "case_id": case.get("case_id"), "condition": a.condition, "skill_mode": a.skill_mode,
        "model": a.model, "end_reason": end_reason, "requests": n_req,
        "wall_seconds": round(time.time() - t0, 1), "first_result_json_at": first_result_at,
        "usage": usage_tot, "tool_calls": tool_counts, "result": check_result(a.out, case),
    }
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

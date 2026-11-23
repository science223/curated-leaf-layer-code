#!/usr/bin/env bash
# E13 (预注册_E13 revision 1): the loop's own requests, game by game, from each game's ckpt/metrics. Every game must have
# requested qwen3.8:27b on Ollama with the body {"reasoning_effort": "none"}, every call must have been answered by
# qwen3.8:27b, and no call may end on the output cap ("length"). Also prints the token sizes (descriptive) and the calls
# whose prompt came within 8,192 tokens of the 32,768-token context. Usage: e13_llm_check.sh <plan> [plan ...]
# (e.g. zs_e13n_life_L9). Prints one summary line starting with OK or BAD; exit 1 when BAD. Read-only.
cd ~/wt_e13/runs_0911 || exit 2
python3 - "$@" <<'PY'
import collections, glob, json, os, sys
bad, games, calls, fr = [], 0, 0, collections.Counter()
pt, ct, near = [], [], 0
for plan in sys.argv[1:]:
    metas = sorted(glob.glob(f"{plan}/work/*/*/ckpt/metrics/run_meta.json"))
    if not metas:
        bad.append(f"{plan}: no run_meta.json")
    for f in metas:
        games += 1
        m = json.load(open(f))
        if m.get("request_extra_body") != {"reasoning_effort": "none"}:
            bad.append(f"{f}: request body {m.get('request_extra_body')}")
        if m.get("model_requested") != "qwen3.8:27b" or ":11434" not in str(m.get("openai_api_base")):
            bad.append(f"{f}: model {m.get('model_requested')} at {m.get('openai_api_base')}")
        lc = f.replace("run_meta.json", "llm_calls.jsonl")
        for line in (open(lc) if os.path.exists(lc) else []):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            calls += 1
            fr[r.get("finish_reason")] += 1
            if r.get("model_returned") not in ("qwen3.8:27b", None):
                bad.append(f"{lc}: answered by {r.get('model_returned')}")
            if r.get("finish_reason") == "length":
                bad.append(f"{lc}: a reply ended on the output cap")
            if isinstance(r.get("prompt_tokens"), int):
                pt.append(r["prompt_tokens"])
                near += r["prompt_tokens"] > 32768 - 8192
            if isinstance(r.get("completion_tokens"), int):
                ct.append(r["completion_tokens"])
pt.sort(); ct.sort()
summary = (f"{games} games, {calls} calls; finish {dict(fr)}; prompt max {pt[-1] if pt else None}, completion max "
           f"{ct[-1] if ct else None}; prompts within 8192 of the context: {near}")
if calls == 0:
    bad.append("no model call recorded")
print(("BAD " if bad else "OK ") + summary)
for b in bad[:20]:
    print("  - " + b)
sys.exit(1 if bad else 0)
PY

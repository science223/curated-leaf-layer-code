#!/usr/bin/env bash
# E13 (预注册_E13 §5, revision 1): one direct call to Ollama with the loop's thinking switch (reasoning_effort "none"),
# before the smoke. Prints "OK ..." when the reply carries no reasoning and no <think> block, else "BAD ...". Read-only.
GW=$(ip route | awk '/default/{print $3; exit}')
r=$(curl -s -m 180 "http://$GW:11434/v1/chat/completions" -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
  -d '{"model":"qwen3.8:27b","messages":[{"role":"user","content":"Write a JavaScript async function craftPickaxe(bot) that crafts a wooden pickaxe with mineflayer. Code only."}],"temperature":0,"reasoning_effort":"none","max_tokens":400}')
python3 - "$r" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
    m = d["choices"][0]["message"]
except Exception as e:
    print(f"BAD no reply ({e})"); raise SystemExit
rs = m.get("reasoning") or m.get("reasoning_content") or ""
c = m.get("content") or ""
if not rs and "<think>" not in c and c.strip():
    print(f"OK no reasoning; model {d.get('model')}, {d.get('usage', {}).get('completion_tokens')} completion tokens")
else:
    print(f"BAD reasoning {len(rs)} chars, think tag {'<think>' in c}, content {len(c)} chars")
PY

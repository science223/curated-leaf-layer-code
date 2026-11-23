#!/usr/bin/env bash
# E13: does Ollama on the Windows side serve qwen3.8:27b to WSL? Prints "OK <base>" or "BAD <reason>". Read-only.
GW=$(ip route | awk '/default/{print $3; exit}')
B=http://$GW:11434/v1
curl -s -m 20 "$B/models" | grep -q '"qwen3.8:27b"' && echo "OK $B" || echo "BAD qwen3.8:27b not listed at $B"

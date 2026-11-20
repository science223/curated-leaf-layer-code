from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from importlib import metadata

from langchain.callbacks.base import BaseCallbackHandler
from langchain.chat_models import ChatOpenAI


VALID_THINKING_MODES = {"disabled", "enabled"}
AUDITED_PACKAGES = ("langchain", "openai", "chromadb")


def _as_dict(value):
    if isinstance(value, dict):
        return value
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                result = method()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
    return {}


def _pip_freeze_versions():
    versions = {package: None for package in AUDITED_PACKAGES}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for line in result.stdout.splitlines():
            if "==" not in line:
                continue
            name, version = line.split("==", 1)
            normalized_name = name.strip().lower().replace("_", "-")
            if normalized_name in versions:
                versions[normalized_name] = version.strip() or None
    except Exception:
        pass

    for package, version in versions.items():
        if version is not None:
            continue
        try:
            versions[package] = metadata.version(package)
        except Exception:
            pass
    return versions


class AuditedChatOpenAI(ChatOpenAI):
    """Preserve the model string from the raw API response for callbacks."""

    def _create_chat_result(self, response):
        response_dict = _as_dict(response)
        result = super()._create_chat_result(response)
        if result.llm_output is None:
            result.llm_output = {}
        result.llm_output["model_returned"] = response_dict.get("model")
        result.llm_output["system_fingerprint"] = response_dict.get(
            "system_fingerprint"
        )
        # 2026-09-11 审计 03 C-05：max_tokens 未设、finish_reason 不落盘，
        # 服务端截断只会表现为 babel 语法错。把 finish_reason 记下来。
        finish_reason = None
        try:
            choices = response_dict.get("choices") or []
            if choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
        except Exception:
            finish_reason = None
        result.llm_output["finish_reason"] = finish_reason
        # ---- F16（2026-09-11，实验线 smoke 发现）：OpenRouter 相关字段 ----
        # 顶层 provider（钉供应商的证据）、生成 id、reasoning_tokens（推理是否真关）、cost。
        usage = _as_dict(response_dict.get("usage"))
        details = _as_dict(usage.get("completion_tokens_details"))
        reasoning_tokens = details.get("reasoning_tokens")
        result.llm_output["provider"] = response_dict.get("provider")
        result.llm_output["gen_id"] = response_dict.get("id")
        result.llm_output["reasoning_tokens"] = reasoning_tokens
        result.llm_output["cost"] = usage.get("cost")
        return result


class LLMCallAuditHandler(BaseCallbackHandler):
    def __init__(self, audit, *, agent, model_requested, thinking):
        self.audit = audit
        self.agent = agent
        self.model_requested = model_requested
        self.thinking = thinking
        self._starts = {}
        self._lock = threading.Lock()

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        try:
            with self._lock:
                self._starts[str(run_id)] = (
                    time.perf_counter(),
                    self.audit.current_iteration(),
                )
        except Exception:
            pass

    def on_llm_end(self, response, *, run_id, **kwargs):
        try:
            started_at, iteration = self._pop_start(run_id)
            llm_output = _as_dict(getattr(response, "llm_output", None))
            usage = _as_dict(llm_output.get("token_usage"))
            model_returned = llm_output.get("model_returned")
            system_fingerprint = llm_output.get("system_fingerprint")
            prompt_details = _as_dict(usage.get("prompt_tokens_details"))
            cached_tokens = usage.get("cached_tokens")
            if cached_tokens is None:
                cached_tokens = usage.get("prompt_cache_hit_tokens")
            if cached_tokens is None:
                cached_tokens = prompt_details.get("cached_tokens")
            self.audit.append_call(
                {
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "iter": iteration,
                    "agent": self.agent,
                    "model_requested": self.model_requested,
                    "model_returned": model_returned,
                    "system_fingerprint": system_fingerprint,
                    "prompt_tokens": usage.get(
                        "prompt_tokens", usage.get("input_tokens")
                    ),
                    "completion_tokens": usage.get(
                        "completion_tokens", usage.get("output_tokens")
                    ),
                    "total_tokens": usage.get("total_tokens"),
                    "cached_tokens": cached_tokens,
                    "finish_reason": llm_output.get("finish_reason"),
                    "thinking": self.thinking,
                    # F16：请求值 vs 实际值。reasoning_tokens>0 = 推理其实开着。
                    "thinking_actual": (
                        "enabled"
                        if isinstance(llm_output.get("reasoning_tokens"), int)
                        and llm_output.get("reasoning_tokens") > 0
                        else ("disabled" if llm_output.get("reasoning_tokens") is not None else None)
                    ),
                    "provider": llm_output.get("provider"),
                    "gen_id": llm_output.get("gen_id"),
                    "reasoning_tokens": llm_output.get("reasoning_tokens"),
                    "cost": llm_output.get("cost"),
                    "latency_ms": self._elapsed_ms(started_at),
                }
            )
        except Exception as error:
            self.audit.warn(f"could not record completed LLM call: {error}")

    def on_llm_error(self, error, *, run_id, **kwargs):
        try:
            started_at, iteration = self._pop_start(run_id)
            self.audit.append_call(
                {
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "iter": iteration,
                    "agent": self.agent,
                    "model_requested": self.model_requested,
                    "model_returned": None,
                    "system_fingerprint": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "cached_tokens": None,
                    "thinking": self.thinking,
                    "latency_ms": self._elapsed_ms(started_at),
                }
            )
        except Exception as audit_error:
            self.audit.warn(f"could not record failed LLM call: {audit_error}")

    def _pop_start(self, run_id):
        with self._lock:
            return self._starts.pop(
                str(run_id),
                (None, self.audit.current_iteration()),
            )

    @staticmethod
    def _elapsed_ms(started_at):
        if started_at is None:
            return None
        return round((time.perf_counter() - started_at) * 1000)


class RunLLMAudit:
    def __init__(
        self,
        ckpt_dir,
        *,
        arm=None,
        trial=None,
        model_requested=None,
        curation_enabled=None,
        thinking="disabled",
        resume=False,
    ):
        self.metrics_dir = os.path.join(ckpt_dir, "metrics")
        self.calls_path = os.path.join(self.metrics_dir, "llm_calls.jsonl")
        self.run_meta_path = os.path.join(self.metrics_dir, "run_meta.json")
        self._iteration_provider = lambda: None
        self._write_lock = threading.Lock()
        # 2026-09-11 审计 01 C-9：--resume 时旧 run_meta 被整体覆盖，前段的
        # started_at / stopped_reason / stopped_at_iteration 丢失，分析脚本靠
        # stopped_reason=iteration_limit 判"跑满"会被误导。现在把前段整份留在
        # previous_segments 里，resume_count = 前段数。
        previous_segments = []
        if resume and os.path.exists(self.run_meta_path):
            try:
                with open(self.run_meta_path, "r", encoding="utf-8") as handle:
                    old = json.load(handle)
                previous_segments = list(old.pop("previous_segments", []) or [])
                previous_segments.append(old)
            except Exception as error:
                self.warn(f"could not read previous run_meta on resume: {error}")
        self.run_meta = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "arm": arm,
            "trial": trial,
            "model_requested": model_requested,
            "first_model_returned": None,
            "curation_enabled": curation_enabled,
            "thinking": thinking,
            "package_versions": _pip_freeze_versions(),
            "resume": bool(resume),
            "resume_count": len(previous_segments),
            "previous_segments": previous_segments,
        }
        self._write_run_meta()

    def set_iteration_provider(self, provider):
        self._iteration_provider = provider

    def current_iteration(self):
        try:
            return self._iteration_provider()
        except Exception:
            return None

    def append_call(self, row):
        try:
            with self._write_lock:
                os.makedirs(self.metrics_dir, exist_ok=True)
                with open(self.calls_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                meta_dirty = False
                if (
                    self.run_meta["first_model_returned"] is None
                    and row.get("model_returned") is not None
                ):
                    self.run_meta["first_model_returned"] = row["model_returned"]
                    meta_dirty = True
                # F16：thinking 实际值。请求关了但 reasoning_tokens>0 → 报警并记数。
                rt = row.get("reasoning_tokens")
                if isinstance(rt, int):
                    if rt > 0:
                        n = int(self.run_meta.get("reasoning_leak_calls") or 0) + 1
                        self.run_meta["reasoning_leak_calls"] = n
                        self.run_meta["thinking_actual"] = "enabled"
                        meta_dirty = True
                        self.warn(
                            f"[thinking-leak] {row.get('agent')} 调用 reasoning_tokens={rt} "
                            f"（请求 thinking={self.run_meta.get('thinking')}），累计 {n} 次。"
                            "OpenRouter 下需 reasoning:{enabled:false}。"
                        )
                    elif self.run_meta.get("thinking_actual") is None:
                        self.run_meta["thinking_actual"] = "disabled"
                        meta_dirty = True
                if row.get("provider") and row["provider"] not in (self.run_meta.get("providers_seen") or []):
                    self.run_meta.setdefault("providers_seen", []).append(row["provider"])
                    meta_dirty = True
                if meta_dirty:
                    self._write_run_meta_unlocked()
        except Exception as error:
            self.warn(f"could not append LLM audit row: {error}")

    def _write_run_meta(self):
        try:
            with self._write_lock:
                self._write_run_meta_unlocked()
        except Exception as error:
            self.warn(f"could not write run metadata: {error}")

    def _write_run_meta_unlocked(self):
        os.makedirs(self.metrics_dir, exist_ok=True)
        temporary_path = self.run_meta_path + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(self.run_meta, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, self.run_meta_path)

    @staticmethod
    def warn(message):
        try:
            print(f"\033[33m[llm-audit] {message}\033[0m", flush=True)
        except Exception:
            pass


def create_chat_openai(
    *,
    model_name,
    temperature,
    request_timeout,
    thinking="disabled",
    audit=None,
    agent,
):
    if thinking not in VALID_THINKING_MODES:
        raise ValueError(
            f"thinking must be one of {sorted(VALID_THINKING_MODES)}, got {thinking!r}"
        )
    callbacks = []
    if audit is not None:
        callbacks.append(
            LLMCallAuditHandler(
                audit,
                agent=agent,
                model_requested=model_name,
                thinking=thinking,
            )
        )
    return AuditedChatOpenAI(
        model_name=model_name,
        temperature=temperature,
        request_timeout=request_timeout,
        # E13 (2026-10-09): Ollama gets the same output cap as OpenRouter (8192); E12's longest reply was 2,422 tokens
        max_tokens=OPENROUTER_MAX_TOKENS if (is_openrouter_base() or is_ollama_base()) else None,
        model_kwargs={"extra_body": build_extra_body(thinking)},
        callbacks=callbacks,
    )


# 2026-09-13：DeepSeek 官方接口默认输出上限 8192；OpenRouter 默认 131072，V4 偶发退化重复会一口气写满
# 131072（单次 30+ 分钟，结果同样是截断→该轮 fallback）。OpenRouter 显式设 8192，两家上限一致；官方路径不传（逐字节不变）。
OPENROUTER_MAX_TOKENS = 8192


def is_openrouter_base(api_base=None):
    base = api_base if api_base is not None else os.environ.get("OPENAI_API_BASE", "")
    return "openrouter.ai" in (base or "").lower()


def is_ollama_base(api_base=None):
    """E13 (2026-10-09): a local Ollama server (port 11434)."""
    base = api_base if api_base is not None else os.environ.get("OPENAI_API_BASE", "")
    return ":11434" in (base or "")


def provider_order_from_env():
    raw = os.environ.get("OPENROUTER_PROVIDER_ORDER", "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()]


def build_extra_body(thinking, api_base=None, provider_order=None):
    """F16（2026-09-11）：请求体按后端分两套。

    - DeepSeek 官方：原生 `thinking:{type: disabled|enabled}`。
    - OpenRouter：**不认** 上面那个字段，推理会全开（实验线 smoke：Flash 29 次调用 3h16m，
      p90 单次 28 min，action completion 中位 12.8k token）。要发
      `reasoning:{enabled:false}`；并用 `provider:{order:[…], allow_fallbacks:false}` 钉死
      供应商（V4 Flash 0731 在 OpenRouter 有 28 个托管端点，精度混杂）。
    供应商列表来自 OPENROUTER_PROVIDER_ORDER（逗号分隔，由 run_voyager --provider-order 设）。
    """
    if is_openrouter_base(api_base):
        body = {"reasoning": {"enabled": thinking != "disabled"}}
        order = provider_order if provider_order is not None else provider_order_from_env()
        if order:
            body["provider"] = {"order": list(order), "allow_fallbacks": False}
        # 让 OpenRouter 在 usage 里带 cost（可选字段，不带也不影响）
        body["usage"] = {"include": True}
        return body
    if is_ollama_base(api_base):
        # E13 (2026-10-09): Ollama's OpenAI endpoint turns Qwen's thinking off with reasoning_effort "none"
        return {"reasoning_effort": "none"} if thinking == "disabled" else {}
    return {"thinking": {"type": thinking}}

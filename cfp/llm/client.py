"""Single-host Ollama client wrapper.

There is exactly one Ollama daemon per machine (OLLAMA_HOST). Per-model host
routing was removed (context.md §12, arch.md §1 ADR-8): the local daemon
either has the model or the job is escalated.

Used by:
    - cfp/llm/__init__.py (re-exports)
    - cfp/embed.py (embedding generation)
    - tier handlers under cfp/pipeline/ (chat with tool calling)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Iterable, Optional, Union

from ollama import Client as OllamaSDKClient

from config import CFP_MACHINE, OLLAMA_HOST, PROFILE_MODELS

log = logging.getLogger(__name__)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


# Tier shortcuts. The pipeline references tiers by alias; this map turns the
# alias into the bare model id (still without the quant suffix). The actual
# quant-tagged name comes from PROFILE_MODELS via _quant_for_machine().
_SHORTCUTS: dict[str, str] = {
    "tier1":       "qwen3:4b",
    "tier2":       "qwen3:14b",
    "tier3":       "qwen3:32b",
    "tier4-batch": "deepseek-r1:70b",
    "dedup":       "deepseek-r1:32b",
    "embed":       "nomic-embed-text",
    "long-ctx":    "mistral-nemo:12b",
    # legacy shortcuts from conf-scr-org-syn
    "fast":        "qwen3:4b",
    "small":       "qwen3:14b",
    "smart":       "qwen3:32b",
}


def _strip_thinking(text: str) -> str:
    """Strip <think>…</think> blocks emitted by Qwen3/DeepSeek thinking mode."""
    return _THINK_RE.sub("", text).strip()


def _parse_json_response(
    text: str,
) -> Union[dict, list, None]:
    """Extract JSON from LLM output even when wrapped in prose or fences.

    3-level fallback:
        1. direct json.loads on the (think-stripped) text
        2. ```json … ``` (or bare ```) code block contents
        3. first '{'/'[' to last '}'/']' slice
    Returns None if all three levels fail.
    """
    cleaned = _strip_thinking(text)

    # Level 1: direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Level 2: fenced code block
    m = _FENCE_RE.search(cleaned)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Level 3: best-effort bracket slice
    for start, end in (("{", "}"), ("[", "]")):
        si = cleaned.find(start)
        ei = cleaned.rfind(end)
        if si != -1 and ei != -1 and ei > si:
            try:
                return json.loads(cleaned[si : ei + 1])
            except json.JSONDecodeError:
                continue

    return None


def get_available_models(host: str = OLLAMA_HOST) -> list[str]:
    """Return model names actually pulled on the local Ollama daemon.

    Uses the SDK; on any error returns []. Names come back with their full
    tag (e.g. "qwen3:4b-q4_K_M"), matching what PROFILE_MODELS stores.
    """
    try:
        resp = OllamaSDKClient(host=host, timeout=5).list()
    except Exception as e:
        log.warning("get_available_models: Ollama unreachable at %s: %s", host, e)
        return []

    names: list[str] = []
    # SDK >= 0.4 returns ListResponse(models=[Model(model="...", ...)])
    for m in getattr(resp, "models", []) or []:
        # Each Model has .model (preferred) or legacy .name attribute
        name = getattr(m, "model", None) or getattr(m, "name", None)
        if name:
            names.append(name)
    return names


def profile_intersection(host: str = OLLAMA_HOST) -> set[str]:
    """needed ∩ available — models on this profile that are actually pulled.

    Treats "model" as matching "model:latest" (Ollama's default tag) so
    PROFILE_MODELS entries without quant suffixes still match. Tier handlers
    use this to decide local-vs-escalate routing.
    """
    needed = set(PROFILE_MODELS.get(CFP_MACHINE, []))
    have = set(get_available_models(host=host))
    matched: set[str] = set()
    for n in needed:
        if n in have or f"{n}:latest" in have or any(h == n for h in have):
            matched.add(n)
    return matched


def resolve_model(name: str) -> str:
    """Resolve a tier alias / bare model id to the quant-tagged variant
    listed in PROFILE_MODELS[CFP_MACHINE].

    Resolution order:
        1. If `name` is a known shortcut, swap to its bare model id.
        2. If `name` is already in PROFILE_MODELS for this machine, return it.
        3. If a profile entry starts with `name + ":"` or `name + "-"`,
           return that entry (matches "qwen3:4b" → "qwen3:4b-q4_K_M").
        4. Otherwise return `name` unchanged.
    """
    candidate = _SHORTCUTS.get(name, name)
    profile = PROFILE_MODELS.get(CFP_MACHINE, [])

    if candidate in profile:
        return candidate

    # Prefix match: "qwen3:4b" should match "qwen3:4b-q4_K_M" but NOT
    # "qwen3:4b-q8_0" if both are present (first wins, profile order is
    # the tiebreaker).
    for entry in profile:
        if entry == candidate or entry.startswith(candidate + "-"):
            return entry

    return candidate


class OllamaClient:
    """Sync wrapper around the Ollama SDK with JSON-mode chat + embeddings.

    The class is intentionally thin: tier-specific retry/escalation logic
    lives in the pipeline (cfp/pipeline/*), not here.
    """

    def __init__(
        self,
        model: str,
        *,
        host: str = OLLAMA_HOST,
        timeout: float = 300.0,
    ) -> None:
        self.model = resolve_model(model)
        self._sdk = OllamaSDKClient(host=host, timeout=timeout)

    # --- chat -----------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        format: Optional[str] = "json",
        options: Optional[dict] = None,
    ) -> str:
        """Send a chat request; return the raw assistant message content
        (with <think> blocks stripped).

        format defaults to "json" because every tier-1/2 prompt expects JSON
        back. Pass format=None for free-form text.
        """
        kw: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            kw["tools"] = tools
        if format:
            kw["format"] = format
        if options:
            kw["options"] = options

        resp = self._sdk.chat(**kw)
        # SDK returns a Pydantic ChatResponse; .message is a Message with .content
        msg = resp["message"] if isinstance(resp, dict) else resp.message
        content = msg["content"] if isinstance(msg, dict) else msg.content
        return _strip_thinking(content or "")

    def chat_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict],
        tool_impls: dict[str, Callable[..., Any]],
        *,
        max_iters: int = 6,
    ) -> tuple[str, list[dict]]:
        """Agentic tool-calling loop. Qwen3 only — DeepSeek-R1 has no
        tool-calling support.

        Returns (final_text, trace) where trace is a list of
        {name, args, result_summary} dicts for observability.
        """
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ]
        trace: list[dict] = []

        for _ in range(max_iters):
            resp = self._sdk.chat(model=self.model, messages=messages, tools=tools)
            msg = resp["message"] if isinstance(resp, dict) else resp.message

            # Persist the assistant turn unchanged — SDK accepts both Pydantic
            # Message instances and Mapping[str, Any] in subsequent calls.
            messages.append(msg)

            content = msg["content"] if isinstance(msg, dict) else msg.content
            tool_calls = (
                msg["tool_calls"] if isinstance(msg, dict) else msg.tool_calls
            ) or []

            if not tool_calls:
                return _strip_thinking(content or ""), trace

            for call in tool_calls:
                fn = call["function"] if isinstance(call, dict) else call.function
                name = fn["name"] if isinstance(fn, dict) else fn.name
                args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                impl = tool_impls.get(name)
                if impl is None:
                    result: Any = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = impl(**(args or {}))
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}

                trace.append(
                    {"name": name, "args": args, "result_summary": str(result)[:200]}
                )
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": json.dumps(result, default=str),
                    }
                )

        raise RuntimeError(f"tool loop exceeded {max_iters} iterations")

    # --- embed ----------------------------------------------------------

    def embed(
        self,
        text: Union[str, Iterable[str]],
    ) -> Union[list[float], list[list[float]]]:
        """Generate embeddings. Returns a single vector for a single string
        input, or a list of vectors for a list input — matching the shape
        callers expect from cfp/embed.py.
        """
        is_single = isinstance(text, str)
        payload = text if is_single else list(text)
        resp = self._sdk.embed(model=self.model, input=payload)
        # SDK returns EmbedResponse(embeddings=[[...]]) — always a 2-D list
        embeddings = (
            resp["embeddings"] if isinstance(resp, dict) else resp.embeddings
        )
        vectors = [list(v) for v in embeddings]
        if is_single:
            return vectors[0] if vectors else []
        return vectors

# Codegen 09 — wcfp/llm/client.py + tools.py

## Files to Create
- `wcfp/llm/__init__.py` (empty)
- `wcfp/llm/client.py`
- `wcfp/llm/tools.py`

## Imports
```python
from config import OLLAMA_HOSTS, MODEL_HOST
```

---

## COPY VERBATIM: _strip_thinking()

```python
import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

def _strip_thinking(text: str) -> str:
    """Strip <think>…</think> blocks emitted by Qwen3/DeepSeek thinking mode."""
    return _THINK_RE.sub("", text).strip()
```

---

## COPY VERBATIM: _parse_json_response() — 3-level fallback

```python
import json

def _parse_json_response(text: str) -> dict | list:
    """Extract JSON from LLM output even if it has surrounding prose."""
    # Level 1: direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Level 2: ```json ... ``` code block
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Level 3: first { to last }  OR  first [ to last ]
    for start, end in [('{', '}'), ('[', ']')]:
        si = text.find(start)
        ei = text.rfind(end)
        if si != -1 and ei != -1 and ei > si:
            try:
                return json.loads(text[si:ei+1])
            except json.JSONDecodeError:
                pass
    return {}
```

---

## Model shortcuts mapping

Map these aliases to full Ollama model names. Used by `resolve_model()`.

```python
SHORTCUTS: dict[str, str] = {
    "tier1":      "qwen3:4b",
    "tier2":      "qwen3:14b",
    "tier3":      "qwen3:32b",
    "tier4-batch":"deepseek-r1:70b",
    "dedup":      "deepseek-r1:32b",
    "embed":      "nomic-embed-text",
    "long-ctx":   "mistral-nemo:12b",
    # legacy shortcuts from conf-scr-org-syn
    "fast":       "qwen3:4b",
    "small":      "qwen3:14b",
    "smart":      "qwen3:32b",
}

def resolve_model(model: str | None) -> str:
    """Resolve shortcut to full model name."""
    if model is None:
        return SHORTCUTS["tier2"]
    return SHORTCUTS.get(model, model)
```

---

## Auto-detect LLM backend (adapted from conf-scr-org-syn)

```python
import os, requests as _req

def _detect_default_host() -> str:
    """Return name key from OLLAMA_HOSTS for the best available host."""
    for host_name, url in OLLAMA_HOSTS.items():
        try:
            resp = _req.get(f"{url}/api/tags", timeout=2)
            if resp.ok and resp.json().get("models"):
                return host_name
        except Exception:
            continue
    return "local"   # fallback to localhost
```

---

## OllamaClient

```python
from ollama import Client as OllamaSDKClient

class OllamaClient:
    """Multi-host Ollama client with tool-calling support."""

    def __init__(self) -> None:
        self._clients: dict[str, OllamaSDKClient] = {
            name: OllamaSDKClient(host=url, timeout=300)
            for name, url in OLLAMA_HOSTS.items()
        }

    def _client_for(self, model: str) -> OllamaSDKClient:
        host_name = MODEL_HOST.get(model, "local")
        return self._clients[host_name]

    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        format: str | None = None,      # "json" forces JSON output
        options: dict | None = None,
    ) -> str:
        """Send a chat request; return stripped text response."""
        kw: dict = {"model": model, "messages": messages}
        if tools:   kw["tools"]   = tools
        if format:  kw["format"]  = format
        if options: kw["options"] = options
        resp = self._client_for(model).chat(**kw)
        raw = resp["message"]["content"]
        return _strip_thinking(raw)

    def chat_with_tools(
        self,
        model: str,
        system: str,
        user: str,
        tools: list[dict],
        tool_impls: dict[str, callable],
        max_iters: int = 6,
    ) -> tuple[str, list[dict]]:
        """
        Agentic tool-calling loop.
        Returns (final_text, tool_call_trace).
        Used ONLY with Qwen3 models (tool calling not supported on DeepSeek-R1).
        """
        import json as _json
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ]
        trace = []
        for _ in range(max_iters):
            resp = self._client_for(model).chat(
                model=model, messages=messages, tools=tools
            )
            msg   = resp["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                return _strip_thinking(msg.get("content", "")), trace
            for call in calls:
                name = call["function"]["name"]
                args = call["function"]["arguments"]
                if isinstance(args, str):
                    args = _json.loads(args)
                try:
                    result = tool_impls[name](**args)
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
                trace.append({"name": name, "args": args,
                               "result_summary": str(result)[:200]})
                messages.append({
                    "role": "tool", "name": name,
                    "content": _json.dumps(result, default=str),
                })
        raise RuntimeError(f"tool loop exceeded {max_iters} iterations")
```

---

## tools.py — TOOLS list (6 tool definitions)

```python
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "extract_text",
            "description": "Return visible text of all elements matching a CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_links",
            "description": "Return list of hrefs whose text or href matches the regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_field",
            "description": "Return the value found next to a labelled field (e.g. 'Submission Deadline').",
            "parameters": {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "is_conference_page",
            "description": "Return True if this page is a real conference/workshop CFP (not journal/spam).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "classify_category",
            "description": (
                "Return list of Category enum values that match the text. "
                "Valid values: AI, ML, DevOps, Linux, ChipDesign, Math, Legal, "
                "ComputerScience, Security, Data, Networking, Robotics, Bioinformatics"
            ),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_virtual",
            "description": "Return True if the conference is online/virtual-only.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
]
```

### Tool implementations (closure-bound in pipeline.py)

Each tool receives a `soup: BeautifulSoup` via closure when pipeline.py calls
`chat_with_tools`. pipeline.py creates the `tool_impls` dict like:

```python
import re
from bs4 import BeautifulSoup
from wcfp.llm.tools import TOOLS

def make_tool_impls(soup: BeautifulSoup, current_url: str) -> dict[str, callable]:
    def extract_text(selector: str) -> str:
        els = soup.select(selector)
        return " ".join(el.get_text(" ", strip=True) for el in els)[:2000]

    def find_links(pattern: str) -> list[str]:
        rx = re.compile(pattern, re.IGNORECASE)
        return [a["href"] for a in soup.find_all("a", href=True)
                if rx.search(a["href"]) or rx.search(a.get_text())][:50]

    def get_field(label: str) -> str:
        for td in soup.find_all("td"):
            if label.lower() in td.get_text(strip=True).lower():
                nxt = td.find_next_sibling("td")
                if nxt:
                    return nxt.get_text(strip=True)
        return ""

    def is_conference_page() -> bool:
        text = soup.get_text(" ", strip=True).lower()
        signals = ["call for papers", "cfp", "submission deadline",
                   "paper deadline", "workshop", "symposium", "conference"]
        return sum(1 for s in signals if s in text) >= 2

    def classify_category(text: str) -> list[str]:
        # Lightweight keyword check; LLM will confirm
        from wcfp.parsers.wikicfp import _guess_category
        return [_guess_category(text)]

    def detect_virtual(text: str) -> bool:
        virtual_kw = ["online", "virtual", "remote", "zoom", "webinar"]
        return any(kw in text.lower() for kw in virtual_kw)

    return {
        "extract_text":      extract_text,
        "find_links":        find_links,
        "get_field":         get_field,
        "is_conference_page": is_conference_page,
        "classify_category": classify_category,
        "detect_virtual":    detect_virtual,
    }
```

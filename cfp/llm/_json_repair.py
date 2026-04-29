"""JSON repair for LLM outputs (codegen/10 §JSON repair helper).

Two-stage strategy:
    1. Lean on cfp.llm.client._parse_json_response (handles fenced blocks,
       leading prose, <think>...</think> noise).
    2. Apply cheap regex repairs and reparse: single-quoted JSON keys,
       trailing commas before ``}`` / ``]``.
    3. Fall back to ``json5`` if the package is installed.

Returns ``None`` when every avenue fails so the caller can decide whether to
retry the prompt or escalate.
"""
from __future__ import annotations

import re
from typing import Optional, Union

from cfp.llm.client import _parse_json_response, _strip_thinking
from config import JSON_REPAIR_ENABLED


# Single-quoted *keys* only — values that legitimately contain apostrophes
# (e.g. ``"O'Brien"``) must not be touched. The lookbehind requires the
# character before the key to be ``{`` or ``,`` so we only rewrite at
# object-key positions.
_SINGLE_QUOTE_KEY_RE = re.compile(r"(?<=[\{,])\s*'([A-Za-z0-9_]+)'\s*:")
_TRAILING_COMMA_RE   = re.compile(r",(\s*[\}\]])")


def repair_json(raw: str) -> Optional[Union[dict, list]]:
    """Best-effort JSON recovery from a possibly-malformed LLM message.

    Returns the parsed object on success, ``None`` on total failure.
    Honours ``JSON_REPAIR_ENABLED`` — when False, no recovery is attempted.
    """
    if not JSON_REPAIR_ENABLED or not raw:
        return None

    text = _strip_thinking(raw)

    # 1. The strict path already understands fenced / preambled JSON.
    parsed = _parse_json_response(text)
    if parsed is not None:
        return parsed

    # 2. Cheap textual repairs.
    candidate = _SINGLE_QUOTE_KEY_RE.sub(r'"\1":', text)
    candidate = _TRAILING_COMMA_RE.sub(r"\1", candidate)

    parsed = _parse_json_response(candidate)
    if parsed is not None:
        return parsed

    # 3. json5 is permissive (single quotes, trailing commas, comments).
    try:
        import json5  # type: ignore
    except ImportError:
        return None
    try:
        return json5.loads(candidate)
    except Exception:
        return None

"""Parser dispatch — routes (url, html) to a per-domain parser module."""
from __future__ import annotations

import importlib
from typing import Optional
from urllib.parse import urlparse

from cfp.prompts_parser import parser_for_domain


def dispatch(url: str, html: str) -> Optional[dict]:
    """Route URL to its registered parser; return None for unknown domains."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    module_path = parser_for_domain(host)
    if not module_path:
        return None
    try:
        mod = importlib.import_module(module_path)
    except ImportError:
        return None
    fn = getattr(mod, "parse", None)
    if fn is None:
        return None
    return fn(url, html)

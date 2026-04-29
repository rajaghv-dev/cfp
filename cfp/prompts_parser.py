from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from config import PROMPTS_FILE
from cfp.models import Category


_RE_PARSER       = re.compile(r"^PARSER:\s+(\S+)\s+->\s+(\S+)\s*$")
_RE_PROMPT_OPEN  = re.compile(r"^(PROMPT_[A-Z0-9_]+):\s*\|\s*$")
_RE_CATEGORY     = re.compile(r"^CATEGORY:\s+(\S+)\s*$")
_RE_KEYWORD      = re.compile(r"^KEYWORD:\s+(.+?)\s*$")
_RE_URL          = re.compile(r"^URL:\s+(\S+)\s*$")
_RE_INDEX_SER    = re.compile(r"^INDEX_SERIES:\s+([A-Z])\s*$")
_RE_INDEX_JNL    = re.compile(r"^INDEX_JOURNAL:\s+([A-Z])\s*$")
_RE_TOPLEVEL_KEY = re.compile(r"^[A-Z][A-Z0-9_]*:")


@dataclass(frozen=True)
class SeedURL:
    url: str
    kind: Literal["category", "series_index", "journal_index"]
    category: Optional[Category] = None
    letter: Optional[str] = None


@dataclass(frozen=True)
class ParserEntry:
    domain: str
    module: str


@dataclass
class PromptsBundle:
    prompts:       dict[str, str]
    categories:    dict[Category, list[str]]
    seed_urls:     list[SeedURL]
    parsers:       list[ParserEntry]
    external_urls: list[str]


class PromptsSyntaxError(ValueError):
    """Malformed prompts.md content. Message includes line number."""


_BUNDLE_CACHE: dict[Path, tuple[float, PromptsBundle]] = {}


def load(path: Path = PROMPTS_FILE) -> PromptsBundle:
    """Parse prompts.md. Cached by (resolved-path, mtime); auto-invalidates."""
    p = path.resolve()
    try:
        mtime = p.stat().st_mtime
    except OSError as e:
        raise PromptsSyntaxError(f"cannot stat prompts file {p}: {e}") from e

    cached = _BUNDLE_CACHE.get(p)
    if cached and cached[0] == mtime:
        return cached[1]

    text = p.read_text(encoding="utf-8")
    if "\t" in text:
        raise PromptsSyntaxError("tabs forbidden — convert to 2 spaces")

    bundle = PromptsBundle(
        prompts={}, categories={}, seed_urls=[], parsers=[], external_urls=[]
    )

    current_category: Optional[Category] = None
    current_index_kind: Optional[str] = None  # "series_index" | "journal_index"
    current_index_letter: Optional[str] = None
    in_external_block = False
    in_code_fence = False

    current_prompt_name: Optional[str] = None
    current_prompt_lines: list[str] = []

    def flush_prompt() -> None:
        nonlocal current_prompt_name, current_prompt_lines
        if current_prompt_name:
            if current_prompt_name in bundle.prompts:
                raise PromptsSyntaxError(f"duplicate prompt: {current_prompt_name}")
            # strip trailing blank lines but preserve internal blanks
            while current_prompt_lines and current_prompt_lines[-1] == "":
                current_prompt_lines.pop()
            bundle.prompts[current_prompt_name] = "\n".join(current_prompt_lines)
        current_prompt_name = None
        current_prompt_lines = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        # Toggle code-fence state on ``` lines (skip everything inside)
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue

        # Detect entry into external sources block (heading-driven; decorative)
        if "EXTERNAL DATA SOURCES" in raw:
            flush_prompt()
            in_external_block = True
            current_category = None
            current_index_kind = None
            current_index_letter = None
            continue
        if "KNOWN PARSERS" in raw or "LLM SYSTEM PROMPTS" in raw:
            flush_prompt()
            in_external_block = False
            current_category = None
            current_index_kind = None
            current_index_letter = None
            continue
        if "CONFERENCE SERIES INDEX" in raw or "JOURNAL & BOOK SERIES INDEX" in raw:
            flush_prompt()
            in_external_block = False
            continue
        if "ADD YOUR PROMPTS" in raw:
            flush_prompt()
            continue

        # Inside a PROMPT_* body?
        if current_prompt_name is not None:
            if raw.startswith("  ") or raw == "":
                # body continues — strip exactly 2-space indent (preserve blank lines)
                current_prompt_lines.append(raw[2:] if raw.startswith("  ") else raw)
                continue
            # New top-level key encountered — close the body, fall through
            if _RE_TOPLEVEL_KEY.match(raw):
                flush_prompt()
            else:
                # Non-indented, non-key line inside a body — also closes the body
                flush_prompt()
                # then fall through to interpret this line normally

        # Skip headings, comments, blank lines (when not in a body)
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("##"):
            continue

        # Dispatch on prefix
        m = _RE_PROMPT_OPEN.match(raw)
        if m:
            current_prompt_name = m.group(1)
            current_prompt_lines = []
            continue

        m = _RE_CATEGORY.match(raw)
        if m:
            cat_str = m.group(1)
            try:
                current_category = Category(cat_str)
            except ValueError:
                raise PromptsSyntaxError(
                    f"unknown category: {cat_str} (line {lineno})"
                )
            bundle.categories.setdefault(current_category, [])
            current_index_kind = None
            current_index_letter = None
            continue

        m = _RE_KEYWORD.match(raw)
        if m:
            if current_category is None:
                raise PromptsSyntaxError(
                    f"orphan KEYWORD at line {lineno} (no open CATEGORY)"
                )
            bundle.categories[current_category].append(m.group(1))
            continue

        m = _RE_INDEX_SER.match(raw)
        if m:
            current_category = None
            current_index_kind = "series_index"
            current_index_letter = m.group(1)
            continue

        m = _RE_INDEX_JNL.match(raw)
        if m:
            current_category = None
            current_index_kind = "journal_index"
            current_index_letter = m.group(1)
            continue

        m = _RE_URL.match(raw)
        if m:
            url = m.group(1)
            if current_category is not None:
                bundle.seed_urls.append(SeedURL(
                    url=url, kind="category", category=current_category
                ))
            elif current_index_kind in ("series_index", "journal_index"):
                bundle.seed_urls.append(SeedURL(
                    url=url, kind=current_index_kind, letter=current_index_letter
                ))
            elif in_external_block:
                bundle.external_urls.append(url)
            else:
                raise PromptsSyntaxError(
                    f"orphan URL at line {lineno}: {url}"
                )
            continue

        m = _RE_PARSER.match(raw)
        if m:
            bundle.parsers.append(ParserEntry(domain=m.group(1), module=m.group(2)))
            continue

        # Unrecognised non-empty line — be strict
        if raw.strip() and not raw.lstrip().startswith("#"):
            # Lines with `:` that don't match one of our known prefixes:
            # Tolerate (decorative metadata in headings, etc.)
            if ":" in raw and not _RE_TOPLEVEL_KEY.match(raw):
                continue
            # Otherwise, it's a syntax-level surprise — but we keep silent
            # rather than fail the whole load (prompts.md has narrative text).

    # End of file
    flush_prompt()

    _BUNDLE_CACHE[p] = (mtime, bundle)
    return bundle


def get_prompt(name: str, *, path: Path = PROMPTS_FILE) -> str:
    return load(path).prompts[name]


def category_keywords(cat: Category, *, path: Path = PROMPTS_FILE) -> list[str]:
    return load(path).categories.get(cat, [])


def parser_for_domain(host: str, *, path: Path = PROMPTS_FILE) -> Optional[str]:
    """Longest-suffix match against PARSER registry."""
    bundle = load(path)
    host = host.lower().strip()
    best_module: Optional[str] = None
    best_len = -1
    for entry in bundle.parsers:
        domain = entry.domain.lower()
        if host == domain or host.endswith("." + domain):
            if len(domain) > best_len:
                best_len = len(domain)
                best_module = entry.module
    return best_module

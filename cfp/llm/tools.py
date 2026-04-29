"""Tool schemas + implementations for Qwen3 tool-calling tiers.

Two flavours of implementation are provided:

    TOOL_FUNCTIONS  — standalone, page-agnostic implementations that take
                      `html` as their first argument. Suitable for
                      stateless dispatch by the LLM.

    make_tool_impls(soup, current_url) — closure-bound dict used by
                      pipeline.py: each impl already has a soup baked in,
                      so the LLM only passes the parameters declared in
                      the JSON Schema.

Both flavours share ALL_TOOLS (the JSON Schema list).

`head_url` is the only async-bridged tool; it runs `cfp.fetch.head` on a
short-lived event loop because the calling LLM loop is synchronous.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from bs4 import BeautifulSoup

from cfp import fetch


# ---------------------------------------------------------------------------
# JSON Schema definitions — passed to the Ollama chat() `tools=` parameter
# ---------------------------------------------------------------------------

ALL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "extract_text",
            "description": "Return the visible text of all elements matching a CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector, e.g. 'h1', 'div.deadlines td', '#cfp'",
                    }
                },
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
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Python regex (case-insensitive) matched against link href and visible text.",
                    }
                },
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
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "The label text to search for (case-insensitive substring match).",
                    }
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "is_conference_page",
            "description": (
                "Return True if this page is a real conference/workshop CFP "
                "(not a journal landing page or spam)."
            ),
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
                "ComputerScience, Security, Data, Networking, Robotics, Bioinformatics."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Page text to classify."}
                },
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
                "properties": {
                    "text": {"type": "string", "description": "Page text to inspect."}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "head_url",
            "description": (
                "HEAD-fetch a URL and return its HTTP status. Used to verify "
                "that a CFP/series link is still live before persisting it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to probe."}
                },
                "required": ["url"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Category keyword table — used by classify_category. Mirrors the canonical
# set in cfp/models.py:Category. The LLM is expected to confirm the choice;
# this is a cheap pre-filter so the model has at least one anchor term.
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI":              ("artificial intelligence", " ai ", "agentic", "llm", "neural"),
    "ML":              ("machine learning", " ml ", "deep learning", "supervised"),
    "DevOps":          ("devops", "ci/cd", "kubernetes", "platform engineering"),
    "Linux":           ("linux", "kernel", "systemd", "open source"),
    "ChipDesign":      ("chip design", "vlsi", "asic", "fpga", "rtl", "verilog"),
    "Math":            ("mathematics", "topology", "algebra", "combinatorics"),
    "Legal":           ("legal", "law", "regulation", "compliance"),
    "ComputerScience": ("computer science", "algorithms", "complexity"),
    "Security":        ("security", "cryptography", "infosec", "vulnerability"),
    "Data":            ("data engineering", "data science", "analytics", "warehouse"),
    "Networking":      ("networking", "tcp", "sdn", "5g", "wireless"),
    "Robotics":        ("robotics", "manipulation", "slam", "autonomous"),
    "Bioinformatics":  ("bioinformatics", "genomics", "proteomics", "biology"),
}


_VIRTUAL_KEYWORDS = ("online", "virtual", "remote", "zoom", "webinar", "fully online")


# ---------------------------------------------------------------------------
# Standalone implementations (`html`-first for stateless callers)
# ---------------------------------------------------------------------------

def _soup(html: str) -> BeautifulSoup:
    # lxml is faster but optional; fall back to the stdlib parser silently.
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def extract_text(html: str, selector: str) -> str:
    soup = _soup(html)
    els = soup.select(selector)
    return " ".join(el.get_text(" ", strip=True) for el in els)[:2000]


def find_links(html: str, pattern: str) -> list[str]:
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return []
    soup = _soup(html)
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        if rx.search(href) or rx.search(text):
            out.append(href)
        if len(out) >= 50:
            break
    return out


def get_field(html: str, label: str) -> str:
    soup = _soup(html)
    needle = label.lower()
    # Two-column tables are the WikiCFP layout; <dt>/<dd> covers most others.
    for td in soup.find_all("td"):
        if needle in td.get_text(" ", strip=True).lower():
            nxt = td.find_next_sibling("td")
            if nxt:
                return nxt.get_text(" ", strip=True)
    for dt in soup.find_all("dt"):
        if needle in dt.get_text(" ", strip=True).lower():
            dd = dt.find_next_sibling("dd")
            if dd:
                return dd.get_text(" ", strip=True)
    return ""


def is_conference_page(html: str) -> bool:
    soup = _soup(html)
    text = soup.get_text(" ", strip=True).lower()
    signals = (
        "call for papers",
        "cfp",
        "submission deadline",
        "paper deadline",
        "workshop",
        "symposium",
        "conference",
    )
    return sum(1 for s in signals if s in text) >= 2


def classify_category(text: str) -> list[str]:
    lower = text.lower()
    hits = [
        cat
        for cat, kws in _CATEGORY_KEYWORDS.items()
        if any(kw in lower for kw in kws)
    ]
    return hits


def detect_virtual(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _VIRTUAL_KEYWORDS)


def head_url(url: str) -> dict[str, Any]:
    """HEAD a URL synchronously by spinning up a one-shot loop.

    cfp.fetch.head is async; the LLM tool loop is sync. Running on a fresh
    loop avoids contaminating any caller-managed loop. Returns
    {status, alive, url} so the model can branch on `alive`.
    """
    try:
        status = asyncio.run(fetch.head(url))
        return {"url": url, "status": status, "alive": 200 <= status < 400}
    except Exception as e:
        return {"url": url, "status": 0, "alive": False, "error": f"{type(e).__name__}: {e}"}


# Stateless dispatch table (html-first). Pipeline code that wants soup-bound
# closures should use make_tool_impls() instead.
TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "extract_text":       extract_text,
    "find_links":         find_links,
    "get_field":          get_field,
    "is_conference_page": is_conference_page,
    "classify_category":  classify_category,
    "detect_virtual":     detect_virtual,
    "head_url":           head_url,
}


# ---------------------------------------------------------------------------
# Closure factory — what pipeline.py actually uses (see codegen/09 §"Tool
# implementations"). The LLM only supplies the schema-declared params; html
# is captured from the surrounding scrape context.
# ---------------------------------------------------------------------------

def make_tool_impls(
    html_or_soup: "str | BeautifulSoup",
    current_url: str | None = None,
) -> dict[str, Callable[..., Any]]:
    soup = (
        html_or_soup
        if isinstance(html_or_soup, BeautifulSoup)
        else _soup(html_or_soup)
    )

    def _extract_text(selector: str) -> str:
        els = soup.select(selector)
        return " ".join(el.get_text(" ", strip=True) for el in els)[:2000]

    def _find_links(pattern: str) -> list[str]:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []
        out: list[str] = []
        for a in soup.find_all("a", href=True):
            if rx.search(a["href"]) or rx.search(a.get_text(" ", strip=True)):
                out.append(a["href"])
            if len(out) >= 50:
                break
        return out

    def _get_field(label: str) -> str:
        needle = label.lower()
        for td in soup.find_all("td"):
            if needle in td.get_text(" ", strip=True).lower():
                nxt = td.find_next_sibling("td")
                if nxt:
                    return nxt.get_text(" ", strip=True)
        for dt in soup.find_all("dt"):
            if needle in dt.get_text(" ", strip=True).lower():
                dd = dt.find_next_sibling("dd")
                if dd:
                    return dd.get_text(" ", strip=True)
        return ""

    def _is_conference_page() -> bool:
        text = soup.get_text(" ", strip=True).lower()
        signals = (
            "call for papers", "cfp", "submission deadline", "paper deadline",
            "workshop", "symposium", "conference",
        )
        return sum(1 for s in signals if s in text) >= 2

    return {
        "extract_text":       _extract_text,
        "find_links":         _find_links,
        "get_field":          _get_field,
        "is_conference_page": _is_conference_page,
        "classify_category":  classify_category,
        "detect_virtual":     detect_virtual,
        "head_url":           head_url,
    }


__all__ = [
    "ALL_TOOLS",
    "TOOL_FUNCTIONS",
    "make_tool_impls",
    "extract_text",
    "find_links",
    "get_field",
    "is_conference_page",
    "classify_category",
    "detect_virtual",
    "head_url",
]

"""Tests for cfp/llm/client.py and cfp/llm/tools.py.

Live tests against the local Ollama daemon are guarded by a reachability
check and skip cleanly if it isn't running.
"""
from __future__ import annotations

import json

import httpx
import pytest

from config import CFP_MACHINE, OLLAMA_HOST
from cfp.llm import (
    ESCALATE_REASONS,
    OllamaClient,
    TierEscalation,
    get_available_models,
    profile_intersection,
    resolve_model,
)
from cfp.llm.client import _parse_json_response, _strip_thinking
from cfp.llm.tools import (
    ALL_TOOLS,
    TOOL_FUNCTIONS,
    classify_category,
    detect_virtual,
    extract_text,
    find_links,
    get_field,
    is_conference_page,
    make_tool_impls,
)


# ---------------------------------------------------------------------------
# Live-Ollama gate
# ---------------------------------------------------------------------------

def _ollama_alive() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


_OLLAMA_OK = _ollama_alive()
_needs_ollama = pytest.mark.skipif(
    not _OLLAMA_OK, reason=f"Ollama not reachable at {OLLAMA_HOST}"
)


# ---------------------------------------------------------------------------
# _strip_thinking
# ---------------------------------------------------------------------------

def test_strip_thinking_single_line():
    assert _strip_thinking("<think>plan</think>answer") == "answer"


def test_strip_thinking_multiline():
    raw = "<think>\nstep one\nstep two\n</think>\nfinal answer"
    assert _strip_thinking(raw) == "final answer"


def test_strip_thinking_multiple_blocks():
    raw = "<think>a</think>x<think>b</think>y"
    assert _strip_thinking(raw) == "xy"


def test_strip_thinking_no_block():
    assert _strip_thinking("just text") == "just text"


# ---------------------------------------------------------------------------
# _parse_json_response — 3 levels + None
# ---------------------------------------------------------------------------

def test_parse_json_direct():
    assert _parse_json_response('{"ok": true}') == {"ok": True}


def test_parse_json_array():
    assert _parse_json_response("[1, 2, 3]") == [1, 2, 3]


def test_parse_json_strips_code_fence():
    raw = 'Here is the JSON:\n```json\n{"x": 1}\n```\nThanks.'
    assert _parse_json_response(raw) == {"x": 1}


def test_parse_json_strips_bare_fence():
    raw = "```\n{\"x\": 2}\n```"
    assert _parse_json_response(raw) == {"x": 2}


def test_parse_json_after_thinking():
    raw = '<think>weighing options</think>\n{"choice": "a"}'
    assert _parse_json_response(raw) == {"choice": "a"}


def test_parse_json_bracket_slice():
    raw = "preamble {\"y\": 9} trailer"
    assert _parse_json_response(raw) == {"y": 9}


def test_parse_json_returns_none_on_garbage():
    assert _parse_json_response("not json at all, no braces here") is None


# ---------------------------------------------------------------------------
# resolve_model
# ---------------------------------------------------------------------------

def test_resolve_model_qwen3_4b_on_gpu_mid():
    # config.py defaults CFP_MACHINE to "gpu_mid" and that profile contains
    # qwen3:4b-q4_K_M. Skip if the runner has overridden CFP_MACHINE.
    if CFP_MACHINE != "gpu_mid":
        pytest.skip(f"requires CFP_MACHINE=gpu_mid (got {CFP_MACHINE})")
    assert resolve_model("qwen3:4b") == "qwen3:4b-q4_K_M"


def test_resolve_model_passthrough_when_no_match():
    assert resolve_model("not-a-model") == "not-a-model"


def test_resolve_model_shortcut_tier1():
    if CFP_MACHINE != "gpu_mid":
        pytest.skip(f"requires CFP_MACHINE=gpu_mid (got {CFP_MACHINE})")
    assert resolve_model("tier1") == "qwen3:4b-q4_K_M"


def test_resolve_model_already_qualified():
    assert resolve_model("qwen3:4b-q4_K_M") == "qwen3:4b-q4_K_M"


# ---------------------------------------------------------------------------
# TierEscalation + ESCALATE_REASONS
# ---------------------------------------------------------------------------

def test_tier_escalation_attrs():
    exc = TierEscalation("low_confidence", target_tier=2)
    assert exc.reason == "low_confidence"
    assert exc.target_tier == 2
    assert "tier2" in str(exc)
    assert "low_confidence" in str(exc)


def test_escalate_reasons_is_frozenset():
    assert isinstance(ESCALATE_REASONS, frozenset)
    assert "low_confidence" in ESCALATE_REASONS
    assert "json_parse_failed" in ESCALATE_REASONS
    assert "model_unavailable" in ESCALATE_REASONS


# ---------------------------------------------------------------------------
# Tool schemas + impls (offline)
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """
<html><body>
<h1>ICML 2026 Call for Papers</h1>
<table>
  <tr><td>Submission Deadline</td><td>2026-02-01</td></tr>
  <tr><td>Conference Date</td><td>2026-07-15</td></tr>
</table>
<a href="https://icml.cc/Conferences/2026">Conference Site</a>
<a href="https://example.com/cfp">Workshop CFP</a>
<p>This online conference covers machine learning and artificial intelligence.</p>
</body></html>
"""


def test_all_tools_schema_shape():
    names = {t["function"]["name"] for t in ALL_TOOLS}
    assert {"extract_text", "find_links", "get_field", "is_conference_page",
            "classify_category", "detect_virtual", "head_url"} <= names
    for t in ALL_TOOLS:
        assert t["type"] == "function"
        assert "name" in t["function"]
        assert "parameters" in t["function"]


def test_tool_functions_dispatch_table():
    for name in (t["function"]["name"] for t in ALL_TOOLS):
        assert name in TOOL_FUNCTIONS
        assert callable(TOOL_FUNCTIONS[name])


def test_extract_text_basic():
    assert "ICML 2026" in extract_text(_SAMPLE_HTML, "h1")


def test_find_links_pattern():
    hits = find_links(_SAMPLE_HTML, r"cfp")
    assert any("example.com/cfp" in h for h in hits)


def test_get_field_two_column_table():
    assert get_field(_SAMPLE_HTML, "Submission Deadline") == "2026-02-01"


def test_is_conference_page_positive():
    assert is_conference_page(_SAMPLE_HTML) is True


def test_is_conference_page_negative():
    assert is_conference_page("<html><body>Hello world.</body></html>") is False


def test_classify_category_ml():
    cats = classify_category("This venue covers machine learning research.")
    assert "ML" in cats


def test_detect_virtual_true():
    assert detect_virtual("Held fully online via Zoom.") is True


def test_detect_virtual_false():
    assert detect_virtual("Held in Vienna, Austria.") is False


def test_make_tool_impls_uses_closure():
    impls = make_tool_impls(_SAMPLE_HTML, current_url="https://example.com/cfp")
    # The closure-bound impls take only the schema-declared params.
    assert "ICML 2026" in impls["extract_text"]("h1")
    assert impls["is_conference_page"]() is True
    assert impls["get_field"]("Submission Deadline") == "2026-02-01"


# ---------------------------------------------------------------------------
# Live Ollama tests
# ---------------------------------------------------------------------------

@_needs_ollama
def test_get_available_models_nonempty():
    models = get_available_models()
    assert isinstance(models, list)
    assert len(models) > 0


@_needs_ollama
def test_profile_intersection_has_expected_models():
    if CFP_MACHINE != "gpu_mid":
        pytest.skip(f"requires CFP_MACHINE=gpu_mid (got {CFP_MACHINE})")
    inter = profile_intersection()
    assert "qwen3:4b-q4_K_M" in inter
    assert "nomic-embed-text" in inter


@_needs_ollama
def test_ollama_client_chat_json_roundtrip():
    if CFP_MACHINE != "gpu_mid":
        pytest.skip(f"requires CFP_MACHINE=gpu_mid (got {CFP_MACHINE})")
    inter = profile_intersection()
    if "qwen3:4b-q4_K_M" not in inter:
        pytest.skip("qwen3:4b-q4_K_M not pulled locally")

    client = OllamaClient("qwen3:4b", timeout=30.0)
    raw = client.chat(
        messages=[
            {
                "role": "system",
                "content": "Reply with valid JSON only. No prose, no markdown.",
            },
            {
                "role": "user",
                "content": 'Return exactly {"ok": true} as JSON.',
            },
        ],
        format="json",
        options={"temperature": 0.0, "num_predict": 32},
    )
    parsed = _parse_json_response(raw)
    assert parsed is not None, f"unparseable response: {raw!r}"
    assert isinstance(parsed, dict)
    assert parsed.get("ok") is True

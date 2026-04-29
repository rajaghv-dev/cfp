"""Tests for cfp/cli.py via Click's CliRunner."""
from __future__ import annotations

import pytest
from click.testing import CliRunner
from unittest.mock import patch

from cfp.cli import main


def test_help_lists_all_commands():
    r = CliRunner().invoke(main, ["--help"])
    assert r.exit_code == 0
    for cmd in ("init-db", "enqueue-seeds", "run-pipeline", "generate-reports",
                "dedup-sweep", "bootstrap-ontology", "list-models", "doctor"):
        assert cmd in r.output


def test_list_models_outputs_space_separated():
    r = CliRunner().invoke(main, ["list-models"])
    assert r.exit_code == 0
    tokens = r.output.strip().split()
    # gpu_mid profile (default in .env) has 4 models
    assert len(tokens) >= 2
    assert any("qwen3" in t for t in tokens)
    assert "nomic-embed-text" in tokens


def test_bootstrap_ontology_v2_warning():
    r = CliRunner().invoke(main, ["bootstrap-ontology"])
    assert r.exit_code == 0
    assert "v2" in r.output


def test_doctor_all_healthy(monkeypatch):
    monkeypatch.setattr("cfp.cli._check_postgres", lambda: (True, "ok"))
    monkeypatch.setattr("cfp.cli._check_redis",    lambda: (True, "ok"))
    monkeypatch.setattr("cfp.cli._check_ollama",   lambda: (True, "ok"))
    monkeypatch.setattr("cfp.cli._check_models",   lambda: (True, "ok"))
    monkeypatch.setattr("cfp.cli._check_queue_depths", lambda: (True, "ok"))
    r = CliRunner().invoke(main, ["doctor"])
    assert r.exit_code == 0
    assert "all checks passed" in r.output


def test_doctor_one_service_down(monkeypatch):
    monkeypatch.setattr("cfp.cli._check_postgres", lambda: (True, "ok"))
    monkeypatch.setattr("cfp.cli._check_redis", lambda: (False, "Connection refused"))
    monkeypatch.setattr("cfp.cli._check_ollama", lambda: (True, "ok"))
    monkeypatch.setattr("cfp.cli._check_models", lambda: (True, "ok"))
    monkeypatch.setattr("cfp.cli._check_queue_depths", lambda: (True, "ok"))
    r = CliRunner().invoke(main, ["doctor"])
    assert r.exit_code == 1
    assert "redis: Connection refused" in r.output


def test_doctor_against_live_stack():
    """Smoke test against the running cfp_postgres + cfp_redis + cfp_ollama."""
    r = CliRunner().invoke(main, ["doctor", "--all"])
    # Don't assert exit code (Ollama may be cold); just verify it runs and emits status
    assert r.output
    assert "postgres" in r.output and "redis" in r.output


def test_run_pipeline_mutually_exclusive_flags():
    r = CliRunner().invoke(main, ["run-pipeline", "--tier1-only", "--tier2-only"])
    assert r.exit_code != 0
    assert "exclusive" in r.output.lower()

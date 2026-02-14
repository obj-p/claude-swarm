"""Tests for SwarmConfig."""

import re
from pathlib import Path

from claude_swarm.config import SwarmConfig


def test_defaults():
    c = SwarmConfig()
    assert c.max_workers == 4
    assert c.model == "sonnet"
    assert c.orchestrator_model == "opus"
    assert c.max_cost == 50.0
    assert c.max_worker_cost == 5.0
    assert c.create_pr is True
    assert c.dry_run is False


def test_repo_path_resolved(tmp_path):
    relative = tmp_path / "subdir"
    relative.mkdir()
    c = SwarmConfig(repo_path=relative)
    assert c.repo_path.is_absolute()
    assert c.repo_path == relative.resolve()


def test_run_id_auto_generated():
    c = SwarmConfig()
    rid = c.run_id
    assert isinstance(rid, str)
    assert re.match(r"\d{8}-\d{6}", rid)


def test_run_id_format():
    c = SwarmConfig()
    rid = c.run_id
    # YYYYMMDD-HHMMSS
    assert len(rid) == 15
    assert rid[8] == "-"


def test_run_id_setter():
    c = SwarmConfig()
    c.run_id = "custom-id"
    assert c.run_id == "custom-id"

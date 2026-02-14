"""Shared test fixtures for claude-swarm."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a real temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    readme = repo / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture()
def make_result_message():
    """Factory for mock ResultMessage objects."""

    def _make(
        result: str = "done",
        is_error: bool = False,
        total_cost_usd: float | None = 0.01,
        structured_output: dict | None = None,
    ):
        class FakeResultMessage:
            pass

        msg = FakeResultMessage()
        msg.result = result
        msg.is_error = is_error
        msg.total_cost_usd = total_cost_usd
        msg.structured_output = structured_output
        return msg

    return _make


@pytest.fixture()
def sample_task_plan_dict() -> dict:
    """Valid TaskPlan as raw dict for parsing tests."""
    return {
        "original_task": "Add logging",
        "reasoning": "Single worker suffices",
        "tasks": [
            {
                "worker_id": "worker-1",
                "title": "Add logging module",
                "description": "Create a logging module with structured output",
                "target_files": ["src/logging.py"],
                "acceptance_criteria": ["Module exists", "Tests pass"],
            }
        ],
        "integration_notes": "No special integration needed",
        "test_command": "pytest",
        "build_command": None,
    }

"""Tests for CLI commands."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from claude_swarm.cli import cli


@pytest.fixture()
def runner():
    return CliRunner()


def test_version(runner):
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "claude-swarm" in result.output or "0.1.0" in result.output


def test_run_command_registered(runner):
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "TASK" in result.output


def test_plan_command_registered(runner):
    result = runner.invoke(cli, ["plan", "--help"])
    assert result.exit_code == 0
    assert "TASK" in result.output


def test_cleanup_command_registered(runner):
    result = runner.invoke(cli, ["cleanup", "--help"])
    assert result.exit_code == 0


def test_run_requires_task(runner):
    result = runner.invoke(cli, ["run"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output


def test_plan_sets_dry_run(runner, tmp_path):
    with patch("claude_swarm.orchestrator.Orchestrator") as MockOrch:
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock()
        MockOrch.return_value = mock_instance

        result = runner.invoke(cli, ["plan", "test task", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        # Verify dry_run=True was set on the config
        config = MockOrch.call_args[0][0]
        assert config.dry_run is True
        mock_instance.run.assert_called_once()


def test_retries_option(runner, tmp_path):
    with patch("claude_swarm.orchestrator.Orchestrator") as MockOrch:
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock()
        MockOrch.return_value = mock_instance

        result = runner.invoke(cli, ["run", "test task", "--repo", str(tmp_path), "--retries", "3"])
        assert result.exit_code == 0
        config = MockOrch.call_args[0][0]
        assert config.max_worker_retries == 3


def test_no_escalation_option(runner, tmp_path):
    with patch("claude_swarm.orchestrator.Orchestrator") as MockOrch:
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock()
        MockOrch.return_value = mock_instance

        result = runner.invoke(cli, ["run", "test task", "--repo", str(tmp_path), "--no-escalation"])
        assert result.exit_code == 0
        config = MockOrch.call_args[0][0]
        assert config.enable_escalation is False


def test_no_conflict_resolution_option(runner, tmp_path):
    with patch("claude_swarm.orchestrator.Orchestrator") as MockOrch:
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock()
        MockOrch.return_value = mock_instance

        result = runner.invoke(cli, ["run", "test task", "--repo", str(tmp_path), "--no-conflict-resolution"])
        assert result.exit_code == 0
        config = MockOrch.call_args[0][0]
        assert config.resolve_conflicts is False


def test_status_command_registered(runner):
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "state" in result.output.lower() or "status" in result.output.lower()


def test_resume_command_registered(runner):
    result = runner.invoke(cli, ["resume", "--help"])
    assert result.exit_code == 0
    assert "resume" in result.output.lower()


def test_status_no_runs(runner, tmp_path):
    result = runner.invoke(cli, ["status", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "No swarm runs found" in result.output


def test_resume_no_interrupted(runner, tmp_path):
    result = runner.invoke(cli, ["resume", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "No interrupted runs" in result.output


def test_oversight_default_is_pr_gated(runner, tmp_path):
    with patch("claude_swarm.orchestrator.Orchestrator") as MockOrch:
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock()
        MockOrch.return_value = mock_instance

        result = runner.invoke(cli, ["run", "test task", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        config = MockOrch.call_args[0][0]
        assert config.oversight == "pr-gated"


def test_process_command_registered(runner):
    result = runner.invoke(cli, ["process", "--help"])
    assert result.exit_code == 0
    assert "--issue" in result.output


def test_watch_command_registered(runner):
    result = runner.invoke(cli, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--interval" in result.output


def test_process_requires_issue(runner):
    result = runner.invoke(cli, ["process"])
    assert result.exit_code != 0
    assert "Missing" in result.output or "required" in result.output.lower()


def test_status_shows_run_data(runner, tmp_path):
    from claude_swarm.config import SwarmConfig
    from claude_swarm.state import StateManager

    mgr = StateManager(tmp_path)
    config = SwarmConfig(task="test task", repo_path=tmp_path)
    mgr.start_run("run-abc", "test task", config)
    mgr.register_worker("run-abc", "w1", "Add logging", "swarm/run-abc/w1")

    result = runner.invoke(cli, ["status", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "run-abc" in result.output
    assert "test task" in result.output


def test_process_cli_integration(runner, tmp_path):
    issue_data = {
        "number": 42,
        "title": "Test issue",
        "body": "Body text",
        "labels": [{"name": "swarm"}],
    }
    with patch("claude_swarm.github.get_repo_slug", AsyncMock(return_value=("owner", "repo"))), \
         patch("claude_swarm.github.get_issue", AsyncMock(return_value=issue_data)), \
         patch("claude_swarm.issue_processor.IssueProcessor.process", AsyncMock()) as mock_process:
        result = runner.invoke(cli, ["process", "--issue", "42", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        mock_process.assert_called_once()


def test_process_max_cost_option(runner, tmp_path):
    issue_data = {
        "number": 42,
        "title": "Test issue",
        "body": "Body text",
        "labels": [{"name": "swarm"}],
    }

    captured_config = {}

    async def capture_process(self):
        captured_config["max_cost"] = self.issue_config.max_cost
        captured_config["max_worker_cost"] = self.issue_config.max_worker_cost

    with patch("claude_swarm.github.get_repo_slug", AsyncMock(return_value=("owner", "repo"))), \
         patch("claude_swarm.github.get_issue", AsyncMock(return_value=issue_data)), \
         patch("claude_swarm.issue_processor.IssueProcessor.process", capture_process):
        result = runner.invoke(cli, [
            "process", "--issue", "42", "--repo", str(tmp_path),
            "--max-cost", "10", "--max-worker-cost", "2.5",
        ])
        assert result.exit_code == 0
        assert captured_config["max_cost"] == 10.0
        assert captured_config["max_worker_cost"] == 2.5

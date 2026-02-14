"""Tests for CLI commands."""

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

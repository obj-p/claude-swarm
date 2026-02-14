"""Tests for configurable oversight levels."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from claude_swarm.cli import cli
from claude_swarm.config import SwarmConfig
from claude_swarm.models import OversightLevel, RunStatus


# ---------------------------------------------------------------------------
# TestOversightLevelEnum
# ---------------------------------------------------------------------------

class TestOversightLevelEnum:
    def test_values(self):
        assert OversightLevel.AUTONOMOUS.value == "autonomous"
        assert OversightLevel.PR_GATED.value == "pr-gated"
        assert OversightLevel.CHECKPOINT.value == "checkpoint"

    def test_from_string(self):
        assert OversightLevel("autonomous") is OversightLevel.AUTONOMOUS
        assert OversightLevel("pr-gated") is OversightLevel.PR_GATED
        assert OversightLevel("checkpoint") is OversightLevel.CHECKPOINT


# ---------------------------------------------------------------------------
# TestConfigOversight
# ---------------------------------------------------------------------------

class TestConfigOversight:
    def test_default_pr_gated(self):
        config = SwarmConfig()
        assert config.oversight == "pr-gated"

    def test_can_set_autonomous(self):
        config = SwarmConfig(oversight="autonomous")
        assert config.oversight == "autonomous"

    def test_can_set_checkpoint(self):
        config = SwarmConfig(oversight="checkpoint")
        assert config.oversight == "checkpoint"


# ---------------------------------------------------------------------------
# TestCLIOversight
# ---------------------------------------------------------------------------

class TestCLIOversight:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_option_registered(self, runner):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--oversight" in result.output

    def test_passed_to_config(self, runner, tmp_path):
        with patch("claude_swarm.orchestrator.Orchestrator") as MockOrch:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock()
            MockOrch.return_value = mock_instance

            result = runner.invoke(cli, [
                "run", "test task", "--repo", str(tmp_path),
                "--oversight", "autonomous",
            ])
            assert result.exit_code == 0
            config = MockOrch.call_args[0][0]
            assert config.oversight == "autonomous"

    def test_autonomous_requires_pr(self, runner, tmp_path):
        result = runner.invoke(cli, [
            "run", "test task", "--repo", str(tmp_path),
            "--oversight", "autonomous", "--no-pr",
        ])
        assert result.exit_code != 0
        assert "incompatible" in result.output.lower() or "Usage" in result.output

    def test_checkpoint_passed(self, runner, tmp_path):
        with patch("claude_swarm.orchestrator.Orchestrator") as MockOrch:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock()
            MockOrch.return_value = mock_instance

            result = runner.invoke(cli, [
                "run", "test task", "--repo", str(tmp_path),
                "--oversight", "checkpoint",
            ])
            assert result.exit_code == 0
            config = MockOrch.call_args[0][0]
            assert config.oversight == "checkpoint"


# ---------------------------------------------------------------------------
# TestCheckpointMode
# ---------------------------------------------------------------------------

class TestCheckpointMode:
    @pytest.fixture()
    def _make_orchestrator(self, tmp_path):
        """Factory that creates a mocked Orchestrator for checkpoint tests."""
        def _factory(oversight="checkpoint"):
            config = SwarmConfig(
                task="test task",
                repo_path=tmp_path,
                oversight=oversight,
            )
            with patch("claude_swarm.orchestrator.SessionRecorder"), \
                 patch("claude_swarm.orchestrator.WorktreeManager"), \
                 patch("claude_swarm.orchestrator.StateManager"):
                from claude_swarm.orchestrator import Orchestrator
                orch = Orchestrator(config, run_id="test-run")
            return orch
        return _factory

    @pytest.mark.asyncio
    async def test_checkpoint_prompts_on_plan(self, _make_orchestrator):
        orch = _make_orchestrator("checkpoint")
        # Mock input to approve
        with patch("builtins.input", return_value="y"):
            approved = await orch._checkpoint("Execute 2 worker(s)?")
        assert approved is True

    @pytest.mark.asyncio
    async def test_decline_stops_run(self, _make_orchestrator):
        orch = _make_orchestrator("checkpoint")
        with patch("builtins.input", return_value="n"):
            approved = await orch._checkpoint("Execute 2 worker(s)?")
        assert approved is False
        # Status should be INTERRUPTED
        orch.state_mgr.set_run_status.assert_any_call("test-run", RunStatus.INTERRUPTED)

    @pytest.mark.asyncio
    async def test_pr_gated_skips_checkpoints(self, _make_orchestrator):
        orch = _make_orchestrator("pr-gated")
        with patch("builtins.input") as mock_input:
            approved = await orch._checkpoint("Should not prompt")
        assert approved is True
        mock_input.assert_not_called()

    @pytest.mark.asyncio
    async def test_checkpoint_records_paused_status(self, _make_orchestrator):
        orch = _make_orchestrator("checkpoint")
        with patch("builtins.input", return_value="y"):
            await orch._checkpoint("Execute?")
        orch.state_mgr.set_run_status.assert_any_call("test-run", RunStatus.PAUSED_CHECKPOINT)

    @pytest.mark.asyncio
    async def test_checkpoint_restores_status_on_approval(self, _make_orchestrator):
        orch = _make_orchestrator("checkpoint")
        with patch("builtins.input", return_value="y"):
            await orch._checkpoint("Execute?", resume_status=RunStatus.INTEGRATING)
        # After approval, status should be restored to the resume_status
        orch.state_mgr.set_run_status.assert_any_call("test-run", RunStatus.INTEGRATING)


# ---------------------------------------------------------------------------
# TestAutonomousMode
# ---------------------------------------------------------------------------

class TestAutonomousMode:
    @pytest.mark.asyncio
    async def test_auto_merge_called_after_pr(self):
        from claude_swarm.integrator import auto_merge_pr

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(return_value=(b"", b""))
            proc_mock.returncode = 0
            mock_exec.return_value = proc_mock

            result = await auto_merge_pr("https://github.com/o/r/pull/1", Path("/tmp"))
            assert result is True
            # Should have been called with --auto --squash
            args = mock_exec.call_args[0]
            assert "gh" in args
            assert "--auto" in args
            assert "--squash" in args

    @pytest.mark.asyncio
    async def test_auto_merge_failure_nonfatal(self):
        from claude_swarm.integrator import auto_merge_pr

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(return_value=(b"", b"error"))
            proc_mock.returncode = 1
            mock_exec.return_value = proc_mock

            result = await auto_merge_pr("https://github.com/o/r/pull/1", Path("/tmp"))
            assert result is False
            # Only one attempt — no fallback to direct merge
            assert mock_exec.call_count == 1

    @pytest.mark.asyncio
    async def test_auto_merge_gh_auto_succeeds(self):
        from claude_swarm.integrator import auto_merge_pr

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(return_value=(b"ok", b""))
            proc_mock.returncode = 0
            mock_exec.return_value = proc_mock

            result = await auto_merge_pr("https://github.com/o/r/pull/1", Path("/tmp"))
            assert result is True
            assert mock_exec.call_count == 1

    @pytest.mark.asyncio
    async def test_auto_merge_no_direct_fallback(self):
        """When --auto fails, auto_merge_pr should NOT fall back to a direct merge."""
        from claude_swarm.integrator import auto_merge_pr

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(return_value=(b"", b"not enabled"))
            proc_mock.returncode = 1
            mock_exec.return_value = proc_mock

            result = await auto_merge_pr("https://github.com/o/r/pull/1", Path("/tmp"))
            assert result is False
            # Must NOT attempt a second (direct) merge
            assert mock_exec.call_count == 1


# ---------------------------------------------------------------------------
# TestOversightState
# ---------------------------------------------------------------------------

class TestOversightState:
    def test_oversight_in_config_snapshot(self, tmp_path):
        from claude_swarm.state import StateManager

        config = SwarmConfig(task="test", repo_path=tmp_path, oversight="checkpoint")
        mgr = StateManager(tmp_path)
        run_state = mgr.start_run("run-1", "test", config)
        assert run_state.config_snapshot["oversight"] == "checkpoint"

    def test_paused_checkpoint_status_transition(self, tmp_path):
        from claude_swarm.state import StateManager

        config = SwarmConfig(task="test", repo_path=tmp_path)
        mgr = StateManager(tmp_path)
        mgr.start_run("run-1", "test", config)
        mgr.set_run_status("run-1", RunStatus.PAUSED_CHECKPOINT)
        run = mgr.get_run("run-1")
        assert run.status == RunStatus.PAUSED_CHECKPOINT

    def test_default_oversight_is_pr_gated(self, tmp_path):
        from claude_swarm.state import StateManager

        config = SwarmConfig(task="test", repo_path=tmp_path)
        mgr = StateManager(tmp_path)
        run_state = mgr.start_run("run-1", "test", config)
        assert run_state.config_snapshot["oversight"] == "pr-gated"

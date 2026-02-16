"""Tests for issue processor and watcher."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_swarm.issue_processor import (
    IssueProcessor,
    IssueWatcher,
    _parse_label_config,
    issue_config_to_swarm_config,
    parse_issue_config,
)
from claude_swarm.models import IssueConfig, SwarmResult, TaskPlan, WorkerResult, WorkerTask


def _make_issue_data(
    number: int = 42,
    title: str = "Add logging",
    body: str = "Please add structured logging.",
    labels: list[str] | None = None,
) -> dict:
    """Build a mock GitHub issue dict."""
    if labels is None:
        labels = ["swarm"]
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": lbl} for lbl in labels],
    }


def _make_swarm_result(run_id: str = "test-run", pr_url: str | None = None) -> SwarmResult:
    plan = TaskPlan(
        original_task="task",
        reasoning="r",
        tasks=[WorkerTask(worker_id="w1", title="t", description="d")],
    )
    return SwarmResult(
        run_id=run_id,
        task="task",
        plan=plan,
        worker_results=[WorkerResult(worker_id="w1", success=True, cost_usd=0.50)],
        integration_success=True,
        pr_url=pr_url,
        total_cost_usd=0.50,
    )


class TestParseIssueConfig:
    def test_basic_issue(self):
        data = _make_issue_data()
        config = parse_issue_config(data, "owner", "repo")
        assert config.issue_number == 42
        assert config.owner == "owner"
        assert config.repo_name == "repo"
        assert config.title == "Add logging"
        assert config.body == "Please add structured logging."

    def test_oversight_from_label(self):
        data = _make_issue_data(labels=["swarm", "oversight:autonomous"])
        config = parse_issue_config(data, "owner", "repo")
        assert config.oversight == "autonomous"

    def test_model_from_label(self):
        data = _make_issue_data(labels=["swarm", "model:opus"])
        config = parse_issue_config(data, "owner", "repo")
        assert config.model == "opus"

    def test_workers_from_label(self):
        data = _make_issue_data(labels=["swarm", "workers:6"])
        config = parse_issue_config(data, "owner", "repo")
        assert config.max_workers == 6

    def test_cost_from_label(self):
        data = _make_issue_data(labels=["swarm", "cost:100"])
        config = parse_issue_config(data, "owner", "repo")
        assert config.max_cost == 100.0

    def test_no_overrides(self):
        data = _make_issue_data(labels=["swarm", "bug"])
        config = parse_issue_config(data, "owner", "repo")
        assert config.oversight is None
        assert config.model is None
        assert config.max_workers is None
        assert config.max_cost is None

    def test_strips_swarm_prefix(self):
        data = _make_issue_data(title="[swarm] Fix the tests")
        config = parse_issue_config(data, "owner", "repo")
        assert config.task_description.startswith("Fix the tests")

    def test_none_body_becomes_empty(self):
        data = _make_issue_data(body=None)
        config = parse_issue_config(data, "owner", "repo")
        assert config.body == ""


class TestParseLabelConfig:
    def test_no_matching_labels(self):
        assert _parse_label_config(["swarm", "bug"]) == {}

    def test_invalid_workers_ignored(self):
        result = _parse_label_config(["workers:abc"])
        assert "max_workers" not in result

    def test_invalid_cost_ignored(self):
        result = _parse_label_config(["cost:xyz"])
        assert "max_cost" not in result

    def test_invalid_oversight_ignored(self):
        result = _parse_label_config(["oversight:yolo"])
        assert "oversight" not in result

    def test_worker_cost_from_label(self):
        result = _parse_label_config(["worker-cost:5.0"])
        assert result["max_worker_cost"] == 5.0


class TestIssueConfigToSwarmConfig:
    def test_defaults(self):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="Do thing", body="details",
        )
        config = issue_config_to_swarm_config(ic, Path("/repo"))
        assert config.task == "Do thing\n\ndetails"
        assert config.create_pr is True
        assert config.issue_number == 1
        assert config.oversight == "pr-gated"  # default
        assert config.model == "sonnet"  # default

    def test_overrides_applied(self):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="Do thing", body="details",
            oversight="autonomous", model="opus", max_workers=8, max_cost=100.0,
        )
        config = issue_config_to_swarm_config(ic, Path("/repo"))
        assert config.oversight == "autonomous"
        assert config.model == "opus"
        assert config.max_workers == 8
        assert config.max_cost == 100.0

    def test_create_pr_always_true(self):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        config = issue_config_to_swarm_config(ic, Path("/repo"))
        assert config.create_pr is True

    def test_issue_number_set(self):
        ic = IssueConfig(
            issue_number=99, owner="o", repo_name="r",
            title="T", body="B",
        )
        config = issue_config_to_swarm_config(ic, Path("/repo"))
        assert config.issue_number == 99


class TestIssueProcessor:
    @pytest.mark.asyncio
    async def test_claim_swaps_labels(self, tmp_path):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)
        with patch("claude_swarm.github.remove_label", AsyncMock()) as rm, \
             patch("claude_swarm.github.add_label", AsyncMock()) as add:
            result = await processor.claim()
            assert result is True
            rm.assert_called_once_with("o", "r", 1, "swarm", cwd=tmp_path)
            add.assert_called_once_with("o", "r", 1, "swarm:active", cwd=tmp_path)

    @pytest.mark.asyncio
    async def test_claim_fails_returns_false(self, tmp_path):
        from claude_swarm.errors import GitHubError
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)
        with patch("claude_swarm.github.remove_label", AsyncMock(side_effect=GitHubError("nope"))):
            result = await processor.claim()
            assert result is False

    @pytest.mark.asyncio
    async def test_process_success_flow(self, tmp_path):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)
        result = _make_swarm_result(pr_url="https://github.com/o/r/pull/1")

        with patch.object(processor, "claim", AsyncMock(return_value=True)), \
             patch.object(processor, "_run_swarm", AsyncMock(return_value=result)), \
             patch.object(processor, "_post_result_comment", AsyncMock()) as post_result, \
             patch.object(processor, "_mark_done", AsyncMock()) as mark_done:
            await processor.process()
            post_result.assert_called_once_with(result)
            mark_done.assert_called_once_with(result.pr_url)

    @pytest.mark.asyncio
    async def test_process_failure_posts_error(self, tmp_path):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)

        with patch.object(processor, "claim", AsyncMock(return_value=True)), \
             patch.object(processor, "_run_swarm", AsyncMock(side_effect=RuntimeError("boom"))), \
             patch.object(processor, "_mark_failed", AsyncMock()) as mark_failed:
            await processor.process()
            mark_failed.assert_called_once_with("boom")

    @pytest.mark.asyncio
    async def test_mark_done_closes_issue(self, tmp_path):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)
        with patch("claude_swarm.github.remove_label", AsyncMock()), \
             patch("claude_swarm.github.add_label", AsyncMock()) as add, \
             patch("claude_swarm.github.close_issue", AsyncMock()) as close:
            await processor._mark_done("https://github.com/o/r/pull/1")
            add.assert_called_once_with("o", "r", 1, "swarm:done", cwd=tmp_path)
            close.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_failed_leaves_open(self, tmp_path):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)
        with patch("claude_swarm.github.post_comment", AsyncMock()), \
             patch("claude_swarm.github.remove_label", AsyncMock()), \
             patch("claude_swarm.github.add_label", AsyncMock()) as add, \
             patch("claude_swarm.github.close_issue", AsyncMock()) as close:
            await processor._mark_failed("oops")
            add.assert_called_once_with("o", "r", 1, "swarm:failed", cwd=tmp_path)
            close.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_failed_escapes_backticks(self, tmp_path):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)
        with patch("claude_swarm.github.post_comment", AsyncMock()) as mock_comment, \
             patch("claude_swarm.github.remove_label", AsyncMock()), \
             patch("claude_swarm.github.add_label", AsyncMock()):
            await processor._mark_failed("error with ``` backticks ```")
            posted_body = mock_comment.call_args[0][3]
            # The error body should not contain raw triple backticks from user input
            assert "``` backticks ```" not in posted_body
            assert "` ` ` backticks ` ` `" in posted_body

    @pytest.mark.asyncio
    async def test_post_result_comment_format(self, tmp_path):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
        )
        processor = IssueProcessor(ic, tmp_path)
        result = _make_swarm_result(
            run_id="test-run",
            pr_url="https://github.com/o/r/pull/1",
        )
        with patch("claude_swarm.github.post_comment", AsyncMock()) as mock_comment:
            await processor._post_result_comment(result)
            posted_body = mock_comment.call_args[0][3]
            assert "| Worker | Status | Cost |" in posted_body
            assert "| w1 | OK | $0.50 |" in posted_body
            assert "**Total cost**: $0.50" in posted_body
            assert "https://github.com/o/r/pull/1" in posted_body


class TestIssueWatcher:
    @pytest.mark.asyncio
    async def test_poll_once_processes_issue(self, tmp_path):
        issues = [_make_issue_data(number=1)]
        watcher = IssueWatcher(tmp_path, "o", "r")

        with patch("claude_swarm.github.list_issues", AsyncMock(return_value=issues)), \
             patch("claude_swarm.issue_processor.IssueProcessor") as MockProcessor:
            mock_instance = MagicMock()
            mock_instance.process = AsyncMock()
            MockProcessor.return_value = mock_instance

            count = await watcher._poll_once()
            assert count == 1
            mock_instance.process.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_once_skips_empty(self, tmp_path):
        watcher = IssueWatcher(tmp_path, "o", "r")
        with patch("claude_swarm.github.list_issues", AsyncMock(return_value=[])):
            count = await watcher._poll_once()
            assert count == 0

    @pytest.mark.asyncio
    async def test_stop_breaks_loop(self, tmp_path):
        watcher = IssueWatcher(tmp_path, "o", "r", interval=1)

        async def stop_after_start():
            await asyncio.sleep(0.1)
            watcher.stop()

        with patch("claude_swarm.github.ensure_labels_exist", AsyncMock()), \
             patch.object(watcher, "_poll_once", AsyncMock(return_value=0)):
            # Run watcher and stop concurrently
            await asyncio.gather(watcher.run(), stop_after_start())
        assert watcher._running is False

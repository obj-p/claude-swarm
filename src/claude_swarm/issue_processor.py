"""Process GitHub issues through the swarm pipeline."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from claude_swarm import github
from claude_swarm.config import SwarmConfig
from claude_swarm.errors import GitHubError
from claude_swarm.models import IssueConfig, OversightLevel, SwarmResult

logger = logging.getLogger(__name__)


def parse_issue_config(issue_data: dict, owner: str, repo_name: str) -> IssueConfig:
    """Parse a GitHub issue dict into an IssueConfig."""
    labels = [
        lbl["name"] if isinstance(lbl, dict) else lbl
        for lbl in issue_data.get("labels", [])
    ]
    overrides = _parse_label_config(labels)
    return IssueConfig(
        issue_number=issue_data["number"],
        owner=owner,
        repo_name=repo_name,
        title=issue_data.get("title", ""),
        body=issue_data.get("body", "") or "",
        labels=labels,
        **overrides,
    )


_VALID_OVERSIGHT = {level.value for level in OversightLevel}


def _parse_label_config(labels: list[str]) -> dict:
    """Extract config overrides from labels.

    Patterns:
      - oversight:autonomous, oversight:pr-gated, oversight:checkpoint
      - model:opus, model:sonnet
      - workers:6
      - cost:100
      - worker-cost:5
    """
    overrides: dict = {}
    for label in labels:
        if label.startswith("oversight:"):
            value = label.split(":", 1)[1]
            if value in _VALID_OVERSIGHT:
                overrides["oversight"] = value
            else:
                logger.warning("Ignoring invalid oversight label: %s", label)
        elif label.startswith("model:"):
            overrides["model"] = label.split(":", 1)[1]
        elif label.startswith("workers:"):
            try:
                overrides["max_workers"] = int(label.split(":", 1)[1])
            except ValueError:
                pass
        elif label.startswith("cost:"):
            try:
                overrides["max_cost"] = float(label.split(":", 1)[1])
            except ValueError:
                pass
        elif label.startswith("worker-cost:"):
            try:
                overrides["max_worker_cost"] = float(label.split(":", 1)[1])
            except ValueError:
                pass
    return overrides


def issue_config_to_swarm_config(issue_config: IssueConfig, repo_path: Path) -> SwarmConfig:
    """Convert an IssueConfig into a SwarmConfig for the orchestrator."""
    config = SwarmConfig(
        task=issue_config.task_description,
        repo_path=repo_path,
        create_pr=True,
        issue_number=issue_config.issue_number,
    )
    if issue_config.oversight is not None:
        config.oversight = issue_config.oversight
    if issue_config.model is not None:
        config.model = issue_config.model
    if issue_config.max_workers is not None:
        config.max_workers = issue_config.max_workers
    if issue_config.max_cost is not None:
        config.max_cost = issue_config.max_cost
    if issue_config.max_worker_cost is not None:
        config.max_worker_cost = issue_config.max_worker_cost
    return config


class IssueProcessor:
    """Processes a single GitHub issue through the swarm pipeline."""

    def __init__(
        self,
        issue_config: IssueConfig,
        repo_path: Path,
        *,
        trigger_label: str = "swarm",
    ) -> None:
        self.issue_config = issue_config
        self.repo_path = repo_path
        self.trigger_label = trigger_label

    @property
    def owner(self) -> str:
        return self.issue_config.owner

    @property
    def repo_name(self) -> str:
        return self.issue_config.repo_name

    @property
    def issue_number(self) -> int:
        return self.issue_config.issue_number

    async def claim(self) -> bool:
        """Swap trigger label -> swarm:active. Returns False on GitHub API error.

        Note: This is not fully atomic. If multiple watcher instances run
        concurrently, both may successfully claim the same issue. For
        reliable single-processing, run only one ``swarm watch`` instance
        per repository.
        """
        try:
            await github.remove_label(
                self.owner, self.repo_name, self.issue_number,
                self.trigger_label, cwd=self.repo_path,
            )
            await github.add_label(
                self.owner, self.repo_name, self.issue_number,
                "swarm:active", cwd=self.repo_path,
            )
            return True
        except GitHubError as e:
            logger.warning("Failed to claim issue #%d: %s", self.issue_number, e)
            return False

    async def process(self) -> None:
        """Full pipeline: claim -> run orchestrator -> report results -> mark done/failed."""
        if not await self.claim():
            return

        config = issue_config_to_swarm_config(self.issue_config, self.repo_path)

        try:
            result = await self._run_swarm(config)
            await self._post_result_comment(result)
            await self._mark_done(result.pr_url)
        except Exception as e:
            logger.error("Issue #%d processing failed: %s", self.issue_number, e)
            await self._mark_failed(str(e))

    async def _run_swarm(self, config: SwarmConfig) -> SwarmResult:
        """Instantiate Orchestrator, call run(), return result."""
        from claude_swarm.orchestrator import Orchestrator

        orchestrator = Orchestrator(config)
        await self._post_started_comment(orchestrator.run_id)
        return await orchestrator.run()

    async def _post_started_comment(self, run_id: str) -> None:
        body = f"Swarm run `{run_id}` started."
        try:
            await github.post_comment(
                self.owner, self.repo_name, self.issue_number,
                body, cwd=self.repo_path,
            )
        except GitHubError as e:
            logger.warning("Failed to post start comment: %s", e)

    async def _post_result_comment(self, result: SwarmResult) -> None:
        lines = [f"Swarm run `{result.run_id}` completed."]
        lines.append("")

        # Worker results table
        lines.append("| Worker | Status | Cost |")
        lines.append("|--------|--------|------|")
        for wr in result.worker_results:
            status = "OK" if wr.success else "FAIL"
            cost = f"${wr.cost_usd:.2f}" if wr.cost_usd is not None else "-"
            lines.append(f"| {wr.worker_id} | {status} | {cost} |")

        lines.append("")
        lines.append(f"**Total cost**: ${result.total_cost_usd:.2f}")

        if result.pr_url:
            lines.append(f"\nPR: {result.pr_url}")

        body = "\n".join(lines)
        try:
            await github.post_comment(
                self.owner, self.repo_name, self.issue_number,
                body, cwd=self.repo_path,
            )
        except GitHubError as e:
            logger.warning("Failed to post result comment: %s", e)

    async def _mark_done(self, pr_url: str | None) -> None:
        """Remove swarm:active, add swarm:done, close issue."""
        try:
            await github.remove_label(
                self.owner, self.repo_name, self.issue_number,
                "swarm:active", cwd=self.repo_path,
            )
            await github.add_label(
                self.owner, self.repo_name, self.issue_number,
                "swarm:done", cwd=self.repo_path,
            )
            await github.close_issue(
                self.owner, self.repo_name, self.issue_number,
                cwd=self.repo_path,
            )
        except GitHubError as e:
            logger.warning("Failed to mark issue #%d as done: %s", self.issue_number, e)

    async def _mark_failed(self, error: str) -> None:
        """Remove swarm:active, add swarm:failed, post error comment."""
        try:
            escaped = error.replace("```", "` ` `")
            body = f"Swarm processing failed:\n\n```\n{escaped}\n```"
            await github.post_comment(
                self.owner, self.repo_name, self.issue_number,
                body, cwd=self.repo_path,
            )
        except GitHubError as e:
            logger.warning("Failed to post error comment: %s", e)
        try:
            await github.remove_label(
                self.owner, self.repo_name, self.issue_number,
                "swarm:active", cwd=self.repo_path,
            )
            await github.add_label(
                self.owner, self.repo_name, self.issue_number,
                "swarm:failed", cwd=self.repo_path,
            )
        except GitHubError as e:
            logger.warning("Failed to mark issue #%d as failed: %s", self.issue_number, e)


class IssueWatcher:
    """Poll loop for continuous GitHub issue watching.

    Warning: Run only one watcher per repository. The label-based claim
    mechanism is not atomic and concurrent watchers may double-process issues.
    """

    def __init__(
        self,
        repo_path: Path,
        owner: str,
        repo_name: str,
        *,
        trigger_label: str = "swarm",
        interval: int = 30,
    ) -> None:
        self.repo_path = repo_path
        self.owner = owner
        self.repo_name = repo_name
        self.trigger_label = trigger_label
        self.interval = interval
        self._running = True

    async def run(self) -> None:
        """Poll loop. Ensure labels exist, then poll/process/sleep."""
        await github.ensure_labels_exist(self.owner, self.repo_name, cwd=self.repo_path)

        while self._running:
            try:
                count = await self._poll_once()
                if count > 0:
                    logger.info("Processed %d issue(s)", count)
            except GitHubError as e:
                logger.error("Poll error: %s", e)
            except Exception as e:
                logger.error("Unexpected poll error: %s", e)

            # Sleep in small increments so stop() is responsive
            for _ in range(self.interval):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _poll_once(self) -> int:
        """Fetch issues with trigger label, process each sequentially. Returns count processed."""
        issues = await github.list_issues(
            self.owner, self.repo_name, self.trigger_label,
            exclude_labels=["swarm:active", "swarm:done", "swarm:failed"],
            cwd=self.repo_path,
        )

        processed = 0
        for issue_data in issues:
            if not self._running:
                break
            issue_config = parse_issue_config(issue_data, self.owner, self.repo_name)
            processor = IssueProcessor(
                issue_config, self.repo_path,
                trigger_label=self.trigger_label,
            )
            await processor.process()
            processed += 1

        return processed

    def stop(self) -> None:
        """Signal the loop to exit."""
        self._running = False

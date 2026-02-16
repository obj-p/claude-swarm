"""Core orchestration pipeline: plan -> execute -> integrate."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from claude_agent_sdk import ClaudeAgentOptions
from rich.console import Console
from rich.table import Table
from rich.text import Text

from claude_swarm.config import SwarmConfig
from claude_swarm.errors import PlanningError, SwarmError
from claude_swarm.guards import swarm_can_use_tool
from claude_swarm.integrator import integrate_results
from claude_swarm.models import RunStatus, SwarmResult, TaskPlan, WorkerResult, WorkerStatus, WorkerTask
from claude_swarm.coordination import CoordinationManager
from claude_swarm.prompts import PLANNER_SYSTEM_PROMPT
from claude_swarm.session import SessionRecorder
from claude_swarm.state import StateManager
from claude_swarm.util import run_agent
from claude_swarm.worker import spawn_worker_with_retry
from claude_swarm.worktree import WorktreeManager

logger = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    """Manages the full swarm pipeline: plan, execute, integrate."""

    def __init__(self, config: SwarmConfig, *, run_id: str | None = None) -> None:
        self.config = config
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.config.run_id = self.run_id
        self.worktree_mgr = WorktreeManager(config.repo_path, self.run_id)
        self.session = SessionRecorder(config.repo_path, self.run_id)
        self.state_mgr = StateManager(config.repo_path)
        self.coord_mgr = CoordinationManager(config.repo_path, self.run_id)

    async def _checkpoint(self, message: str, context: str = "", resume_status: RunStatus = RunStatus.EXECUTING) -> bool:
        """Prompt user for confirmation. Returns True if approved.

        No-op (returns True) for non-checkpoint modes.
        On approval, restores status to *resume_status* so a crash between
        checkpoint and the next explicit status change leaves the run resumable.
        """
        if self.config.oversight != "checkpoint":
            return True
        self.state_mgr.set_run_status(self.run_id, RunStatus.PAUSED_CHECKPOINT)
        console.print(f"\n[bold yellow]CHECKPOINT[/bold yellow]")
        if context:
            console.print(context)
        console.print(f"[yellow]{message}[/yellow]")
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: input("Proceed? [Y/n] "))
        approved = response.strip().lower() in ("y", "yes", "")
        if approved:
            self.state_mgr.set_run_status(self.run_id, resume_status)
        else:
            console.print("[red]Declined. Marking run as interrupted.[/red]")
            self.state_mgr.set_run_status(self.run_id, RunStatus.INTERRUPTED)
        return approved

    async def run(self) -> SwarmResult:
        """Execute the full pipeline."""
        start = time.monotonic()
        console.print(f"\n[bold blue]claude-swarm[/bold blue] run [dim]{self.run_id}[/dim]")
        console.print(f"[dim]Task:[/dim] {self.config.task}\n")

        # Register run in persistent state
        self.state_mgr.start_run(self.run_id, self.config.task, self.config)

        # Step 1: Plan
        plan = await self._plan_task()
        self.state_mgr.set_run_plan(self.run_id, plan)
        console.print(f"\n[green]Plan ready:[/green] {len(plan.tasks)} subtask(s)")
        for t in plan.tasks:
            console.print(f"  [dim]-[/dim] {t.worker_id}: {t.title}")
        if plan.test_command:
            console.print(f"  [dim]Tests:[/dim] {plan.test_command}")
        console.print()

        if self.config.dry_run:
            console.print("[yellow]Dry run — stopping before execution.[/yellow]")
            console.print(f"\n[dim]Plan JSON:[/dim]")
            console.print(plan.model_dump_json(indent=2))
            self.state_mgr.complete_run(self.run_id)
            return SwarmResult(
                run_id=self.run_id,
                task=self.config.task,
                plan=plan,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # Checkpoint 1: after planning, before execution
        task_list = "\n".join(f"  - {t.worker_id}: {t.title}" for t in plan.tasks)
        if not await self._checkpoint(
            f"Execute {len(plan.tasks)} worker(s)?",
            context=task_list,
        ):
            self.state_mgr.complete_run(self.run_id)
            return SwarmResult(
                run_id=self.run_id,
                task=self.config.task,
                plan=plan,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # Step 2: Execute workers
        self.state_mgr.set_run_status(self.run_id, RunStatus.EXECUTING)
        worker_results = await self._execute_workers(plan)

        # Step 3: Integrate
        successful = [r for r in worker_results if r.success]
        failed = [r for r in worker_results if not r.success]

        if failed:
            console.print(f"\n[yellow]{len(failed)} worker(s) failed:[/yellow]")
            for r in failed:
                console.print(f"  [red]-[/red] {r.worker_id}: {r.error}")

        pr_url: str | None = None
        integration_success = False

        if successful:
            # Checkpoint 2: after workers, before integration
            worker_summary = "\n".join(
                f"  - {r.worker_id}: {r.summary or 'completed'}" for r in successful
            )
            if not await self._checkpoint(
                f"Integrate {len(successful)} successful branch(es)?",
                context=worker_summary,
                resume_status=RunStatus.INTEGRATING,
            ):
                await self.worktree_mgr.cleanup_all()
                return SwarmResult(
                    run_id=self.run_id,
                    task=self.config.task,
                    plan=plan,
                    worker_results=worker_results,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            console.print(f"\n[blue]Integrating {len(successful)} successful branch(es)...[/blue]")
            self.state_mgr.set_run_status(self.run_id, RunStatus.INTEGRATING)
            self.session.integration_start()
            base_branch = await self.worktree_mgr.get_base_branch()

            # In checkpoint mode, defer PR creation to after the checkpoint
            should_create_pr = self.config.create_pr
            create_pr_now = should_create_pr and self.config.oversight != "checkpoint"

            try:
                integration_success, pr_url, error_msg = await integrate_results(
                    self.worktree_mgr,
                    worker_results,
                    base_branch,
                    run_id=self.run_id,
                    test_command=plan.test_command,
                    build_command=plan.build_command,
                    should_create_pr=create_pr_now,
                    review=self.config.review,
                    task_description=self.config.task,
                    orchestrator_model=self.config.orchestrator_model,
                    resolve_conflicts=self.config.resolve_conflicts,
                    notes_summary=self.coord_mgr.format_coordination_summary(),
                    issue_number=self.config.issue_number,
                )

                if integration_success:
                    self.session.merge_result(
                        success=True,
                        branches=[self.worktree_mgr.get_branch_name(r.worker_id) for r in successful],
                    )

                    # Checkpoint 3: after integration, before PR (checkpoint mode only)
                    if should_create_pr and self.config.oversight == "checkpoint":
                        if await self._checkpoint("Create PR?", resume_status=RunStatus.INTEGRATING):
                            from claude_swarm.integrator import create_pr as do_create_pr
                            integration_branch = self.worktree_mgr.get_branch_name("integration")
                            integration_path = self.worktree_mgr.get_worktree_path("integration")
                            if integration_path is None:
                                raise SwarmError("Integration worktree not found")
                            pr_url = await do_create_pr(
                                integration_path,
                                integration_branch,
                                base_branch,
                                run_id=self.run_id,
                                task_description=self.config.task,
                                worker_results=successful,
                                issue_number=self.config.issue_number,
                            )

                    if pr_url:
                        self.session.pr_created(pr_url)
                        console.print(f"\n[green bold]PR created:[/green bold] {pr_url}")

                        # Autonomous mode: auto-merge
                        if self.config.oversight == "autonomous":
                            from claude_swarm.integrator import auto_merge_pr
                            merged = await auto_merge_pr(pr_url, self.config.repo_path)
                            if merged:
                                console.print(f"[green bold]PR auto-merged:[/green bold] {pr_url}")
                            else:
                                console.print(f"[yellow]Auto-merge failed. PR remains open:[/yellow] {pr_url}")
                    else:
                        console.print("\n[green]Integration successful.[/green]")
                else:
                    self.session.merge_result(
                        success=False,
                        branches=[self.worktree_mgr.get_branch_name(r.worker_id) for r in successful],
                        error=error_msg,
                    )
                    console.print(f"\n[red]Integration failed:[/red] {error_msg}")

            except SwarmError as e:
                integration_success = False
                self.session.merge_result(success=False, branches=[], error=str(e))
                console.print(f"\n[red]Integration error:[/red] {e}")

        # Summary
        duration_ms = int((time.monotonic() - start) * 1000)
        total_cost = sum(r.cost_usd or 0 for r in worker_results)

        self._print_summary(worker_results, total_cost, duration_ms, pr_url)

        self.session.write_metadata()

        # Update persistent state
        if integration_success or not successful:
            self.state_mgr.complete_run(self.run_id, pr_url=pr_url)
        else:
            self.state_mgr.fail_run(self.run_id, "Integration failed")

        # Cleanup worktrees (keep branches for PR) and notes
        await self.worktree_mgr.cleanup_all()
        self.coord_mgr.cleanup()

        return SwarmResult(
            run_id=self.run_id,
            task=self.config.task,
            plan=plan,
            worker_results=worker_results,
            integration_success=integration_success,
            pr_url=pr_url,
            total_cost_usd=total_cost,
            duration_ms=duration_ms,
        )

    async def _plan_task(self) -> TaskPlan:
        """Use an Opus agent to discover the repo and decompose the task."""
        console.print("[blue]Planning...[/blue]")
        self.session.plan_start(self.config.task)

        plan_schema = {
            "type": "json_schema",
            "schema": TaskPlan.model_json_schema(),
        }

        system_prompt = PLANNER_SYSTEM_PROMPT.format(max_workers=self.config.max_workers)

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self.config.orchestrator_model,
            cwd=str(self.config.repo_path),
            permission_mode="default",
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            max_budget_usd=min(5.0, self.config.max_cost * 0.2),
            max_turns=30,
            output_format=plan_schema,
            setting_sources=["project"],
            can_use_tool=swarm_can_use_tool,
        )

        result = await run_agent(
            prompt=f"Analyze this repository and decompose the following task into parallel subtasks:\n\n{self.config.task}",
            options=options,
        )

        if result.is_error:
            raise PlanningError(f"Planning agent failed: {result.result}")

        # Parse structured output
        try:
            if result.structured_output:
                raw = result.structured_output
            elif result.result:
                raw = json.loads(result.result)
            else:
                raise PlanningError("Planning agent returned no output")

            plan = TaskPlan.model_validate(raw)
        except (json.JSONDecodeError, ValueError) as e:
            raise PlanningError(f"Failed to parse plan: {e}") from e

        # Enforce max_workers limit
        if len(plan.tasks) > self.config.max_workers:
            plan.tasks = plan.tasks[: self.config.max_workers]

        self.session.plan_complete(len(plan.tasks), cost_usd=result.total_cost_usd)
        return plan

    async def _execute_workers(self, plan: TaskPlan) -> list[WorkerResult]:
        """Spawn workers in parallel, each in its own worktree."""
        base_branch = await self.worktree_mgr.get_base_branch()

        # Disable gc during parallel operations
        await self.worktree_mgr.disable_gc()

        # Set up coordination directories (notes, messages, status)
        worker_ids = [t.worker_id for t in plan.tasks]
        notes_dir = self.coord_mgr.setup(worker_ids=worker_ids)
        coordination_dir = self.coord_mgr.coordination_dir

        # Create worktrees sequentially to avoid git lock contention
        console.print("[blue]Creating worktrees...[/blue]")
        worktree_paths: dict[str, Path] = {}
        for task in plan.tasks:
            path = await self.worktree_mgr.create_worktree(task.worker_id, base_branch)
            worktree_paths[task.worker_id] = path
            branch = self.worktree_mgr.get_branch_name(task.worker_id)
            self.state_mgr.register_worker(
                self.run_id, task.worker_id, task.title, branch,
            )

        # Launch workers with rate limiting
        console.print(f"[blue]Launching {len(plan.tasks)} worker(s)...[/blue]\n")
        semaphore = asyncio.Semaphore(self.config.max_workers)

        _running_cost = 0.0
        _cost_exceeded = False

        async def launch_with_throttle(task: WorkerTask, delay: float) -> WorkerResult:
            nonlocal _running_cost, _cost_exceeded
            await asyncio.sleep(delay)

            async with semaphore:
                # Check INSIDE semaphore so we see updates from just-finished workers
                if _cost_exceeded:
                    console.print(f"  {task.worker_id}: [yellow]skipped[/yellow] — cost limit exceeded")
                    self.state_mgr.update_worker(
                        self.run_id, task.worker_id,
                        status=WorkerStatus.FAILED,
                        error="Skipped: cost limit exceeded",
                        completed_at=self.state_mgr._now(),
                    )
                    return WorkerResult(
                        worker_id=task.worker_id,
                        success=False,
                        error="Skipped: cost limit exceeded",
                    )

                self.session.worker_start(task.worker_id, task.title)
                self.state_mgr.update_worker(
                    self.run_id, task.worker_id,
                    status=WorkerStatus.RUNNING, started_at=self.state_mgr._now(),
                )

                try:
                    result = await spawn_worker_with_retry(
                        task,
                        worktree_paths[task.worker_id],
                        model=self.config.model,
                        max_retries=self.config.max_worker_retries,
                        escalation_model=self.config.escalation_model,
                        enable_escalation=self.config.enable_escalation,
                        max_budget_usd=self.config.max_worker_cost,
                        notes_dir=notes_dir,
                        coordination_dir=coordination_dir,
                    )
                    # Get changed files from worktree
                    changed = await self.worktree_mgr.get_worktree_changed_files(task.worker_id)
                    result.files_changed = changed

                    self.session.worker_complete(
                        task.worker_id,
                        success=result.success,
                        cost_usd=result.cost_usd,
                        duration_ms=result.duration_ms,
                        files_changed=result.files_changed,
                        summary=result.summary,
                    )
                    self.state_mgr.update_worker(
                        self.run_id, task.worker_id,
                        status=WorkerStatus.COMPLETED if result.success else WorkerStatus.FAILED,
                        cost_usd=result.cost_usd,
                        duration_ms=result.duration_ms,
                        summary=result.summary,
                        files_changed=result.files_changed,
                        error=result.error,
                        attempt=result.attempt,
                        model_used=result.model_used,
                        completed_at=self.state_mgr._now(),
                    )
                    if result.cost_usd is not None:
                        _running_cost += result.cost_usd
                        if _running_cost > self.config.max_cost:
                            _cost_exceeded = True
                            logger.warning(
                                "Cost limit exceeded: $%.2f > $%.2f",
                                _running_cost, self.config.max_cost,
                            )
                            console.print(
                                f"  [yellow]Cost limit reached (${_running_cost:.2f} > "
                                f"${self.config.max_cost:.2f}). Remaining workers will be skipped.[/yellow]"
                            )

                    status = "[green]done[/green]" if result.success else "[red]failed[/red]"
                    cost_str = f" (${result.cost_usd:.2f})" if result.cost_usd is not None else ""
                    console.print(f"  {task.worker_id}: {status}{cost_str} — {task.title}")
                    return result

                except Exception as e:
                    self.session.worker_error(task.worker_id, str(e))
                    self.state_mgr.update_worker(
                        self.run_id, task.worker_id,
                        status=WorkerStatus.FAILED,
                        error=str(e),
                        completed_at=self.state_mgr._now(),
                    )
                    console.print(f"  {task.worker_id}: [red]error[/red] — {e}")
                    return WorkerResult(
                        worker_id=task.worker_id,
                        success=False,
                        error=str(e),
                    )

        # Stagger launches by 500ms
        coros = [
            launch_with_throttle(task, i * 0.5)
            for i, task in enumerate(plan.tasks)
        ]

        results = await asyncio.gather(*coros, return_exceptions=True)

        # Convert exceptions to WorkerResults
        final_results: list[WorkerResult] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                task = plan.tasks[i]
                final_results.append(WorkerResult(
                    worker_id=task.worker_id,
                    success=False,
                    error=str(r),
                ))
            else:
                final_results.append(r)

        return final_results

    def _print_summary(
        self,
        results: list[WorkerResult],
        total_cost: float,
        duration_ms: int,
        pr_url: str | None,
    ) -> None:
        """Print a summary table of the run."""
        console.print("\n" + "=" * 60)

        table = Table(title="Swarm Run Summary", show_header=True)
        table.add_column("Worker", style="cyan")
        table.add_column("Status")
        table.add_column("Cost", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Files", justify="right")

        for r in results:
            status = Text("OK", style="green") if r.success else Text("FAIL", style="red")
            cost = f"${r.cost_usd:.2f}" if r.cost_usd is not None else "-"
            duration = f"{r.duration_ms / 1000:.1f}s" if r.duration_ms else "-"
            files = str(len(r.files_changed)) if r.files_changed else "-"
            table.add_row(r.worker_id, status, cost, duration, files)

        console.print(table)
        console.print(f"\n[bold]Total cost:[/bold] ${total_cost:.2f}")
        console.print(f"[bold]Duration:[/bold] {duration_ms / 1000:.1f}s")
        if pr_url:
            console.print(f"[bold]PR:[/bold] {pr_url}")
        console.print()

    async def cleanup(self) -> None:
        """Emergency cleanup (e.g., on Ctrl-C)."""
        try:
            self.state_mgr.set_run_status(self.run_id, RunStatus.INTERRUPTED)
        except Exception as e:
            logger.error("Failed to update state on interrupt: %s", e)
        try:
            await self.worktree_mgr.cleanup_all(force=True)
        except Exception as e:
            logger.error("Cleanup failed: %s", e)
        try:
            self.coord_mgr.cleanup()
        except Exception as e:
            logger.error("Coordination cleanup failed: %s", e)

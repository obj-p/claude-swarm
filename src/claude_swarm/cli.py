"""CLI interface for claude-swarm."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console

from claude_swarm.config import SwarmConfig

console = Console()


@click.group()
@click.version_option(package_name="claude-swarm")
def cli() -> None:
    """claude-swarm: Orchestrate dynamic pools of Claude agents."""


@cli.command()
@click.argument("task")
@click.option("--repo", type=click.Path(exists=True, path_type=Path), default=None, help="Repository path (default: cwd)")
@click.option("--workers", type=int, default=4, help="Max parallel workers")
@click.option("--model", type=str, default="sonnet", help="Worker model (sonnet/opus)")
@click.option("--orchestrator-model", type=str, default="opus", help="Orchestrator model")
@click.option("--max-cost", type=float, default=50.0, help="Max total cost in USD")
@click.option("--max-worker-cost", type=float, default=5.0, help="Max cost per worker in USD")
@click.option("--pr/--no-pr", default=True, help="Create PR after integration")
@click.option("--dry-run", is_flag=True, help="Plan only, don't execute")
@click.option("--review", is_flag=True, help="Run semantic review after merge")
@click.option("--verbose", is_flag=True, help="Verbose output")
@click.option("--retries", type=int, default=1, help="Max attempts per worker (1 = no retry)")
@click.option("--no-escalation", is_flag=True, help="Disable model escalation on retry")
@click.option("--no-conflict-resolution", is_flag=True, help="Disable automated merge conflict resolution")
@click.option(
    "--oversight",
    type=click.Choice(["autonomous", "pr-gated", "checkpoint"], case_sensitive=False),
    default="pr-gated",
    help="Oversight level: autonomous (auto-merge), pr-gated (default), checkpoint (pause for approval)",
)
def run(
    task: str,
    repo: Path | None,
    workers: int,
    model: str,
    orchestrator_model: str,
    max_cost: float,
    max_worker_cost: float,
    pr: bool,
    dry_run: bool,
    review: bool,
    verbose: bool,
    retries: int,
    no_escalation: bool,
    no_conflict_resolution: bool,
    oversight: str,
) -> None:
    """Run the full swarm pipeline: plan, execute, integrate, PR."""
    if oversight == "autonomous" and not pr:
        raise click.UsageError("--oversight=autonomous requires PR creation (incompatible with --no-pr)")

    config = SwarmConfig(
        task=task,
        repo_path=repo or Path.cwd(),
        max_workers=workers,
        model=model,
        orchestrator_model=orchestrator_model,
        max_cost=max_cost,
        max_worker_cost=max_worker_cost,
        create_pr=pr,
        dry_run=dry_run,
        review=review,
        verbose=verbose,
        max_worker_retries=retries,
        enable_escalation=not no_escalation,
        resolve_conflicts=not no_conflict_resolution,
        oversight=oversight,
    )

    from claude_swarm.orchestrator import Orchestrator

    orchestrator = Orchestrator(config)

    async def _main() -> None:
        try:
            await orchestrator.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[yellow]Interrupted. Cleaning up...[/yellow]")
            await orchestrator.cleanup()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(130)


@cli.command()
@click.argument("task")
@click.option("--repo", type=click.Path(exists=True, path_type=Path), default=None, help="Repository path (default: cwd)")
@click.option("--workers", type=int, default=4, help="Max parallel workers")
@click.option("--model", type=str, default="sonnet", help="Worker model")
@click.option("--orchestrator-model", type=str, default="opus", help="Orchestrator model")
@click.option("--verbose", is_flag=True, help="Verbose output")
def plan(
    task: str,
    repo: Path | None,
    workers: int,
    model: str,
    orchestrator_model: str,
    verbose: bool,
) -> None:
    """Plan task decomposition without executing (alias for run --dry-run)."""
    config = SwarmConfig(
        task=task,
        repo_path=repo or Path.cwd(),
        max_workers=workers,
        model=model,
        orchestrator_model=orchestrator_model,
        dry_run=True,
        verbose=verbose,
    )

    from claude_swarm.orchestrator import Orchestrator

    orchestrator = Orchestrator(config)
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        sys.exit(130)


@cli.command()
@click.option("--repo", type=click.Path(exists=True, path_type=Path), default=None, help="Repository path (default: cwd)")
def cleanup(repo: Path | None) -> None:
    """Remove all swarm worktrees and branches."""
    from claude_swarm.worktree import WorktreeManager

    repo_path = repo or Path.cwd()
    manager = WorktreeManager(repo_path=repo_path, run_id="cleanup")
    asyncio.run(manager.cleanup_all(force=True))
    console.print("[green]Cleanup complete.[/green]")


@cli.command()
@click.option("--repo", type=click.Path(exists=True, path_type=Path), default=None, help="Repository path (default: cwd)")
def status(repo: Path | None) -> None:
    """Show the current state of any active or recent swarm run."""
    from rich.table import Table
    from rich.text import Text

    from claude_swarm.state import StateManager

    repo_path = repo or Path.cwd()
    mgr = StateManager(repo_path)
    state = mgr.load()

    if not state.runs:
        console.print("[dim]No swarm runs found.[/dim]")
        return

    # Show active run first, then most recent
    if state.active_run and state.active_run in state.runs:
        run = state.runs[state.active_run]
        console.print(f"\n[bold blue]Active run:[/bold blue] {run.run_id}")
    else:
        # Show most recent run
        run = max(state.runs.values(), key=lambda r: r.updated_at)
        console.print(f"\n[bold]Last run:[/bold] {run.run_id}")

    status_styles = {
        "planning": "blue",
        "executing": "blue",
        "integrating": "blue",
        "completed": "green",
        "failed": "red",
        "interrupted": "yellow",
        "paused_checkpoint": "yellow",
    }
    style = status_styles.get(run.status.value, "white")
    console.print(f"[dim]Status:[/dim]  [{style}]{run.status.value}[/{style}]")
    console.print(f"[dim]Task:[/dim]    {run.task}")
    console.print(f"[dim]Started:[/dim] {run.started_at}")
    if run.pr_url:
        console.print(f"[dim]PR:[/dim]      {run.pr_url}")

    if run.workers:
        table = Table(show_header=True, title="Workers")
        table.add_column("Worker", style="cyan")
        table.add_column("Status")
        table.add_column("Cost", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Files", justify="right")

        worker_status_styles = {
            "pending": "dim",
            "running": "blue",
            "completed": "green",
            "failed": "red",
        }
        for w in run.workers.values():
            ws = worker_status_styles.get(w.status.value, "white")
            status_text = Text(w.status.value, style=ws)
            cost = f"${w.cost_usd:.2f}" if w.cost_usd is not None else "-"
            duration = f"{w.duration_ms / 1000:.1f}s" if w.duration_ms else "-"
            files = str(len(w.files_changed)) if w.files_changed else "-"
            table.add_row(w.worker_id, status_text, cost, duration, files)

        console.print(table)

    if run.total_cost_usd > 0:
        console.print(f"\n[bold]Total cost:[/bold] ${run.total_cost_usd:.2f}")
    console.print()


@cli.command()
@click.option("--repo", type=click.Path(exists=True, path_type=Path), default=None, help="Repository path (default: cwd)")
@click.option("--issue", "issue_number", type=int, required=True, help="GitHub issue number to process")
@click.option("--label", "trigger_label", type=str, default="swarm", help="Trigger label (default: swarm)")
@click.option("--workers", type=int, default=None, help="Override max workers")
@click.option("--model", type=str, default=None, help="Override worker model")
@click.option(
    "--oversight",
    type=click.Choice(["autonomous", "pr-gated", "checkpoint"], case_sensitive=False),
    default=None,
    help="Override oversight level",
)
@click.option("--max-cost", type=float, default=None, help="Override max total cost in USD")
@click.option("--max-worker-cost", type=float, default=None, help="Override max cost per worker in USD")
@click.option("--verbose", is_flag=True, help="Verbose output")
def process(
    repo: Path | None,
    issue_number: int,
    trigger_label: str,
    workers: int | None,
    model: str | None,
    oversight: str | None,
    max_cost: float | None,
    max_worker_cost: float | None,
    verbose: bool,
) -> None:
    """Process a single GitHub issue through the swarm pipeline."""
    import logging

    from claude_swarm.github import get_issue, get_repo_slug
    from claude_swarm.issue_processor import IssueProcessor, issue_config_to_swarm_config, parse_issue_config

    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    repo_path = repo or Path.cwd()

    async def _main() -> None:
        owner, repo_name = await get_repo_slug(repo_path)
        issue_data = await get_issue(owner, repo_name, issue_number, cwd=repo_path)
        issue_config = parse_issue_config(issue_data, owner, repo_name)

        # Apply CLI overrides
        if workers is not None:
            issue_config.max_workers = workers
        if model is not None:
            issue_config.model = model
        if oversight is not None:
            issue_config.oversight = oversight
        if max_cost is not None:
            issue_config.max_cost = max_cost
        if max_worker_cost is not None:
            issue_config.max_worker_cost = max_worker_cost

        processor = IssueProcessor(
            issue_config, repo_path, trigger_label=trigger_label,
        )
        await processor.process()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(130)


@cli.command()
@click.option("--repo", type=click.Path(exists=True, path_type=Path), default=None, help="Repository path (default: cwd)")
@click.option("--label", "trigger_label", type=str, default="swarm", help="Trigger label (default: swarm)")
@click.option("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
@click.option("--verbose", is_flag=True, help="Verbose output")
def watch(
    repo: Path | None,
    trigger_label: str,
    interval: int,
    verbose: bool,
) -> None:
    """Watch for GitHub issues and process them continuously."""
    import logging

    from claude_swarm.github import get_repo_slug
    from claude_swarm.issue_processor import IssueWatcher

    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    repo_path = repo or Path.cwd()

    async def _main() -> None:
        owner, repo_name = await get_repo_slug(repo_path)
        console.print(f"[bold blue]claude-swarm[/bold blue] watching {owner}/{repo_name}")
        console.print(f"[dim]Label:[/dim] {trigger_label}  [dim]Interval:[/dim] {interval}s\n")

        watcher = IssueWatcher(
            repo_path, owner, repo_name,
            trigger_label=trigger_label,
            interval=interval,
        )
        try:
            await watcher.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            watcher.stop()
            console.print("\n[yellow]Stopped watching.[/yellow]")

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(130)


@cli.command()
@click.option("--repo", type=click.Path(exists=True, path_type=Path), default=None, help="Repository path (default: cwd)")
@click.option("--run-id", type=str, default=None, help="Specific run to resume (default: last interrupted)")
def resume(repo: Path | None, run_id: str | None) -> None:
    """Resume an interrupted swarm run."""
    from claude_swarm.models import RunStatus, WorkerStatus
    from claude_swarm.state import StateManager

    repo_path = repo or Path.cwd()
    state_mgr = StateManager(repo_path)

    # Find the run to resume
    if run_id:
        run = state_mgr.get_run(run_id)
        if not run:
            console.print(f"[red]No run found with ID {run_id}[/red]")
            sys.exit(1)
    else:
        run = state_mgr.get_last_interrupted_run()
        if not run:
            console.print("[dim]No interrupted runs to resume.[/dim]")
            return

    if run.status not in (RunStatus.INTERRUPTED, RunStatus.FAILED, RunStatus.EXECUTING, RunStatus.PAUSED_CHECKPOINT):
        console.print(f"[yellow]Run {run.run_id} is {run.status.value}, cannot resume.[/yellow]")
        return

    if not run.plan:
        console.print("[yellow]Run was interrupted before planning completed. Please start a new run.[/yellow]")
        return

    # Determine which workers need to be re-executed
    resumable = [
        w for w in run.workers.values()
        if w.status in (WorkerStatus.PENDING, WorkerStatus.FAILED)
    ]

    if not resumable:
        console.print("[green]All workers completed. Nothing to resume.[/green]")
        return

    completed = [
        w for w in run.workers.values()
        if w.status == WorkerStatus.COMPLETED
    ]

    console.print(f"\n[bold blue]Resuming run[/bold blue] [dim]{run.run_id}[/dim]")
    console.print(f"[dim]Task:[/dim] {run.task}")
    console.print(f"[green]{len(completed)} worker(s) already completed[/green]")
    console.print(f"[yellow]{len(resumable)} worker(s) to resume[/yellow]")
    for w in resumable:
        console.print(f"  [dim]-[/dim] {w.worker_id}: {w.title}")
    console.print()

    # Rebuild config from snapshot
    config = SwarmConfig(
        task=run.task,
        repo_path=repo_path,
        max_workers=run.config_snapshot.get("max_workers", 4),
        model=run.config_snapshot.get("model", "sonnet"),
        orchestrator_model=run.config_snapshot.get("orchestrator_model", "opus"),
        max_cost=run.config_snapshot.get("max_cost", 50.0),
        max_worker_cost=run.config_snapshot.get("max_worker_cost", 5.0),
        max_worker_retries=run.config_snapshot.get("max_worker_retries", 1),
        escalation_model=run.config_snapshot.get("escalation_model", "opus"),
        enable_escalation=run.config_snapshot.get("enable_escalation", True),
        resolve_conflicts=run.config_snapshot.get("resolve_conflicts", True),
        oversight=run.config_snapshot.get("oversight", "pr-gated"),
        issue_number=run.config_snapshot.get("issue_number"),
    )
    config.run_id = run.run_id

    # Filter plan to only include tasks that need resuming
    from claude_swarm.models import TaskPlan
    resumable_ids = {w.worker_id for w in resumable}
    resume_tasks = [t for t in run.plan.tasks if t.worker_id in resumable_ids]
    resume_plan = TaskPlan(
        original_task=run.plan.original_task,
        reasoning=f"Resumed run — re-executing {len(resume_tasks)} worker(s)",
        tasks=resume_tasks,
        integration_notes=run.plan.integration_notes,
        test_command=run.plan.test_command,
        build_command=run.plan.build_command,
    )

    from claude_swarm.orchestrator import Orchestrator
    orchestrator = Orchestrator(config, run_id=run.run_id)

    # Re-activate the run
    state_mgr.set_run_status(run.run_id, RunStatus.EXECUTING)

    async def _main() -> None:
        try:
            # Execute only the resumable workers
            worker_results = await orchestrator._execute_workers(resume_plan)
            state_mgr.set_run_status(run.run_id, RunStatus.INTEGRATING)

            # Combine with previously completed workers for integration
            from claude_swarm.models import WorkerResult
            all_results = []
            for w in completed:
                all_results.append(WorkerResult(
                    worker_id=w.worker_id,
                    success=True,
                    cost_usd=w.cost_usd,
                    duration_ms=w.duration_ms,
                    summary=w.summary,
                    files_changed=w.files_changed,
                    model_used=w.model_used,
                ))
            all_results.extend(worker_results)

            successful = [r for r in all_results if r.success]
            if successful:
                from claude_swarm.integrator import integrate_results
                base_branch = run.base_branch
                integration_success, pr_url, error_msg = await integrate_results(
                    orchestrator.worktree_mgr,
                    all_results,
                    base_branch,
                    run_id=run.run_id,
                    test_command=run.plan.test_command,
                    build_command=run.plan.build_command,
                    should_create_pr=True,
                    review=False,
                    task_description=run.task,
                    orchestrator_model=config.orchestrator_model,
                    resolve_conflicts=config.resolve_conflicts,
                    issue_number=config.issue_number,
                )
                if integration_success:
                    state_mgr.complete_run(run.run_id, pr_url=pr_url)
                    if pr_url:
                        console.print(f"\n[green bold]PR created:[/green bold] {pr_url}")
                else:
                    state_mgr.fail_run(run.run_id, error_msg or "Integration failed")
                    console.print(f"\n[red]Integration failed:[/red] {error_msg}")
            else:
                state_mgr.fail_run(run.run_id, "No workers succeeded")
                console.print("[red]All workers failed.[/red]")

            orchestrator.session.write_metadata()

        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[yellow]Interrupted. Cleaning up...[/yellow]")
            try:
                state_mgr.set_run_status(run.run_id, RunStatus.INTERRUPTED)
            except Exception:
                pass
            try:
                await orchestrator.worktree_mgr.cleanup_all(force=True)
            except Exception:
                pass

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(130)

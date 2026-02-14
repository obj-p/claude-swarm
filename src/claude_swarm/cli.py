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
) -> None:
    """Run the full swarm pipeline: plan, execute, integrate, PR."""
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

# claude-swarm

Orchestration system for dynamic pools of Claude agents working autonomously on development tasks.

## What it does

You describe a task, and claude-swarm decomposes it into parallel subtasks, spawns independent Claude Code agents in isolated git worktrees, coordinates their work, merges the results, and optionally opens a PR.

```
Task  -->  Planner (Opus)  -->  Workers (Sonnet, parallel)  -->  Merge + Review  -->  PR
```

## Quick start

```bash
uv sync
uv run swarm run "Add user authentication" --repo . --workers 4
```

## Commands

```bash
uv run swarm run "task" --repo .                    # full pipeline
uv run swarm plan "task" --repo .                   # dry-run, plan only
uv run swarm run "task" --repo . --oversight autonomous    # auto-merge PR
uv run swarm run "task" --repo . --oversight checkpoint    # pause at decisions
uv run swarm process --issue 42 --repo .            # process a GitHub issue
uv run swarm watch --repo . --interval 30           # poll for labeled issues
uv run swarm status --repo .                        # show run state
uv run swarm resume --repo .                        # resume interrupted run
uv run swarm cleanup --repo .                       # remove worktrees + branches
```

## How it works

1. **Plan** -- An Opus agent discovers the repo and decomposes the task into parallel subtasks
2. **Execute** -- Each subtask runs in an isolated git worktree with its own Claude Code agent
3. **Coordinate** -- Workers share findings via notes, directed messages, and status updates
4. **Integrate** -- Branches are merged, conflicts resolved, and a semantic review checks for interface mismatches
5. **Deliver** -- A PR is created (or auto-merged in autonomous mode)

## Key features

- **Worktree isolation** -- each worker gets its own branch, no interference
- **Inter-worker coordination** -- shared notes, directed messaging, peer status tracking
- **Worker retry + model escalation** -- failed workers retry with error context, escalate Sonnet to Opus
- **Cost circuit breaker** -- per-worker and total cost limits prevent runaway spend
- **Security guards** -- blocks dangerous operations (force push, rm -rf, etc.)
- **Configurable oversight** -- autonomous, pr-gated, or checkpoint modes
- **GitHub Issues intake** -- `swarm process` and `swarm watch` for issue-driven workflows
- **Resumable runs** -- `swarm resume` picks up where interrupted runs left off

## Development

```bash
uv sync                     # install deps
uv run pytest -v            # run tests
uv run swarm --version      # verify install
```

## Architecture

See [VISION.md](VISION.md) for the full vision and roadmap, and [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for detailed design rationale.

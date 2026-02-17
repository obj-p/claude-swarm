# claude-swarm

Orchestration system for dynamic pools of Claude agents working autonomously on development tasks.

You describe a task, and claude-swarm decomposes it into parallel subtasks, spawns independent Claude Code agents in isolated git worktrees, coordinates their work, merges the results, and optionally opens a PR.

```
Task  -->  Planner (Opus)  -->  Workers (Sonnet, parallel)  -->  Merge + Review  -->  PR
```

## Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) (package manager)
- git
- [GitHub CLI](https://cli.github.com/) (`gh`) — authenticated (`gh auth login`)
- `ANTHROPIC_API_KEY` environment variable

## Quick Start

```bash
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
swarm run "Add user authentication" --repo .
```

> After `uv sync`, the `swarm` entry point is available directly. `uv run swarm ...` also works without a prior install step.

## How It Works

1. **Plan** — An Opus agent discovers the repo and decomposes the task into parallel subtasks
2. **Execute** — Each subtask runs in an isolated git worktree with its own Claude Code agent
3. **Coordinate** — Workers share findings via notes, directed messages, and status updates
4. **Integrate** — Branches are merged, conflicts resolved, and a semantic review checks for interface mismatches
5. **Deliver** — A PR is created (or auto-merged in autonomous mode)

Workers operate in `.swarm-worktrees/<run_id>/<worker_id>/`, each on its own branch (`swarm/<run_id>/<worker_id>`). Coordination happens through the filesystem at `.claude-swarm/coordination/<run_id>/` using three channels: shared notes (broadcast findings), directed messages (point-to-point), and peer status (self-reported progress milestones).

## Commands

### `swarm run`

Full pipeline — plan, execute workers, integrate, and deliver.

```bash
swarm run "task description" [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--repo PATH` | `.` | Repository path |
| `--workers INTEGER` | `4` | Max parallel workers |
| `--model TEXT` | `sonnet` | Worker model (`sonnet` or `opus`) |
| `--orchestrator-model TEXT` | `opus` | Orchestrator/planner model |
| `--max-cost FLOAT` | `50.0` | Max total cost in USD |
| `--max-worker-cost FLOAT` | `5.0` | Max cost per worker in USD |
| `--oversight TEXT` | `pr-gated` | Oversight level: `autonomous`, `pr-gated`, `checkpoint` |
| `--live / --no-live` | auto | Live dashboard (auto-detected from TTY) |
| `--pr / --no-pr` | `--pr` | Create PR after integration |
| `--retries INTEGER` | `1` | Max attempts per worker |
| `--review` | off | Run semantic review after merge |
| `--dry-run` | off | Plan only, don't execute |
| `--no-escalation` | off | Disable model escalation on retry |
| `--no-conflict-resolution` | off | Disable automated merge conflict resolution |
| `--verbose` | off | Verbose output |

### `swarm plan`

Dry-run — decompose task into subtasks without executing.

```bash
swarm plan "task description" [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--repo PATH` | `.` | Repository path |
| `--workers INTEGER` | `4` | Max parallel workers |
| `--model TEXT` | `sonnet` | Worker model |
| `--orchestrator-model TEXT` | `opus` | Orchestrator model |
| `--verbose` | off | Verbose output |

### `swarm process`

Process a single GitHub issue.

```bash
swarm process --issue 42 [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--issue INTEGER` | *(required)* | GitHub issue number |
| `--repo PATH` | `.` | Repository path |
| `--label TEXT` | `swarm` | Trigger label |
| `--workers INTEGER` | *(from labels/defaults)* | Override max workers |
| `--model TEXT` | *(from labels/defaults)* | Override worker model |
| `--oversight TEXT` | *(from labels/defaults)* | Override oversight level |
| `--max-cost FLOAT` | *(from labels/defaults)* | Override max total cost |
| `--max-worker-cost FLOAT` | *(from labels/defaults)* | Override max cost per worker |
| `--verbose` | off | Verbose output |

### `swarm watch`

Continuously poll for GitHub issues with the trigger label.

```bash
swarm watch [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--repo PATH` | `.` | Repository path |
| `--label TEXT` | `swarm` | Trigger label |
| `--interval INTEGER` | `30` | Poll interval in seconds |
| `--verbose` | off | Verbose output |

### `swarm status`

Show current or most recent run state.

```bash
swarm status [--repo PATH]
```

### `swarm resume`

Resume an interrupted run, re-executing only pending/failed workers.

```bash
swarm resume [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--repo PATH` | `.` | Repository path |
| `--run-id TEXT` | *(last interrupted)* | Specific run to resume |
| `--live / --no-live` | auto | Live dashboard |

### `swarm cleanup`

Remove all swarm worktrees and branches.

```bash
swarm cleanup [--repo PATH]
```

## Key Features

- **Worktree isolation** — each worker gets its own branch in `.swarm-worktrees/`, no interference between parallel agents
- **Inter-worker coordination** — shared notes, directed messaging, and peer status tracking via `.claude-swarm/coordination/`
- **Worker retry + model escalation** — failed workers retry with error context; escalates Sonnet to Opus on final attempt (disable with `--no-escalation`)
- **Cost tracking + circuit breaker** — per-worker (`$5` default) and total (`$50` default) cost limits; remaining workers are skipped when the budget is exceeded
- **Security guards** — blocks 20+ dangerous operation patterns including force push, `rm -rf`, `DROP TABLE`, privilege escalation, reverse shells, and more
- **Conflict resolution** — automated merge conflict resolution via a resolver agent; falls back to error on failure (disable with `--no-conflict-resolution`)
- **Resumable runs** — `swarm resume` picks up interrupted runs and re-executes only pending/failed workers using the saved plan

## Oversight Levels

The `--oversight` flag controls how much human intervention is required.

| Level | Behavior |
|---|---|
| **`autonomous`** | Auto-merges the PR if CI passes (requires `--pr`) |
| **`pr-gated`** (default) | Creates a PR for human review and merge |
| **`checkpoint`** | Pauses at 3 decision points — before executing workers, before integration, and before PR creation — for interactive approval |

## Live Dashboard

The `--live` / `--no-live` flag on `run` and `resume` enables a real-time terminal dashboard.

- Auto-detected from TTY — enabled in interactive terminals, disabled in CI/pipes and with `--dry-run`
- Shows worker status with peer milestones, cost, elapsed time, and recent events from `events.jsonl`
- Refreshes at 1 Hz

## GitHub Issues Intake

### One-shot processing

```bash
swarm process --issue 42 --repo .
```

### Continuous polling

```bash
swarm watch --interval 30 --repo .
```

### Label state machine

Issues transition through labels as they're processed:

```
swarm  -->  swarm:active  -->  swarm:done / swarm:failed
```

### Label-driven configuration

Add labels to issues to override defaults — CLI flags take precedence over label-derived config:

| Label | Effect |
|---|---|
| `oversight:autonomous` | Set oversight level |
| `model:opus` | Set worker model |
| `workers:6` | Set max workers |
| `cost:100` | Set max total cost |
| `worker-cost:5` | Set max cost per worker |

## Development

```bash
uv sync                     # install deps
uv run pytest -v            # run tests
uv run swarm --version      # verify install
```

See [CLAUDE.md](CLAUDE.md) for the module map and implementation details.

## Architecture

See [VISION.md](VISION.md) for the full vision and roadmap, and [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for detailed design rationale.

## License

MIT

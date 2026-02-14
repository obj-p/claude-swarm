# claude-swarm

Orchestration system for dynamic pools of Claude agents working autonomously on development tasks.

## Module Map

```
src/claude_swarm/
  cli.py          — Click CLI: run, plan, cleanup commands
  config.py       — SwarmConfig dataclass (from CLI args)
  orchestrator.py — Pipeline: plan → execute → integrate
  worker.py       — Spawns Claude Code agents in worktrees
  worktree.py     — Git worktree lifecycle (create/remove/cleanup)
  integrator.py   — Merges branches, runs tests, creates PRs via gh
  session.py      — JSONL event logging + cost tracking
  models.py       — Pydantic models: TaskPlan, WorkerTask, WorkerResult, SwarmResult
  prompts.py      — System prompts for planner, worker, reviewer agents
  util.py         — run_agent() helper (consumes SDK async stream)
  errors.py       — Error hierarchy (SwarmError base)
```

## Commands

```bash
uv run swarm run "task" --repo . --workers 4 --model sonnet --no-pr
uv run swarm plan "task" --repo .          # dry-run, plan only
uv run swarm cleanup --repo .             # remove all worktrees + branches
```

## Key Patterns

- **Async throughout** — orchestrator, workers, worktree ops all use asyncio
- **Structured output** — planner uses `output_format` with `TaskPlan.model_json_schema()`
- **Worktree isolation** — each worker gets `.swarm-worktrees/<run_id>/<worker_id>`
- **Branch naming** — `swarm/<run_id>/<worker_id>` (integration branch: `swarm/<run_id>/integration`)
- **Git lock retry** — `_run_git()` retries on lock contention with backoff
- **Session recording** — events → `.claude-swarm/logs/<run_id>/events.jsonl`, summary → `metadata.json`
- **Cost tracking** — per-worker and total, accumulated in SessionRecorder
- **SDK usage** — `claude_agent_sdk.query()` returns `AsyncIterator[Message]`; `run_agent()` consumes stream, returns `ResultMessage`

## Development

```bash
uv sync                     # install deps
uv run pytest -v            # run tests
uv run swarm --version      # verify install
```

## Things to Avoid

- Don't import from `claude_agent_sdk` at module level in tests — mock it
- Don't use `git init` without `-b main` in test fixtures (non-deterministic default branch)
- Don't call `_run_git` without `check=False` when failure is expected
- Don't forget `asyncio_mode = "auto"` — all worktree tests are async
- Don't modify `_run_id` on SwarmConfig without using the setter
- Prompts use `{{` / `}}` for literal braces in `.format()` — don't break this

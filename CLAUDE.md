# claude-swarm

Orchestration system for dynamic pools of Claude agents working autonomously on development tasks.

## Module Map

```
src/claude_swarm/
  cli.py          — Click CLI: run, plan, cleanup, status, resume commands
  config.py       — SwarmConfig dataclass (from CLI args)
  orchestrator.py — Pipeline: plan → execute → integrate
  worker.py       — Spawns Claude Code agents in worktrees
  worktree.py     — Git worktree lifecycle (create/remove/cleanup)
  integrator.py   — Merges branches, runs tests, creates PRs via gh
  session.py      — JSONL event logging + cost tracking
  state.py        — Persistent state management (StateManager + Pydantic state models)
  models.py       — Pydantic models: TaskPlan, WorkerTask, WorkerResult, SwarmResult, RunStatus, WorkerStatus
  prompts.py      — System prompts for planner, worker, reviewer agents
  util.py         — run_agent() helper (consumes SDK async stream)
  errors.py       — Error hierarchy (SwarmError base)
```

## Commands

```bash
uv run swarm run "task" --repo . --workers 4 --model sonnet --no-pr
uv run swarm plan "task" --repo .          # dry-run, plan only
uv run swarm cleanup --repo .             # remove all worktrees + branches
uv run swarm status --repo .              # show current/recent run state
uv run swarm resume --repo .              # resume last interrupted run
```

## Key Patterns

- **Async throughout** — orchestrator, workers, worktree ops all use asyncio
- **Structured output** — planner uses `output_format` with `TaskPlan.model_json_schema()`
- **Worktree isolation** — each worker gets `.swarm-worktrees/<run_id>/<worker_id>`
- **Branch naming** — `swarm/<run_id>/<worker_id>` (integration branch: `swarm/<run_id>/integration`)
- **Git lock retry** — `_run_git()` retries on lock contention with backoff
- **Session recording** — events → `.claude-swarm/logs/<run_id>/events.jsonl`, summary → `metadata.json`
- **Cost tracking** — per-worker and total, accumulated in SessionRecorder
- **Worker retry** — `spawn_worker_with_retry()` retries failed workers with error context; escalates model (Sonnet → Opus) on final attempt
- **Conflict resolution** — on merge conflict, spawns a resolver agent to fix conflict markers before falling back to `MergeConflictError`
- **SDK usage** — `claude_agent_sdk.query()` returns `AsyncIterator[Message]`; `run_agent()` consumes stream, returns `ResultMessage`
- **State persistence** — `StateManager` writes `.claude-swarm/state.json` (atomic via `os.replace`); tracks run/worker lifecycle for `status` and `resume` commands
- **State file** — `.claude-swarm/state.json` (gitignored); contains `SwarmState` with `active_run` pointer and per-run `RunState`/`WorkerState`
- **Resumption** — `swarm resume` detects incomplete workers via `WorkerStatus`, re-executes only pending/failed workers using the saved plan

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

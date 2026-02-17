# claude-swarm

Orchestration system for dynamic pools of Claude agents working autonomously on development tasks.

## Module Map

```
src/claude_swarm/
  cli.py              — Click CLI: run, plan, cleanup, status, resume, process, watch commands
  config.py           — SwarmConfig dataclass (from CLI args)
  orchestrator.py     — Pipeline: plan → execute → integrate
  worker.py           — Spawns Claude Code agents in worktrees
  worktree.py         — Git worktree lifecycle (create/remove/cleanup)
  integrator.py       — Merges branches, runs tests, creates PRs via gh
  github.py           — GitHub API wrapper (gh CLI): issues, labels, comments
  issue_processor.py  — IssueProcessor (single issue) + IssueWatcher (poll loop)
  session.py          — JSONL event logging + cost tracking
  state.py            — Persistent state management (StateManager + Pydantic state models)
  models.py           — Pydantic models: TaskPlan, WorkerTask, WorkerResult, SwarmResult, IssueConfig, RunStatus, WorkerStatus, OversightLevel
  coordination.py     — Inter-worker coordination: CoordinationManager + SharedNote, Message, WorkerPeerStatus models (notes, messaging, status)
  dashboard.py        — Real-time terminal dashboard (SwarmDashboard renderable, Rich Live integration)
  notes.py            — Backward-compat shim re-exporting NoteManager (alias for CoordinationManager) and SharedNote from coordination.py
  prompts.py          — System prompts for planner, worker, reviewer agents
  util.py             — run_agent() helper (consumes SDK async stream)
  errors.py           — Error hierarchy (SwarmError, GitHubError base)
```

## Commands

```bash
uv run swarm run "task" --repo . --workers 4 --model sonnet --no-pr
uv run swarm run "task" --repo . --oversight autonomous   # auto-merge PR if CI passes
uv run swarm run "task" --repo . --oversight checkpoint   # pause at key decisions
uv run swarm plan "task" --repo .          # dry-run, plan only
uv run swarm process --issue 42 --repo .  # process a single GitHub issue
uv run swarm watch --repo . --interval 30 # poll for issues labeled 'swarm'
uv run swarm run "task" --repo . --live      # run with live dashboard (default when TTY)
uv run swarm run "task" --repo . --no-live   # disable live dashboard
uv run swarm cleanup --repo .             # remove all worktrees + branches
uv run swarm status --repo .              # show current/recent run state
uv run swarm resume --repo .              # resume last interrupted run
uv run swarm resume --repo . --live       # resume with live dashboard
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
- **Coordination** — workers coordinate via `.claude-swarm/coordination/<run_id>/` with three channels: notes (`notes/<worker_id>.json`), directed messages (`messages/<worker_id>/NNN-from-<sender>.json`), and peer status (`status/<worker_id>.json`); CoordinationManager validates on read with graceful fallback; `notes.py` re-exports `CoordinationManager` as `NoteManager` for backward compat
- **Conflict resolution** — on merge conflict, spawns a resolver agent to fix conflict markers before falling back to `MergeConflictError`
- **SDK usage** — `claude_agent_sdk.query()` returns `AsyncIterator[Message]`; `run_agent()` consumes stream, returns `ResultMessage`
- **State persistence** — `StateManager` writes `.claude-swarm/state.json` (atomic via `os.replace`); tracks run/worker lifecycle for `status` and `resume` commands
- **State file** — `.claude-swarm/state.json` (gitignored); contains `SwarmState` with `active_run` pointer and per-run `RunState`/`WorkerState`
- **Resumption** — `swarm resume` detects incomplete workers via `WorkerStatus`, re-executes only pending/failed workers using the saved plan
- **Oversight levels** — `--oversight autonomous|pr-gated|checkpoint`; autonomous auto-merges via `gh pr merge --auto --squash`; checkpoint pauses at 3 decision points (execute workers, integrate, create PR) with terminal confirmation; pr-gated (default) creates PR for human merge
- **Live dashboard** — `SwarmDashboard` implements `__rich__()` protocol; polls `state.json` + coordination status + events.jsonl; integrated into `_execute_workers()` via Rich `Live` with asyncio refresh task at 1Hz; `--live` auto-detected from TTY
- **GitHub Issues intake** — `swarm process --issue N` (one-shot) and `swarm watch` (poll loop); label state machine: `swarm` → `swarm:active` → `swarm:done`/`swarm:failed`; `issue_number` threads through to PR body as `Closes #N`

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

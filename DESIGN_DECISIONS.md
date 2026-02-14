# Design Decisions

Deep dives into each open design question from the vision document. Each section presents the problem, evaluates options, and documents the chosen approach.

---

## 1. Coordination Depth

**Question**: When should the orchestrator use Agent Teams (peer messaging) vs. simple subagent delegation (fire-and-forget)? Should workers be able to spawn sub-workers?

### Options Considered

**A. Subagent Delegation (Default)**
- Orchestrator spawns workers, each gets a task, they execute and return results
- No inter-worker communication -- workers are unaware of each other
- Simpler, fewer tokens, more predictable, easier to debug
- Works well when tasks are truly independent

**B. Agent Teams (Peer Messaging)**
- Workers can message each other directly + shared task list
- Essential when one worker's discoveries affect another's work
- Example: Backend agent discovers the API schema needs to change, frontend agent needs to know immediately rather than building against the old schema
- Higher token cost, more complex coordination, harder to debug

**C. Nested Subagents (Workers spawning sub-workers)**
- Currently blocked by Claude Code (subagents cannot spawn subagents)
- Would enable hierarchical decomposition (orchestrator -> team leads -> workers)
- Risk: coordination tax grows exponentially with depth

### Decision: Adaptive Selection

The orchestrator chooses the coordination mode during task decomposition based on **coupling analysis**:

```
Task Decomposition
       |
       v
  Coupling Analysis
       |
       +-- Independent tasks --> Subagent Delegation
       |   (no shared files,      (fire-and-forget)
       |    no shared state)
       |
       +-- Loosely coupled -----> Subagent Delegation + Shared Notes
       |   (read same files,      (workers write to shared artifacts
       |    write different)       directory that others can read)
       |
       +-- Tightly coupled -----> Agent Teams
           (shared interfaces,    (full peer messaging +
            API contracts,         shared task list)
            coordinated state)
```

**No nested subagents.** The orchestrator is the sole spawner. This keeps the coordination graph flat and predictable. If a task is complex enough to need hierarchy, the orchestrator breaks it into more granular subtasks rather than delegating decomposition to workers.

**Shared notes pattern** for loose coupling: Workers write intermediate findings to a shared `.claude-swarm/notes/` directory in the repo. Other workers can read these files for context without direct messaging overhead. This is a lightweight coordination channel that costs zero extra tokens.

---

## 2. Conflict Resolution

**Question**: When two workers edit overlapping areas, how does the orchestrator resolve conflicts? Prevent overlap proactively or handle it reactively?

### Options Considered

**A. Strict Proactive Boundaries**
- Orchestrator assigns non-overlapping file sets to each worker
- Workers are explicitly told: "You may only modify files X, Y, Z"
- Prevents conflicts entirely but is rigid -- features naturally cross file boundaries

**B. Reactive Merge Resolution**
- Let workers overlap freely, handle conflicts via git merge
- More flexible and faster, keeps full parallelism
- Risk: AI-generated merge conflicts can introduce subtle bugs

**C. Hybrid: Proactive Partitioning + Sequential Fallback**
- Best-effort non-overlap, sequential execution when files must be shared
- Safest but slower -- sacrifices parallelism for safety

### Decision: Overlap + Merge (Option B)

**Speed-first.** Workers execute in full parallel without file boundary restrictions. When they produce overlapping changes, the orchestrator resolves conflicts via merge.

```
All Workers Execute in Parallel
       |
       v
  Workers Complete
       |
       v
  Orchestrator Collects Branches
       |
       v
  Merge Attempt
       |
       +-- Clean merge ---------> Done, open PR
       |
       +-- Git conflict --------> Spawn Integration Agent
       |                           - Receives both diffs + conflict markers
       |                           - Resolves conflicts with full context
       |                           - Runs tests to verify resolution
       |
       +-- Semantic conflict ----> Spawn Integration Agent
           (detected during         - Reviews all changes holistically
            integration review)     - Fixes interface mismatches
                                    - Ensures components wire together
```

**Why this over proactive partitioning:**
- Features naturally cross file boundaries -- restricting file access makes workers less effective
- Full parallelism is maintained (no sequential bottlenecks)
- Git merge handles most overlaps cleanly without intervention
- When conflicts do arise, an integration agent with full context resolves them better than pre-planned boundaries

**The integration step** (runs after all workers complete):
1. Orchestrator attempts to merge all worker branches
2. If git conflicts exist: spawns an integration agent with both diffs and conflict context
3. Even if merge is clean: orchestrator does a semantic review checking for:
   - Interface mismatches (worker A exports something differently than worker B expects)
   - Incompatible assumptions across workers
   - Missing connections (worker A built the API, worker B built the UI, but nobody wired them together)
4. If semantic issues found: integration agent fixes them
5. Runs the full test suite on the merged result

**Risk mitigation:**
- The integration review step catches semantic conflicts that git merge can't detect
- Test suite validation on the merged result provides a safety net
- If merge resolution fails after 2 attempts, escalate to human

---

## 3. State Persistence

**Question**: How do long-running tasks survive across session boundaries? Should the swarm maintain a persistent task database or rely on git/GitHub as the source of truth?

### Options Considered

**A. Git + GitHub as Source of Truth**
- Tasks -> GitHub Issues (with labels for status)
- Work-in-progress -> Git branches (one per worker)
- Results -> Pull Requests
- No custom infrastructure needed

**B. Local State File**
- `.claude-swarm/state.json` in the repo
- Fast but doesn't survive repo clones

**C. External Database**
- Most flexible but adds infrastructure dependency
- Overkill for personal tooling

### Decision: GitHub-Native State + Local Cache

Primary state lives in GitHub. Local cache accelerates reads.

```
+---------------------------------------------+
|  GitHub (Durable State)                      |
|                                              |
|  Issues --> Task definitions + status        |
|  Labels --> Task state (swarm:active, etc.)  |
|  Branches > Worker progress (code changes)   |
|  PRs -----> Completed work ready for review  |
|  Comments > Agent progress updates + logs    |
+---------------------------------------------+
                    ^
                    | sync
                    v
+---------------------------------------------+
|  Local Cache (.claude-swarm/state.json)      |
|                                              |
|  - Active task assignments                   |
|  - Worker PIDs and worktree paths            |
|  - Session IDs for resume capability         |
|  - Token spend counters                      |
|  - Timestamps for progress tracking          |
+---------------------------------------------+
```

**Resumption workflow:**
1. Swarm session crashes or is interrupted
2. On restart, orchestrator reads GitHub Issues to find active tasks (labeled `swarm:active`)
3. Checks which git worktrees still exist and which branches have uncommitted work
4. Checks local cache for session IDs to attempt Claude Code `--resume`
5. For any worker that can't be resumed: creates a new worker with context from the branch state + issue comments

**Why GitHub-native?**
- You already use GitHub -- no new tools to learn
- Issues/PRs are human-readable -- you can inspect swarm state in the GitHub UI
- Survives machine failures (state is remote)
- Other humans (or future agents) can participate through normal GitHub workflows
- GitHub's API is accessible via the MCP server that's already available

**Issue structure for a swarm task:**
```markdown
Title: [swarm] Implement user authentication
Labels: swarm:active, swarm:feature, oversight:pr-gated

## Task Description
Add JWT-based authentication to the API...

## Decomposition (auto-generated by orchestrator)
- [ ] #42 Add auth middleware (worker-1, branch: swarm/auth-middleware)
- [ ] #43 Add login/register endpoints (worker-2, branch: swarm/auth-endpoints)
- [ ] #44 Add auth tests (worker-3, branch: swarm/auth-tests)
- [ ] Integration pass (pending workers 1-3)

## Progress Log
- 14:23 Orchestrator: Discovered Express.js API, 12 routes, no existing auth
- 14:24 Worker-1: Starting auth middleware implementation
- 14:25 Worker-2: Starting endpoint implementation
- 14:31 Worker-1: Completed. PR #45 opened.
- ...
```

---

## 4. Observability

**Question**: What does monitoring look like? How do you inspect agents in real-time? How do you measure cost?

### Decision: Three Observation Channels

#### Channel 1: Terminal (Real-Time)
For active monitoring while the swarm runs.

```
+----------------------------------------------------------+
| claude-swarm: Implement user authentication               |
| Status: RUNNING | Workers: 3/3 active | Tokens: 142.3k   |
+----------------------------------------------------------+
|                                                          |
|  +- Worker 1 (auth-middleware) ----------------------+   |
|  | Status: Writing code                              |   |
|  | Files: src/middleware/auth.ts, src/types/auth.ts   |   |
|  | Tokens: 48.2k | Elapsed: 2m 14s                   |   |
|  +---------------------------------------------------+   |
|                                                          |
|  +- Worker 2 (auth-endpoints) -----------------------+   |
|  | Status: Running tests                             |   |
|  | Files: src/routes/auth.ts, src/controllers/auth.ts|   |
|  | Tokens: 52.1k | Elapsed: 2m 31s                   |   |
|  +---------------------------------------------------+   |
|                                                          |
|  +- Worker 3 (auth-tests) ---------------------------+   |
|  | Status: Waiting (blocked by workers 1,2)          |   |
|  | Tokens: 0 | Elapsed: --                           |   |
|  +---------------------------------------------------+   |
|                                                          |
|  [q]uit  [p]ause  [d]etail <worker#>  [l]ogs            |
+----------------------------------------------------------+
| Log: Worker-2 running `npm test` ... 14 passed, 0 failed |
+----------------------------------------------------------+
```

Implementation: tmux session managed by the orchestrator. Each worker runs in a pane. The orchestrator renders a status header. This builds on Agent Teams' existing tmux integration.

#### Channel 2: GitHub (Async / Historical)
For reviewing progress after the fact or from a different machine.

- Issue comments for progress milestones (not every tool call -- that would be noisy)
- PR descriptions summarize what each worker did
- Labels track aggregate status
- Check runs could report swarm health alongside CI

#### Channel 3: Local Logs (Debug / Audit)
For deep debugging when something goes wrong.

```
.claude-swarm/
  logs/
    session-2026-02-14-1423/
      orchestrator.jsonl     # All orchestrator decisions and reasoning
      worker-1.jsonl         # Full tool call log for worker 1
      worker-2.jsonl         # Full tool call log for worker 2
      worker-3.jsonl         # Full tool call log for worker 3
      cost-report.json       # Token usage breakdown
```

Each log entry is structured JSON:
```json
{
  "timestamp": "2026-02-14T14:24:03Z",
  "agent": "worker-1",
  "event": "tool_call",
  "tool": "Edit",
  "file": "src/middleware/auth.ts",
  "tokens_used": 1243,
  "duration_ms": 892
}
```

#### Cost Tracking

Token spend tracked at three levels:
1. **Per-worker**: How many tokens each worker consumed
2. **Per-task**: Total tokens for the entire swarm task (all workers + orchestrator)
3. **Per-session**: Running total across all tasks in a session

The cost report surfaces:
- Total tokens (input + output, by model)
- Estimated dollar cost (based on current API pricing)
- Token efficiency: tokens per file changed, tokens per line of code
- Coordination overhead: what percentage of tokens went to orchestration vs. actual work

---

## 5. Error Recovery

**Question**: When a worker fails or produces bad output, what happens?

### Failure Categories

| Category | Example | Severity |
|----------|---------|----------|
| **Transient** | API rate limit, network timeout | Low - retry fixes it |
| **Task failure** | Tests don't pass, build breaks | Medium - needs diagnosis |
| **Bad output** | Code works but is wrong approach | Medium - needs human judgment |
| **Agent confusion** | Worker goes off-task, loops | Medium - needs fresh start |
| **Destructive** | Corrupted worktree, deleted files | High - needs cleanup |
| **Systemic** | All workers hitting same issue | High - needs human escalation |

### Decision: Tiered Recovery Strategy

```
Worker Failure
       |
       v
  Classify Failure
       |
       +-- Transient -----------> Auto-retry (max 3x with backoff)
       |   (rate limit,
       |    network error)
       |
       +-- Task Failure --------> Retry with Error Context
       |   (tests fail,           - Feed error output back to worker
       |    build breaks)          - "Tests failed with: <output>. Fix the issue."
       |                           - Max 2 retries, then escalate model
       |
       +-- Model Escalation ----> Retry with Opus
       |   (Sonnet failed 2x)     - Same task, stronger model
       |                           - If Opus also fails, escalate to human
       |
       +-- Agent Confusion -----> Fresh Start
       |   (off-task,              - Kill the worker
       |    infinite loop)          - Spawn new worker with same task
       |                           - Add explicit constraints to prevent repeat
       |
       +-- Destructive ---------> Cleanup + Fresh Start
       |   (corrupted state)       - Delete worktree
       |                           - Create new worktree from clean branch
       |                           - Spawn new worker
       |
       +-- Systemic ------------> Escalate to Human
           (multiple workers       - Pause all workers
            hitting same issue)    - Post summary to GitHub issue
                                   - Notify human (terminal + optional webhook)
```

**Detection mechanisms:**
- **Transient**: Caught by Claude Code's built-in retry logic + orchestrator monitoring API responses
- **Task failure**: Worker reports test/build failure in its output
- **Bad output**: Integration step catches semantic issues; optional evaluator agent reviews
- **Agent confusion**: Timeout detection (worker running too long without progress), repeated identical tool calls
- **Destructive**: Git status check after worker completes (unexpected file deletions, corrupted files)
- **Systemic**: Pattern detection -- if 2+ workers fail with similar errors within a short window

**Timeout policy:**
- Workers get a configurable time budget (default: 10 minutes for standard tasks)
- Orchestrator checks progress at intervals
- If no meaningful progress (no new file edits, no test runs) for 3 minutes, the worker is considered confused
- Grace period can be extended for tasks known to require long build/test cycles

**Cost circuit breaker (enabled by default, generous limits):**
- Per-worker token budget: 500k tokens (configurable)
- Per-task total budget: 2M tokens (configurable)
- Per-session budget: 10M tokens (configurable)
- When any budget is hit: worker is paused, orchestrator notified, human alerted
- Prevents runaway loops from burning unlimited tokens
- Budget scales with task complexity (the orchestrator sets it during assignment)

---

## 6. Model Selection

**Question**: Should workers always use the same model, or should the orchestrator pick models based on task complexity?

### Decision: Sonnet Floor with Opus Escalation

Given the speed-first priority, the default is **capable models that minimize rework**, not cheap models that might fail and need retries.

```
Task Complexity Assessment
       |
       v
  +-----------------------------------------------------+
  |  Orchestrator (always Opus)                          |
  |  Needs the best reasoning for:                       |
  |  - Task decomposition                                |
  |  - Coupling analysis                                  |
  |  - Integration review                                |
  |  - Error recovery decisions                          |
  +-----------------------------------------------------+
       |
       | assigns model per worker
       |
       +-- Complex tasks -------> Opus
       |   - Architectural changes
       |   - Cross-service refactoring
       |   - Security-critical code
       |   - Novel algorithms / complex logic
       |   - Tasks that failed with Sonnet
       |
       +-- Standard tasks ------> Sonnet (default)
       |   - Feature implementation
       |   - Bug fixes with known root cause
       |   - Writing tests
       |   - Code review
       |   - API endpoint implementation
       |
       +-- Simple tasks --------> Sonnet (not Haiku)
           - Config changes        Speed-first means we'd
           - Dependency updates    rather overshoot on
           - Formatting fixes      capability than risk
           - Simple renames        rework from a weaker model
```

**Why Sonnet as floor (not Haiku)?**
- Speed-first philosophy: rework from a less capable model costs more time than using a slightly more expensive model
- Haiku may struggle with codebase comprehension on complex projects
- The time cost of a failed attempt + retry exceeds the token savings
- Can revisit this in Phase 4 (optimization) once we have data on task success rates

**Escalation path:**
- If a Sonnet worker fails twice on the same task, the orchestrator retries with Opus
- This gives us automatic model escalation without manual intervention

**Override:**
- Users can force a specific model via CLI flag: `claude-swarm --model opus "implement auth"`
- Per-task override in the task spec: `model: opus` in the issue template

---

## 7. Security Boundaries

**Question**: How do we prevent destructive actions? How do we handle secrets?

### Threat Model

| Threat | Risk | Mitigation Layer |
|--------|------|-------------------|
| Force push to main | High - overwrites history | Git hooks + Claude Code hooks |
| Delete important files | High - data loss | Worktree isolation + PreToolUse hooks |
| Expose secrets in code | High - credential leak | PreToolUse hooks + git-secrets |
| Drop database tables | High - data destruction | MCP server permissions |
| Infinite token spend | Medium - cost explosion | Cost circuit breaker |
| Commit to wrong branch | Medium - git pollution | Worktree isolation |
| Install malicious packages | Medium - supply chain | PreToolUse hooks + allowlists |
| Agent prompt injection via repo content | Low-Medium - misdirection | Sandboxed execution, review step |

### Decision: Defense in Depth

#### Layer 1: Worktree Isolation (Structural)
- Workers NEVER operate on the main branch directly
- Each worker gets a dedicated worktree with its own branch
- The main branch is only modified through merged PRs
- Even the orchestrator doesn't push to main

#### Layer 2: Claude Code Hooks (Deterministic)
PreToolUse hooks that block dangerous operations before they execute:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "claude-swarm-guard \"$TOOL_INPUT\"",
            "blocking": true
          }
        ]
      }
    ]
  }
}
```

The `claude-swarm-guard` script blocks:
- `git push --force`, `git push -f`
- `git checkout main`, `git switch main` (workers stay on their branch)
- `rm -rf` on directories outside the worktree
- `DROP TABLE`, `DELETE FROM` without WHERE clause
- `curl | sh`, `wget | bash` (arbitrary script execution)
- Package installs not on an allowlist (configurable)

#### Layer 3: MCP Server Permissions (Principle of Least Privilege)
- Workers only get the MCP servers they need for their specific task
- GitHub MCP: read-only by default, write access only for PR creation
- Database MCP: read-only for investigation tasks, write access only when explicitly needed
- No MCP server gets admin/destructive permissions

#### Layer 4: Cost Circuit Breaker (Financial)
- Per-worker token budget (configurable, default 500k tokens)
- Per-task total budget (configurable, default 2M tokens)
- Per-session budget (configurable, default 10M tokens)
- When any budget is hit: worker is paused, orchestrator notified, human alerted

#### Layer 5: Secrets Management
```
Secrets NEVER enter worker context directly.

Instead:
+--------------+     +--------------+     +--------------+
|   Worker      |---->|  MCP Server   |---->|  External     |
|  (no secrets) |     |  (has creds)  |     |  Service      |
+--------------+     +--------------+     +--------------+

Workers call tools ("create PR", "query database")
MCP servers handle authentication internally
Workers never see API keys, tokens, or passwords
```

- Environment variables with secrets are NOT passed to worker subprocesses
- `.env` files are in `.gitignore` and excluded from worker file access via hooks
- If a worker somehow writes a secret to a file, a PostToolUse hook runs `git-secrets --scan` and blocks the commit

#### Layer 6: Review Gate (Configurable per-task)
- All changes go through PRs
- PRs trigger CI/CD (tests, linting, security scans)
- **Merge rights are configurable per-task:**
  - `oversight: autonomous` -- auto-merge if CI passes (for low-risk tasks like tests, formatting, deps)
  - `oversight: pr-gated` -- PR created but human must merge (default)
  - `oversight: checkpoint` -- orchestrator pauses at key decisions and posts "@user: approval needed for X"
- Auto-merge criteria (when enabled): all CI checks pass, no security scan warnings, changes are within expected scope

### Escape Hatches
Sometimes you legitimately need to do something the guard blocks. Options:
- `--trust` flag on the CLI to disable guards for a specific task (logged with warning)
- Per-command allowlist in `.claude-swarm/security.json`
- Guards are configurable, not hardcoded -- you own the policy

---

## Summary of Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Coordination | Adaptive: subagent delegation by default, Agent Teams for coupled tasks | Minimize coordination tax; escalate only when needed |
| Conflict Resolution | **Overlap + merge**: full parallel execution, integration agent resolves conflicts after | Speed-first: maximize parallelism, handle conflicts reactively |
| State Persistence | GitHub-native (issues + branches + PRs) with local cache | No new infrastructure; human-readable; survives crashes |
| Observability | Three channels: terminal (real-time), GitHub (async), local logs (debug) | Different needs at different times |
| Error Recovery | Tiered: auto-retry -> error context -> model escalation -> human | Exhaust automated options before bothering the human |
| Model Selection | Opus orchestrator, Sonnet default workers, Opus for complex/failed tasks | Speed-first: overshoot capability to minimize rework |
| Security | Defense in depth: worktree isolation + hooks + MCP permissions + cost limits + secrets management | Multiple independent layers; no single point of failure |
| Merge Rights | **Configurable per-task**: auto-merge for low-risk, human-merge for default, checkpoints for high-risk | Matches configurable oversight philosophy |
| Cost Limits | **Enabled with generous defaults**: 500k/worker, 2M/task, 10M/session | Prevents runaways without being restrictive |

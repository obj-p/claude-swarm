# claude-swarm

> An orchestration system for dynamic pools of Claude agents working autonomously on development tasks.

## The Problem

Modern development tasks -- feature implementation, bug investigation, code review, testing -- often involve cross-cutting work across multiple files, services, and layers. A single Claude Code session can handle these, but complex tasks benefit from parallelism: multiple agents working simultaneously on independent subtasks, each with its own context window and focus area.

Today's options for multi-agent Claude workflows are either:
- **Too manual**: Spinning up multiple Claude Code sessions by hand and coordinating yourself
- **Too rigid**: Pre-configured agent roles that don't adapt to different codebases
- **Too experimental**: Agent Teams is promising but still has rough edges around session management and coordination

## The Vision

**claude-swarm** is an orchestration layer that manages dynamic pools of Claude Code agents. You describe what you want done, and the swarm figures out how to decompose the work, spin up the right number of agents, coordinate their efforts, and deliver results.

### Core Principles

1. **Adaptive, not pre-configured.** Agents discover repo structure, tooling, and conventions at runtime. Drop the swarm into any codebase and it figures out how to partition work. No hardcoded assumptions about monorepos, languages, or frameworks.

2. **Speed-first parallelism.** Throw agents at the problem. Parallelize aggressively. The default is maximum throughput -- spin up as many agents as the task can support.

3. **Dynamic/elastic scaling.** Agent pool size scales based on workload. A simple typo fix gets one agent. A cross-layer feature gets a full team. The orchestrator decides.

4. **Configurable human oversight.** Different tasks need different levels of control:
   - **Fully autonomous**: Agents run end-to-end, you review the PR
   - **PR-gated**: Agents work freely but all changes go through PRs before merge
   - **Checkpoint-based**: Agents pause at key decision points for approval
   - The oversight level is configurable per-task

5. **Hybrid coordination.** No single coordination pattern fits all tasks. The swarm uses:
   - **Orchestrator-worker** for task decomposition and delegation
   - **Peer collaboration** (Agent Teams) when agents need to share findings
   - **GitHub-native coordination** (branches, PRs, issues) for human-visible state

6. **Extensible intake.** Tasks enter the system through multiple channels:
   - CLI (talk to the orchestrator directly)
   - GitHub Issues (agents watch for labeled issues)
   - Webhooks, scheduled jobs, CI triggers (future)

## Architecture

```
                    ┌─────────────────────┐
                    │    Task Intake       │
                    │  CLI / Issues / API  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │    Orchestrator      │
                    │   (Claude Opus)      │
                    │                      │
                    │  - Discovers repo    │
                    │  - Decomposes tasks  │
                    │  - Assigns agents    │
                    │  - Monitors progress │
                    │  - Synthesizes work  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                 │
   ┌──────────▼──────┐ ┌──────▼───────┐ ┌──────▼───────┐
   │  Worker Agent 1  │ │ Worker Agent 2│ │ Worker Agent N│
   │  (Claude Code)   │ │ (Claude Code) │ │ (Claude Code) │
   │                  │ │               │ │               │
   │  Git Worktree A  │ │ Git Worktree B│ │ Git Worktree N│
   │  Branch: feat-1a │ │ Branch: feat-1b│ │ Branch: feat-1n│
   └──────────────────┘ └───────────────┘ └───────────────┘
              │                │                 │
              └────────────────┼─────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Merge & Review     │
                    │   Pull Requests      │
                    └─────────────────────┘
```

### Components

#### 1. Orchestrator Agent
The brain of the swarm. Receives a task, inspects the codebase, and decides:
- How many workers to spawn
- What each worker should focus on (file boundaries, service boundaries, layer boundaries)
- What coordination pattern to use
- What oversight level applies
- When to scale up or down

Runs as a Claude Code session (Opus model for complex reasoning). Uses the Agent SDK or Agent Teams to spawn and manage workers.

#### 2. Worker Agents
Individual Claude Code instances, each operating in an isolated git worktree with its own branch. Workers:
- Receive a focused task with clear boundaries (objective, scope, output format)
- Execute autonomously within their worktree
- Report results back to the orchestrator
- Can communicate with peers when using Agent Teams mode

#### 3. Discovery Phase
Before decomposing work, the orchestrator runs a discovery step:
- Reads project structure (directories, config files, package manifests)
- Identifies the tech stack, build system, test framework
- Maps service/module boundaries
- Checks for existing CI/CD, linting, formatting configs
- Reads CLAUDE.md, .claude/ configs if present

This discovery output becomes shared context for all workers.

#### 4. Isolation Layer
Each worker gets a git worktree -- a separate working directory with its own branch, sharing the same repository. This provides:
- **Code isolation**: Workers can't step on each other's changes
- **Clean state**: Each worker starts from a known-good baseline
- **Easy merge**: Standard git merge/PR workflow to combine results

Future: Full environment isolation (ports, databases, services) via dev containers per worktree.

#### 5. Coordination Bus
Agents coordinate through multiple channels depending on the task:
- **Task list** (shared state): What needs doing, who's doing what, what's done
- **Direct messaging** (Agent Teams): When agents need to share findings or resolve conflicts
- **Git** (branches + PRs): The source of truth for code changes
- **Filesystem** (shared notes/artifacts): Intermediate results agents can read

#### 6. Oversight Controller
Configurable per-task human-in-the-loop:
- Determines when agents can proceed autonomously vs. when they pause for approval
- Manages PR creation and review workflows
- Handles escalation when agents get stuck or encounter ambiguity

## Building Blocks (Claude Code Ecosystem)

claude-swarm is built entirely on the Claude Code ecosystem:

| Component | How We Use It |
|-----------|---------------|
| **Claude Code CLI** | Runtime for every agent (orchestrator + workers) |
| **Agent Teams** | Peer coordination between workers when needed |
| **Custom Subagents** (.claude/agents/) | Specialized agent definitions for different task types |
| **Skills** (.claude/commands/) | Reusable workflow templates (discovery, review, testing, etc.) |
| **Hooks** | Lifecycle automation (auto-format, auto-test, security checks) |
| **MCP Servers** | Tool integration (GitHub, databases, monitoring, CI/CD) |
| **Agent SDK** | Programmatic spawning and management of agent instances |
| **Git Worktrees** | Code isolation between parallel workers |

## Task Types

The swarm handles the full development lifecycle:

### Feature Implementation
1. Orchestrator receives feature spec
2. Discovery phase maps the codebase
3. Orchestrator decomposes into subtasks (e.g., "add API endpoint", "update frontend form", "write integration tests")
4. Workers execute in parallel on isolated worktrees
5. Orchestrator reviews, resolves conflicts, opens PR

### Bug Investigation & Fixes
1. Orchestrator receives bug report (or picks up a GitHub issue)
2. Spawns investigation agent(s) to reproduce, trace, and identify root cause
3. Spawns fix agent with the diagnosis
4. Spawns test agent to add regression tests
5. Opens PR with fix + tests

### Code Review & Refactoring
1. Orchestrator receives review request or refactoring directive
2. Spawns review agents to analyze different aspects (architecture, performance, security, style)
3. Aggregates findings into structured feedback
4. For refactoring: spawns workers to execute changes across the codebase in parallel

### Testing & CI/CD
1. Orchestrator analyzes test coverage gaps or CI failures
2. Spawns agents to write missing tests, fix broken tests, or investigate CI issues
3. Validates by running the test suite
4. Opens PR with improvements

## Design Questions (Open)

These are decisions we're actively exploring:

### Coordination Depth
- When should the orchestrator use Agent Teams (peer messaging) vs. simple subagent delegation (fire-and-forget)?
- Should workers be able to spawn their own sub-workers? (Currently Claude Code prevents nested subagents)

### Conflict Resolution
- When two workers edit overlapping areas, how does the orchestrator resolve conflicts?
- Should we prevent overlap proactively (strict file boundaries) or handle it reactively (merge conflict resolution)?

### State Persistence
- How do long-running tasks survive across session boundaries?
- Should the swarm maintain a persistent task database or rely on git/GitHub as the source of truth?

### Observability
- What does the dashboard/monitoring look like?
- How do you inspect what each agent is doing in real-time?
- How do you measure token spend and cost per task?

### Error Recovery
- When a worker fails or produces bad output, how does the orchestrator handle it?
- Retry with more context? Reassign to a different worker? Escalate to human?

### Model Selection
- Should workers always use the same model, or should the orchestrator pick models based on task complexity?
- Opus for complex architectural decisions, Sonnet for routine implementation, Haiku for simple edits?

### Security Boundaries
- How do we prevent agents from making destructive changes (force pushes, dropping databases)?
- How do we handle secrets and credentials across the agent pool?

## Prior Art & Inspiration

- [Anthropic: Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) -- composable patterns
- [Anthropic: Multi-Agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system) -- orchestrator-worker at scale
- [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) -- experimental peer coordination
- [claude-flow](https://github.com/ruvnet/claude-flow) -- agent orchestration platform for Claude
- [ccswarm](https://github.com/nwiizo/ccswarm) -- multi-agent orchestration with git worktree isolation
- [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) -- teams-first multi-agent orchestration

## Roadmap

### Phase 1: Foundation
- [ ] CLI interface for the orchestrator
- [ ] Discovery phase implementation
- [ ] Git worktree isolation for workers
- [ ] Basic orchestrator-worker pattern (spawn N agents, collect results)
- [ ] Single-repo support

### Phase 2: Coordination
- [ ] Agent Teams integration for peer communication
- [ ] Shared task list management
- [ ] Conflict detection and resolution
- [ ] Configurable oversight levels

### Phase 3: Intake & Automation
- [ ] GitHub Issue intake (watch for labeled issues)
- [ ] Webhook support
- [ ] Scheduled/recurring tasks
- [ ] CI/CD integration

### Phase 4: Observability & Optimization
- [ ] Real-time agent monitoring dashboard
- [ ] Token spend tracking per task
- [ ] Cost optimization (adaptive model selection)
- [ ] Performance metrics and benchmarking

### Phase 5: Advanced Patterns
- [ ] Multi-repo support
- [ ] Environment isolation (dev containers per worktree)
- [ ] Agent memory/learning across sessions
- [ ] Custom MCP servers for swarm coordination

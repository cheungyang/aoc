# INSTRUCTIONS: EGM Design & Handoff Workflow

Your workflow operates in two phases: Co-Design (Interactive) and Finalization (Handoff).

## Phase 1: Interactive Co-Design
1. When the user proposes a feature or project, act as the sparring partner.
2. Identify and discuss edge cases, security flaws, data models, and performance bottlenecks.
3. Validate existing architecture by reading relevant project documents in `pkm/wiki/software/<project-slug>/` if necessary.

## Phase 2: Finalization & Handoff
Once the user agrees the architecture is sound, you must generate the final Design Specification and queue it for the backend graph.

### 1. Generate the Design Specification
You must write a highly rigorous Markdown file. You must use the `filesystem` tool to `write` or `overwrite` it to:
**Path:** `pkm/wiki/software/<project-slug>/specs/[Feature_Name]_v[Version].md`

`<project-slug>` is the project name in lowercase with hyphens for spaces and
underscores — `Recipe Box` → `recipe-box`. Everything
belonging to a project lives in that one folder.

**The Required Document Template:**
- **Context & Objective:** What are we building and why?
- **Strict Constraints:** Framework versions, language limits, performance requirements.
- **State & Schema:** Database models, API contracts, state management.
- **File-by-File Breakdown:** Explicit instructions on *exactly* which files need to be created or modified, and the logical steps required in each. (NO CODE SYNTAX).
- **Zero-Assumption Check:** A required final section verifying that this document contains all necessary context for a stateless agent to execute it without any outside knowledge.

### 2. Queue for LangGraph Execution

> **One project, one queue.** The backend contract is
> `pkm/wiki/software/<project-slug>/build_request.json` (schema version `3.0`).
> There is **no** shared `pkm/wiki/software/build_request.json` any more, and you
> must never create one. Each project's manifest carries that project's `repo`,
> `setup_command`, `max_concurrency` and `reviewers`, which is only coherent if
> one manifest describes exactly one project.

Use the `filesystem` tool to `read` the project's manifest, add your tasks to
`queue`, and `overwrite` it. If the project has no manifest yet, `write` a new
one with the full skeleton below. Never append XML, and never post the manifest
or any part of it into the channel — it is a machine interface, not a status
report.

```json
{
  "version": "3.0",
  "project_name": "[project_name]",
  "max_concurrency": 1,
  "setup_command": "[see §3 — the first step of a new project]",
  "repo": {
    "mode": "existing | create",
    "slug": "owner/repo",
    "default_branch": "main",
    "visibility": "private",
    "push_identity": "aoc-bot"
  },
  "reviewers": ["cheungyang"],
  "queue": [
    {
      "task_id": "feat_01_short_slug",
      "spec_path": "pkm/wiki/software/<project-slug>/specs/[Feature_Name]_v1.md",
      "status": "queued",
      "dependencies": [],
      "allowed_files": ["src/hooks/useFlashcards.ts", "src/hooks/useFlashcards.test.ts"],
      "verification_command": "npx vitest run src/hooks/useFlashcards.test.ts"
    }
  ]
}
```

Only `status: "queued"` tasks are picked up. The graph owns every other field
(`stage`, `attempts`, `run_id`, `branch_name`, `lease_*`, `last_error`,
`setup_done`) — write them and you will corrupt a run in flight.

**Dependencies are not optional.** A task whose code imports what an earlier
task writes must list that task in `dependencies`. Without it both branch from
`main`, the second one cannot find the first one's module, and the missing
import is read as the worker's own bug — which spends its whole budget.


### 3. Environment Setup Is the First Step of a New Project
Tests don't run where the code is written. Each verification starts a fresh
**E2B sandbox** (the default verify backend): it downloads the pushed commit
from GitHub, runs the manifest's **`setup_command`**, then the task's
`verification_command`. Nothing reaches the sandbox that wasn't committed, so
before you write a single feature task, decide how the project's dependencies
get installed and put that in `setup_command` at the top level of the manifest.

`setup_command` **installs dependencies, and nothing else**. It runs before
every verification, and the sandbox fails a setup that creates or changes any
file the commit doesn't have — a scaffold, an `npm install <pkg>` that rewrites
`package.json`, a regenerated lockfile:

```
"setup_command": "npm ci --no-audit --no-fund"
```
```
"setup_command": "python -m venv .venv && .venv/bin/pip install -e '.[dev]'"
```

Rules you must follow:

1. **Never prefix `npm install &&` (or any install) onto `verification_command`.**
   `verification_command` must be a pure test invocation and nothing else. An
   install in the test command re-runs on every attempt, and — the reason this
   rule exists — makes a broken environment look like a failing test, which
   spends the task's 3-attempt implement budget on something no amount of code
   could fix.
2. **`setup_command` must be idempotent, non-interactive and install-only.**
   It runs with no terminal attached, before every test run. Install into
   ignored paths (`node_modules/`, a gitignored `.venv`) from what is committed:
   `npm ci` with the lockfile committed, not `npm install`; never `create-vite`,
   `cp -r` or `npm i -D <pkg>`. Use `"repo.mode": "existing"` for a repository
   that already exists (`cheungyang/aoc` included: every repository is named by
   its `repo.slug`) or `"create"` for a new one.
3. **Put it at the manifest level** when it describes the project. Use a
   task-level `setup_command` only for the bootstrap task (rule 6) or to
   override it for one unusual task.
4. **A failing `setup_command` halts the task as `kind: "environment"` and does
   not consume the implement budget.** You will see it in the tick report as
   "`setup_command` failed in the e2b sandbox". Fix the command in the manifest
   and re-queue; do not re-write the spec.
5. **`--passWithNoTests` is banned.** It converts "the worker wrote nothing" into
   a pass. If a task's test file does not exist yet, the spec must instruct the
   worker to create it, and the task is only done when it genuinely runs.
6. For a **brand new project**, the first queued task should still be a real
   feature — and it carries the bootstrap. A scaffold and its lockfile are code
   that must be committed once, not regenerated before every test, so give that
   one task its own `"environment": {"backend": "local"}` and a task-level
   `setup_command` that scaffolds and installs (e.g.
   `npm create vite@latest . -- --template react-ts --no-interactive && npm install && npm i -D vitest @testing-library/react jsdom`).
   It runs on the host, its push commits the scaffold, and every later task
   inherits it under the manifest's install-only `setup_command` on E2B. Do not
   create a separate "set up the project" task.
7. **A project whose tests need system packages or a heavy dependency set**
   (this repository's own venv is over a gigabyte) needs an E2B template with
   them pre-installed, named in `"environment": {"template": "..."}`. Building
   one is the user's: ask. Until it exists, put
   `"environment": {"backend": "local"}` at the manifest level and say why.

### 4. Deployment (when the user wants it live)
If the user wants the project somewhere they can open it after each approved
merge, add a `deploy` block at the manifest's top level. Deployment belongs to
the project, not to a task: when a PR merges, the graph asks for the merge
commit to go live and reports `🌐 … is live at <url>` once it has. A merged
task is done either way — a failed deploy never holds back its dependents.

```json
"deploy": { "backend": "github_pages" }
```

- `github_pages`: the repository deploys itself to GitHub Pages on every push
  to `main`; the graph only watches. The user sets that up with one command,
  `scripts/coding_deploy_setup.py --project <project>`: it switches Pages on,
  commits the deploy workflow (a Vite app is built for its `/<repo>/` path),
  adds this block if it's missing, and waits for the first deployment. It runs
  as the user, because the bot's token can't switch Pages on or commit
  workflows, so ask them to run it once the project's first task has merged;
  don't queue a task for it.
- `github_deployment`, with `"environment": "<name>"`: any deploy that a GitHub
  Actions job reports to that environment — for example Fly.io deployed by a
  workflow job with `environment: production`.
- Optional: `url` (shown when the deployment reports none) and
  `timeout_minutes` (default 30) before a commit that hasn't gone live counts
  as failed.
- Never write the top-level `deployment`: the graph owns it, like `tick_error`.

### 5. Notification
Inform the user in `#software-dev`, in prose, that the specification is
finalized, saved, and queued. One or two sentences: what was queued, and the
`setup_command` you chose (and where it deploys, if it does). No XML, no JSON,
no manifest dumps.
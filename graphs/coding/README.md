# Coding Graph

The coding graph is **one reconciliation tick** over a single project's build manifest. A tick
reclaims dead leases, picks exactly one task, advances it as far as it safely can — provision,
implement, verify, audit, publish, or sync a review decision off GitHub — and then returns.
There is no in-graph waiting and no human-in-the-loop interrupt: *"retry" is simply another tick*.
The previous interrupt-driven topology was removed because its durable state lived in a LangGraph
checkpoint keyed to the caller's session, so a halted run could not be resumed from anywhere else
(C4/C5). Durable state now lives where it can be inspected and hand-edited: the manifest on disk,
the git repository, and the pull requests on GitHub ([graph.py:L1-L7](file:///Users/alvac/aoc/graphs/coding/graph.py#L1-L7)).

> [!IMPORTANT]
> The graph compiles with `checkpointer=None` even when the loader passes one. A checkpoint would
> reintroduce exactly the resume problem this design removed.

## Workflow

```mermaid
flowchart TD
    START([START]) --> scheduler

    scheduler{{scheduler}}
    implement{{implement}}
    verify{{verify}}
    audit{{audit}}
    publish{{publish}}
    sync_review{{sync_review}}
    END([END])

    scheduler -->|"route = implement"| implement
    scheduler -->|"route = publish"| publish
    scheduler -->|"route = sync_review"| sync_review
    scheduler -->|"route = done (or unset)"| END

    implement -->|"route = verify"| verify
    implement -->|"route = done (or unset)"| END

    verify -->|"route = audit"| audit
    verify -->|"route = implement"| implement
    verify -->|"route = done (or unset)"| END

    audit -->|"route = publish"| publish
    audit -->|"route = done (or unset)"| END

    publish -->|"route = scheduler"| scheduler
    publish -->|"route = done (or unset)"| END

    sync_review -->|"route = implement"| implement
    sync_review -->|"route = publish"| publish
    sync_review -->|"route = scheduler"| scheduler
    sync_review -->|"route = done (or unset)"| END
```

Topology and allowed route sets are declared in
[create_graph()](file:///Users/alvac/aoc/graphs/coding/graph.py#L22-L83).

Three rationale notes are embedded in the edges themselves:

- **`implement` can only go to `verify`.** A worker that produced nothing ends the tick rather than
  testing an unchanged tree; the next tick retries within the implement budget
  ([graph.py:L56-L60](file:///Users/alvac/aoc/graphs/coding/graph.py#L56-L60)).
- **`audit` has no path back to the worker.** The audit is advisory, so its verdict rides along to
  the PR and the tick carries on to publish
  ([graph.py:L66-L70](file:///Users/alvac/aoc/graphs/coding/graph.py#L66-L70)).
- **`publish` never loops in-process.** A transient failure is retried by the *next* tick, so one
  tick cannot spin on a GitHub outage
  ([graph.py:L72-L76](file:///Users/alvac/aoc/graphs/coding/graph.py#L72-L76)).

## The Tick Model

Routing is done by a single router factory,
[`_router(allowed)`](file:///Users/alvac/aoc/graphs/coding/graph.py#L45-L49):

```python
def _router(allowed):
    def route(state: CodingState):
        value = state.get("route")
        return value if value in allowed else END
    return route
```

`route` is a **persistent LangGraph channel**, declared on `CodingState`
([schemas.py:L145-L149](file:///Users/alvac/aoc/graphs/coding/schemas.py#L145-L149)). Because it is
a channel it survives until a node overwrites it, so every router reads it as an **explicit
instruction** rather than inferring the next step. If the value is not one of that router's own
options — including a route nobody set, or a node's `"done"` sentinel — the tick **ends**. A node
that forgets to set a route stops the tick instead of re-running whatever the last node asked for.

> [!NOTE]
> `route` must be declared as a channel or LangGraph drops it between nodes and *every* conditional
> edge falls through to `END`. This is precisely what the end-to-end tests in
> [test_graph.py](file:///Users/alvac/aoc/tests/graphs/coding/test_graph.py#L81-L88) exist to catch;
> unit tests calling nodes directly cannot.

**What ends a tick:**

- the scheduler finds nothing to do (empty queue, all tasks leased, concurrency bound reached,
  or the polling window is closed) — it emits `"done"`;
- a missing `build_request_path`, a failed preflight, a repo that cannot be resolved, a lost lease
  race, a failed worktree provision, or a failed setup command;
- any node that halts or yields a task (`route: "done"`): exhausted implement budget, a worker that
  modified no files, a missing `verification_command`, an environment-class verification failure, a
  push/PR failure;
- `publish` and `sync_review` routing back to `scheduler`, which then finds no *unhandled* task
  (`tick_handled` guarantees one tick cannot work the same task twice).

**What a subsequent tick resumes from:** the task's recorded `stage` in the manifest.
[`select_task()`](file:///Users/alvac/aoc/graphs/coding/nodes/scheduler.py#L42-L79) maps stage →
route so work already done is not redone: `awaiting_review`/`published` → `sync_review`,
`verified`/`audited` → `publish`, anything else → `implement`. Every node additionally opens with a
`stage_at_or_past(...)` guard backed by a worktree content digest, so "already implemented" and
"already verified" are claims with evidence behind them, not flags.

## State

### `CodingState` ([schemas.py:L102-L169](file:///Users/alvac/aoc/graphs/coding/schemas.py#L102-L169))

`total=False` TypedDict. Grouped as in the source.

| Field | Type | Description |
|---|---|---|
| `build_request_path` | `str` | Absolute path to this project's `build_request.json`. The one required input. |
| `project_name` | `str` | Project name, from kwargs/query or the manifest. |
| `target_repo` | `str` | `owner/repo`, legacy top-level form of `repo.slug`. |
| `repo` | `RepoDescriptor` | The repository block declared in the manifest. |
| `max_concurrency` | `int` | Upper bound on live leases before a tick declines to pick up work. |
| `queue` | `List[TaskEnvelope]` | The manifest queue as loaded by the scheduler. |
| `completed_tasks` | `List[str]` | Task ids with status `done` (written on idle exits). |
| `failed_tasks` | `List[str]` | Task ids with status `failed`/`halted`/`blocked` (idle exits). |
| `run_id` | `str` | Per-task run identifier; names the worktree directory. |
| `thread_id` | `str` | Caller thread, defaults to `session_id`. |
| `session_id` | `str` | Caller session, if any. |
| `channel` | `str` | Channel the worker call is attributed to; defaults `coding-pipeline`. |
| `current_task` | `TaskEnvelope` | The one task this tick is advancing. Nodes feed writes back into it. |
| `project_path` | `str` | Spec directory, `pkm/wiki/software/<project>`. |
| `workspace_path` | `str` | Worktree directory, `<repo_root>/workspaces/runs/<run_id>`. |
| `branch_name` | `str` | `feat/<project>/<feature>_<run_id>`. |
| `base_branch` | `str` | Override for the branch a new worktree is based on. |
| `base_ref` | `str` | Alternate override; falls back to `origin/<default_branch>`. |
| `spec_path` | `str` | Path to the task's spec file. |
| `implementation_summary` | `str` | Worker's summary of what it changed; goes into the PR body. |
| `test_run_passed` | `bool` | Whether the verification command exited 0. |
| `test_stdout` | `str` | Verification stdout. |
| `test_stderr` | `str` | Sanitized failure output, fed back to the worker on retry. |
| `modified_files` | `List[str]` | From `git status --porcelain` — never the model's own file list. |
| `diff_summary` | `str` | Sanitized diff the audit reviewed. |
| `pr_url` | `str` | PR URL, only ever set from a `gh`-confirmed response. |
| `pr_number` | `Optional[int]` | PR number. |
| `latest_human_feedback` | `str` | Chat nudge passed to the worker as feedback (never an approval). |
| `github_pr_comments` | `List[str]` | New reviewer comments harvested from the PR. |
| `commit_url` | `str` | Merge commit URL once the PR lands. |
| `error_message` | `str` | Fatal-for-this-tick message; `format_output` renders it with 🛑. |
| `messages` | `List[AnyMessage]` | Seeded with the incoming query as a `HumanMessage`. |
| `route` | `str` | The tick channel: `implement`/`verify`/`audit`/`publish`/`sync_review`/`scheduler`/`done`. |
| `stage` | `TaskStage` | Stage reached by `current_task` during this tick. |
| `graph_id` | `str` | `"coding"`, read from `graph.json`; the authority field for the worker's tool roster. |
| `required_tools` | `List[str]` | Derived from `graph.json`'s `tools` grant — the grant *is* the requirement. |
| `audit_passed` | `bool` | Advisory audit verdict. |
| `audit_feedback` | `str` | Advisory audit findings; posted to the PR, and re-prompted to the worker. |
| `reviewers` | `List[str]` | GitHub logins whose signals count. |
| `approval_signals` | `Optional[List[str]]` | Overrides `DEFAULT_APPROVAL_SIGNALS`. |
| `rejection_signals` | `Optional[List[str]]` | Overrides `DEFAULT_REJECTION_SIGNALS`. |
| `lease_owner` | `str` | This tick's lease owner id (`tick_<hex>` when not supplied). |
| `impl_digest` | `Optional[str]` | Worktree digest after implement. |
| `head_sha` | `str` | HEAD of the pushed branch. |
| `poll_until` | `Optional[float]` | Epoch until which the scheduled tick keeps checking GitHub. |
| `repo_root` | `str` | Checkout the tick works against: project root (`self`) or the cached clone. |
| `tick_report` | `List[str]` | Lines the runner posts. Accumulated across the whole tick. |
| `tick_handled` | `List[str]` | Task ids already touched — one tick cannot work the same task twice. |

### `TaskEnvelope` ([schemas.py:L47-L89](file:///Users/alvac/aoc/graphs/coding/schemas.py#L47-L89))

One queue entry in the manifest.

| Field | Type | Description |
|---|---|---|
| `task_id` | `str` | Unique id within the manifest. |
| `project_name` | `str` | Owning project. |
| `feature_name` | `str` | Used to build the branch name. |
| `spec_path` | `str` | Path to the spec the worker and critic read. |
| `dependencies` | `List[str]` | Prerequisite task ids; gated on *completion*, not on a branch existing. |
| `allowed_files` | `List[str]` | Strict filesystem whitelist handed to the worker. |
| `setup_command` | `Optional[str]` | Run once per worktree before any code is written. Falls back to the manifest-level `setup_command`. |
| `setup_done` | `bool` | Reset whenever the worktree is re-created. |
| `verification_command` | `str` | The CLI test command. Absence halts the task as a config error. |
| `acceptance_criteria` | `str` | Given-When-Then criteria. |
| `status` | `TaskStatus` | v3 vocabulary, legacy values still readable. |
| `run_id` | `Optional[str]` | Run that owns the worktree. |
| `branch_name` | `Optional[str]` | Feature branch. |
| `target_repo` | `Optional[str]` | Per-task repo override. |
| `pr_url` | `Optional[str]` | Confirmed PR URL. |
| `commit_url` | `Optional[str]` | Merge commit URL. |
| `error_message` | `Optional[str]` | Legacy free-text error. |
| `stage` | `TaskStage` | Where the task got to — the resume point. |
| `attempts` | `Dict[str, int]` | Per-stage counters, e.g. `{"implement": 2, "publish": 1}`, so a flaky `gh` does not burn the LLM budget (B1). |
| `lease_owner` | `Optional[str]` | Holder of the current claim. |
| `lease_expires_at` | `Optional[float]` | Epoch expiry of the claim. |
| `impl_digest` | `Optional[str]` | Digest of the worktree after implement. |
| `verified_digest` | `Optional[str]` | Digest of the tree verification passed on. |
| `head_sha` | `Optional[str]` | Pushed HEAD. |
| `review_cursor` | `Optional[str]` | Highest review comment id already actioned (E5). |
| `poll_until` | `Optional[float]` | Review polling window; outside it the task is dormant. |
| `last_error` | `Optional[TaskError]` | Classified last failure. |
| `updated_at` | `float` | Set on every manifest write. |

### `TaskError` ([schemas.py:L36-L44](file:///Users/alvac/aoc/graphs/coding/schemas.py#L36-L44))

| Field | Type | Description |
|---|---|---|
| `stage` | `str` | Where it failed (`provision`, `setup`, `implement`, `verify`, `publish`, `merge`, `control`, …). |
| `kind` | `str` | `llm` \| `verification` \| `git` \| `github` \| `config` \| `environment` \| `operator` \| `unknown`. Infrastructure failures must not consume the LLM budget. |
| `message` | `str` | Human-readable detail. |
| `at` | `float` | Epoch timestamp. |

### `RepoDescriptor` ([schemas.py:L92-L99](file:///Users/alvac/aoc/graphs/coding/schemas.py#L92-L99))

| Field | Type | Description |
|---|---|---|
| `mode` | `"self" \| "existing" \| "create"` | Where the checkout comes from. |
| `slug` | `Optional[str]` | `owner/repo` on GitHub. |
| `default_branch` | `str` | Base for new worktrees and PRs. |
| `visibility` | `"private" \| "public"` | Used by `gh repo create`. |
| `push_identity` | `Optional[str]` | GitHub login of the machine user, if any. The token itself is read from a file outside the repo. |
| `push_identity_email` | `Optional[str]` | Commit author email for that identity. |

### Literals and constants

- `TaskStatusV3` — `queued`, `active`, `awaiting_review`, `done`, `failed`, `halted`, `blocked`.
- `TaskStatusLegacy` — `pending`, `in_progress`, `in_review`, `completed`, `rejected`; accepted on
  read and mapped by `migrate_manifest`. Nothing writes them any more.
- `TaskStatus` — the union of both, since a manifest on disk may predate the migration.
- `TaskStage` / `STAGE_ORDER` — `queued` → `provisioned` → `implemented` → `verified` → `audited`
  → `published` → `awaiting_review` → `merged` → `done`. Every stage is the output of exactly one
  node, which is what makes "never redo the LLM part" true after an infrastructure failure.

## Nodes

### scheduler

[scheduler.py:L82-L263](file:///Users/alvac/aoc/graphs/coding/nodes/scheduler.py#L82-L263) ·
[`select_task()`](file:///Users/alvac/aoc/graphs/coding/nodes/scheduler.py#L42-L79) ·
[`_resolve_base_ref()`](file:///Users/alvac/aoc/graphs/coding/nodes/scheduler.py#L267-L281)

**Responsibility.** The entry node of every tick. It decides *what* to advance, in a fixed order:
reclaim dead leases, respect the concurrency bound, pick one task, preflight before any token is
spent, resolve the repository, claim the task with a lease, provision and set up its worktree, and
route by the task's recorded stage.

**Reads:** `build_request_path`, `lease_owner`, `tick_handled`, `tick_report`, `project_name`,
`repo`, `max_concurrency`, `graph_id`, `required_tools`, `base_branch`/`base_ref`, `error_message`.

**Writes:** `build_request_path` (resolved), `project_name`, `repo`, `repo_root`, `queue`,
`current_task`, `route`, `stage`, `run_id`, `branch_name`, `workspace_path`, `spec_path`, `pr_url`,
`lease_owner`, `tick_handled`, `tick_report`, `error_message`; on idle exits also `completed_tasks`
and `failed_tasks`.

**Routes:**

| Route | Meaning |
|---|---|
| `sync_review` | An `awaiting_review` task inside its polling window, or a runnable task whose stage is `awaiting_review`/`published`. Reviews go first: they are cheap, they unblock dependents, and a merge may make another task runnable in the same tick. |
| `publish` | A runnable task already at stage `verified` or `audited` — no LLM needed. |
| `implement` | Anything else runnable. |
| `done` | No `build_request_path`; concurrency bound reached; nothing runnable; preflight failed; repo unavailable; lease lost to another tick; worktree provisioning failed; setup command failed. Ends the tick. |

**Side effects.** Manifest: `reclaim_expired_leases`, `acquire_lease`, `persist_task` (status,
stage, `run_id`, `branch_name`, `setup_done`, halts with `last_error`). Git: `provision_worktree`
(idempotent, and skipped for `sync_review` — re-creating the worktree under a task waiting on
review would throw the code away). Network: `preflight_tick` (tool-roster check, push-identity
resolution, push access), `ensure_repo_available` (clone/fetch/`gh repo create`). Shell: the
task's `setup_command`, once per worktree.

> [!NOTE]
> Preflight runs only *after* a task has been selected. Running it earlier would make an empty
> queue cost a network round trip on every scheduled tick. Setup failures are recorded as
> `environment`, never against the implement budget — the code is not what went wrong. Dependents
> branch from the default branch, not from a prerequisite's branch, which the merge has deleted by
> then (F3).

### implement

[implement.py:L40-L193](file:///Users/alvac/aoc/graphs/coding/nodes/implement.py#L40-L193)

**Responsibility.** The **only** node that calls the LLM to write code. It touches nothing outside
the worktree: no git mutation, no network, no manifest beyond its own bookkeeping. That separation
is the point — when a push or PR call later fails, the next tick resumes at `publish` and this node
is skipped entirely, because its output is still in the worktree and its digest still matches.

**Reads:** `build_request_path`, `current_task` (`task_id`, `stage`, `impl_digest`, `attempts`,
`spec_path`, `allowed_files`, `acceptance_criteria`, `verification_command`), `workspace_path`,
`spec_path`, `test_stderr`, `audit_feedback`, `github_pr_comments`, `latest_human_feedback`,
`graph_id`, `channel`, `tick_report`.

**Writes:** `current_task` (refreshed from the manifest write, including the new attempt count),
`stage`, `route`, `impl_digest`, `modified_files`, `implementation_summary`, `tick_report`,
`error_message`; and clears the consumed feedback channels `test_stderr`, `audit_feedback`,
`latest_human_feedback`, `github_pr_comments`.

**Routes:**

| Route | Meaning |
|---|---|
| `verify` | Files changed (or the stage guard matched an unchanged digest, so the LLM was skipped entirely). |
| `done` | No `task_id`; implement budget exhausted (`MAX_IMPLEMENT_ATTEMPTS = 3`, task halted); worker modified no files (task yielded back to the queue with no stage advance, so the next tick retries); worker was blocked by a missing permission (halted as `config`). |

**Side effects.** Worker subprocess via
[`call_worker`](file:///Users/alvac/aoc/graphs/coding/utils/worker.py#L78-L93) →
`agent_call` on `graph-worker`. Read-only git: `git status --porcelain` for the true file list, and
`git diff HEAD` + status for the digest. Manifest: `bump_attempt`, `persist_task`, `yield_task`.

> [!IMPORTANT]
> The attempt count must travel in `current_task`, not only to disk. `current_task` is the snapshot
> the scheduler took at the start of the tick and `verify` reads the budget off it — leaving it
> stale meant `verify` saw 0 attempts every time, so `implement → verify → implement` never
> terminated and one tick burned the whole recursion limit on LLM calls.

A worker reply of "I was not allowed to" is not a worker that failed to think; three more attempts
cannot grant it a tool, so a blocked reply halts instead of retrying. `git` is ground truth for what
changed — the model's own `<modified_files>` list is ignored (§7.6).

### verify

[verify.py:L87-L233](file:///Users/alvac/aoc/graphs/coding/nodes/verify.py#L87-L233) ·
[`classify_failure()`](file:///Users/alvac/aoc/graphs/coding/nodes/verify.py#L54-L84)

**Responsibility.** The only **blocking** quality gate. Runs the task's `verification_command`
inside the worktree with a 600-second timeout.

**Reads:** `build_request_path`, `current_task` (`task_id`, `stage`, `verified_digest`,
`verification_command`, `attempts`), `workspace_path`, `impl_digest`, `tick_report`.

**Writes:** `current_task`, `stage`, `route`, `test_run_passed`, `test_stdout`, `test_stderr`
(sanitized), `tick_report`, `error_message`.

**Routes:**

| Route | Meaning |
|---|---|
| `audit` | The command exited 0, or this exact tree already passed (digest match). |
| `implement` | A genuine test failure with implement budget left — the output is handed back to the worker. Writes the *cleared* digest and real attempt count into `current_task`. |
| `done` | No `task_id`; no `verification_command` (halt, `config`); workspace missing (yield, re-provision next tick); the failure was classified `environment` (halt, implementation left alone); budget exhausted (halt). |

**Side effects.** Shell: the verification command via `run_in_worktree`. Read-only git for the
digest. Manifest: `persist_task` / `yield_task`.

> [!NOTE]
> Two things this node deliberately does *not* do: it does not require a PR to exist — gating local
> tests behind a network operation is what turned a GitHub hiccup into "tests failed" (A3) — and it
> does not re-run for a tree it has already tested. `classify_failure` is conservative on purpose:
> anything it cannot *prove* is an environment problem stays `verification` and goes back to the
> worker, because misreading a real test failure as a broken environment would halt a task one more
> attempt would have fixed. The converse case (a missing dependency handed to the worker) is what
> produced 25 rewrites of a correct implementation.

### audit

[audit.py:L26-L63](file:///Users/alvac/aoc/graphs/coding/nodes/audit.py#L26-L63) ·
[`_run_audit()`](file:///Users/alvac/aoc/graphs/coding/nodes/audit.py#L85-L115)

**Responsibility.** Advisory anti-pattern review of the diff against the spec — Fake It Trap, Happy
Path Bias, Silent Failure, Bloated Files.

**Reads:** `build_request_path`, `current_task` (`task_id`, `stage`, `spec_path`,
`acceptance_criteria`, `allowed_files`), `workspace_path`, `spec_path`, `channel`, `graph_id`,
`tick_report`.

**Writes:** `stage` (`audited`), `route`, `audit_passed`, `audit_feedback`, `diff_summary`,
`tick_report`, `error_message`.

**Routes:**

| Route | Meaning |
|---|---|
| `publish` | Always — whether the audit passed, raised concerns, was already done for this tree, or could not run at all. |
| `done` | Only when `current_task` has no `task_id`. |

**Side effects.** Read-only git: `get_git_diff`. Worker subprocess via `call_worker` for the critic
verdict. Manifest: `persist_task(stage="audited")`.

> [!IMPORTANT]
> The audit is **always advisory, and that is not configurable**. The critic is good at catching a
> fake implementation that passes its own tests and bad at judging whether a 200-line file is a
> problem, so its opinion is written into the PR for the human reviewer instead of blocking the
> pipeline. A mode that *could* gate on this verdict would be an untested path behind a flag, one
> parser slip away from bouncing correct work back to the worker. An audit that cannot run is not a
> rejection either: the old critic failed closed and blocked the pipeline whenever the LLM call
> errored, which would now spam every PR with a message about the auditor rather than the code.

### publish

[publish.py:L31-L141](file:///Users/alvac/aoc/graphs/coding/nodes/publish.py#L31-L141) ·
[`_retry_or_halt()`](file:///Users/alvac/aoc/graphs/coding/nodes/publish.py#L144-L170) ·
[`_upsert_pull_request()`](file:///Users/alvac/aoc/graphs/coding/nodes/publish.py#L173-L208)

**Responsibility.** Commit, push, upsert the pull request, and post the review context onto it.
Everything here is deterministic and idempotent, and **nothing here calls the LLM** — which is what
makes retrying safe: if `gh` is down, the next tick re-enters with the same code and spends no
tokens.

**Reads:** `build_request_path`, `current_task`, `workspace_path`, `branch_name`, `repo`,
`target_repo`, `base_branch`, `project_name`, `run_id`, `pr_url`, `implementation_summary`,
`spec_path`, `test_run_passed`, `audit_feedback`, `tick_report`.

**Writes:** `stage` (`awaiting_review`), `pr_url`, `pr_number`, `head_sha`, `poll_until`, `route`,
`tick_report`, `error_message`.

**Routes:**

| Route | Meaning |
|---|---|
| `scheduler` | The PR is open and the task is now `awaiting_review`. Control returns to the scheduler, which may pick up a *different* task in the same tick (`tick_handled` prevents re-picking this one). |
| `done` | No `task_id`; no worktree or branch; push failed; PR could not be opened. Retries within the publish budget (`MAX_PUBLISH_ATTEMPTS = 3`) on later ticks, then halts. |

**Side effects.** Git/GitHub: `commit_and_push`, `git rev-parse HEAD`, `gh pr list` /
`get_pull_request_status` / `create_pull_request`, `comment_pull_request`. Manifest: `bump_attempt`,
`yield_task`, `persist_task` (status `awaiting_review`, `pr_url`, `pr_number`, `head_sha`,
`poll_until = now + 1h`, lease released).

Two rules from §6.3 hold throughout:

- **A URL is only reported if `gh` confirmed it.** Fabricated links (D2) are gone, so a link in the
  channel is proof the PR exists.
- **A push that succeeded is never thrown away.** If the PR call fails, the task halts with a
  clickable compare URL, and the next tick *adopts* the PR you open from it — `_upsert_pull_request`
  always looks before creating.

Publishing failures retry within their own budget, never the LLM's, and the lease is handed back
rather than held: holding it over a transient `gh` failure would keep the task invisible until the
lease expired. A failure to post the review-context comment is logged and never fatal — the PR
exists and is reviewable, and losing a comment must not trigger a retry that re-pushes.

### sync_review

[sync_review.py:L158-L246](file:///Users/alvac/aoc/graphs/coding/nodes/sync_review.py#L158-L246) ·
[`evaluate_signals()`](file:///Users/alvac/aoc/graphs/coding/nodes/sync_review.py#L55-L115) ·
[`harvest_comments()`](file:///Users/alvac/aoc/graphs/coding/nodes/sync_review.py#L118-L155) ·
[`_finish()`](file:///Users/alvac/aoc/graphs/coding/nodes/sync_review.py#L249-L304)

**Responsibility.** Read the human decision off GitHub and act on it. **GitHub is the only approval
surface**: there is no interrupt, no chat approval and no keyword classification.

**Reads:** `build_request_path`, `current_task` (`task_id`, `pr_url`, `pr_number`, `review_cursor`),
`pr_url`, `workspace_path`, `repo`, `target_repo`, `reviewers`, `approval_signals`,
`rejection_signals`, `lease_owner`, `tick_report`.

**Writes:** `stage`, `github_pr_comments`, `commit_url`, `route`, `tick_report`, `error_message`.

Signals, first match wins:

| # | Approval | What it is |
|---|---|---|
| 1 | `merged_manually` | PR state is `MERGED` — terminal, checked first |
| 2 | `review_approved` | a native review by a listed reviewer |
| 3 | `label:approved` | the `approved` label |
| 4 | `comment:/approve` | a comment whose **first line** is exactly `/approve` |

Rejections are symmetric — `changes_requested`, `comment:/reject` (back to the worker) and
`comment:/abort` (fail the task and tear down) — and are evaluated *before* approvals, so a label
added after a "request changes" does not override it. A `CLOSED` PR is an abort.

**Routes:**

| Route | Meaning |
|---|---|
| `publish` | There is no PR URL to sync against; fall back to publish, which adopts an existing PR for the branch if there is one. |
| `implement` | Changes requested, or new unresolved reviewer comments. Task returns to stage `provisioned`, digest cleared, `review_cursor` advanced. |
| `scheduler` | Terminal or no-op outcomes: merged/approved-and-merged (task `done`, worktree torn down), aborted (task `failed`, worktree removed), an approval whose merge failed (stays `awaiting_review`, retried in 15 minutes), or nothing new (lease released so the next tick can look again). |
| `done` | Only when `current_task` has no `task_id`. |

**Side effects.** GitHub: `get_pull_request_status`, `get_unresolved_review_threads`,
`merge_pull_request` (squash, delete branch). Git: `teardown_worktree`. Manifest: `persist_task`
(status `done`/`failed`/`active`, stage, `review_cursor`, `poll_until`, `commit_url`),
`release_lease`.

> [!NOTE]
> [`first_line_command()`](file:///Users/alvac/aoc/graphs/coding/nodes/sync_review.py#L35-L45)
> matches the first line **exactly**. Prose cannot trigger it, a quoted `/approve` cannot, and a
> code block mentioning it cannot — this is the direct fix for the gate that read "ok" inside
> "looks broken" and defaulted to *approved* when it found nothing (E1, E2). Comment harvesting
> excludes the machine user (otherwise the system reacts to its own test-result comments) and
> ignores ids at or below `review_cursor`, so a resume does not re-action a comment already
> addressed (E5). Inline review threads live in a different API from issue comments and are fetched
> separately; a review written entirely on the lines would otherwise look like silence.

## Durable State

There is no checkpointer. Everything a later tick needs is on disk or on GitHub.

**The manifest — [utils/manifest.py](file:///Users/alvac/aoc/graphs/coding/utils/manifest.py)** is
the system of record, one `build_request.json` per project. Three properties make that safe:

1. **Atomic writes** — temp file in the same directory, then `os.replace`, so a crash mid-write
   leaves the previous manifest intact rather than a truncated one. A corrupt manifest raises
   rather than being silently replaced by an empty one, which would look like "all tasks finished".
2. **Locked read-modify-write** — `locked_manifest()` and `persist_task()` re-read under an
   exclusive `flock` (on a sidecar `.lock` file, because the atomic write replaces the inode) and
   write back the merged document. Nodes used to rewrite the whole document from memory, so two
   writers silently lost each other's updates.
3. **Leases** — a task claimed by a process that then dies is reclaimed on the next tick at its
   recorded stage, which turns "stuck in_progress forever" into automatic crash recovery.

The manifest **is the API**: hand-editing the JSON and running another tick is a supported
operation, and that is exactly what
[utils/control.py](file:///Users/alvac/aoc/graphs/coding/utils/control.py) does — nothing in the
control plane runs the pipeline, it only makes a task runnable and lets the next tick pick it up.

**The git repo — [utils/repo.py](file:///Users/alvac/aoc/graphs/coding/utils/repo.py)** holds the
actual work. The worktree under `workspaces/runs/<run_id>` carries the code between ticks; the
branch carries it after a push. The checkout itself is resolved from `repo.mode`: `self` (this
process's own repository, rooted by walking up to the `.git` marker — never `os.getcwd()`),
`existing` (a managed clone at `workspaces/repos/<owner>__<repo>`, fetched not re-cloned), or
`create` (`gh repo create`, then behave as `existing`).

**GitHub** holds the review state. `publish.py` writes it (branch, PR, review-context comment);
`sync_review.py` reads it back (PR state, review decision, labels, issue comments, unresolved
inline threads). Approval exists nowhere else — a nudge in chat reopens the polling window and is
passed to the worker as feedback, but it is not an approval.

**The DAG — [utils/dag.py](file:///Users/alvac/aoc/graphs/coding/utils/dag.py)** is the queue
itself. `get_runnable_tasks()` evaluates topological dependencies over the manifest: status
`queued`/`pending`, all dependencies *completed*, not currently leased. Dependencies gate on
completion rather than on the prerequisite's branch existing, so a dependent cannot start against a
branch the merge deleted (F3). There is one manifest per project and deliberately no global
fallback — a run that was never told which project it was for used to find *a* queue, the wrong one.

**How a new tick rebuilds context.** It is handed only a `build_request_path`. From there:
`reclaim_expired_leases` frees anything a dead process held; `load_manifest` (migrating v1/v2 to v3
in memory) restores the queue, repo descriptor, reviewers and concurrency bound; `select_task`
picks one task and reads its `stage` as the resume point; `ensure_repo_available` and the
provisioning guard restore or reuse the worktree; the digest recorded against `impl_digest` /
`verified_digest` proves whether the previous tick's LLM output and test result are still valid;
`attempts` restores the per-stage budgets; `pr_url` and `review_cursor` restore the review
conversation. Nothing depends on the caller who started the previous tick.

## Utilities

| Module | Purpose |
|---|---|
| [dag.py](file:///Users/alvac/aoc/graphs/coding/utils/dag.py) | Manifest path resolution (one per project under `pkm/wiki/software/<slug>/`), project-slug normalisation, manifest discovery, and topological selection of runnable tasks. |
| [manifest.py](file:///Users/alvac/aoc/graphs/coding/utils/manifest.py) | Durable persistence: atomic writes, `flock`-protected read-modify-write, v1/v2→v3 migration, per-stage attempt counters, leases and their reclamation, and the `stage_at_or_past` guard every node opens with. |
| [control.py](file:///Users/alvac/aoc/graphs/coding/utils/control.py) | Operator control plane — `status_report`, `retry`, `reset`, `skip`, `abort`, `unblock`. Every operation is a manifest write; `reset` is the only destructive one. |
| [repo.py](file:///Users/alvac/aoc/graphs/coding/utils/repo.py) | Repository descriptor defaults, machine-user (`push_identity`) resolution, project-root detection, and the `self`/`existing`/`create` repository modes. |
| [worker.py](file:///Users/alvac/aoc/graphs/coding/utils/worker.py) | The single place the graph reaches `graph-worker`, so the call always carries this graph's binding. `graph_id` is the authority field that decides the worker's tool roster. |
| [shell.py](file:///Users/alvac/aoc/graphs/coding/utils/shell.py) | One implementation of "run a command in a worktree", returning `(exit_code, stdout, stderr)` and never raising. Shared by setup and verify so they cannot disagree on what a failure means. |
| [digest.py](file:///Users/alvac/aoc/graphs/coding/utils/digest.py) | SHA-256 over `git diff HEAD` plus sorted `git status --porcelain`, so "already implemented" and "already verified" are claims with evidence. `None` means *no evidence* and must be treated as a cache miss. |
| [preflight.py](file:///Users/alvac/aoc/graphs/coding/utils/preflight.py) | Checks, before any token is spent, that the worker really receives the tools `graph.json` grants it and that the push identity can push. Resolves the roster through the same `worker_session` factory the real call uses. |
| [token_opt.py](file:///Users/alvac/aoc/graphs/coding/utils/token_opt.py) | Truncates tracebacks (head + tail) and diffs before they enter a prompt — the failing case produces the longest output and is exactly when the spec risks being crowded out. |
| [xml_parsers.py](file:///Users/alvac/aoc/graphs/coding/utils/xml_parsers.py) | Parses the `<worker_handoff>`, `<critic_verdict>` and `<spec_validation_result>` blocks. Verdicts match **exactly**, never as substrings, so an echoed placeholder reads as FAIL/REJECT. |

## Entry & Exit

### `prepare_input()`

[adapters.py:L8-L114](file:///Users/alvac/aoc/graphs/coding/adapters.py#L8-L114)

Translates an incoming text query plus kwargs into the initial `CodingState`. It:

- prefixes the query with `<caller>…</caller>` when a caller is supplied;
- resolves `project_name`, `project_path`, `build_request_path` and `target_repo` from kwargs
  first, then by regex over the query text; `project_path` defaults to
  `pkm/wiki/software/<project>` and `build_request_path` to that project's `build_request.json`;
- **requires a manifest.** If none can be resolved it does not raise — it returns an
  `error_message` telling the caller exactly what to pass. Which manifest is the one required
  input, the way `topic` is for content creation: without it a run has no queue, and guessing one
  means working through some other project's tasks;
- treats a chat nudge containing approve/lgtm/yes/revise/abort/cancel/proceed as
  `latest_human_feedback` — **not** as an approval, which is read only off GitHub;
- loads `graph_id` and `required_tools` from `graph.json`, deriving the tool requirement from the
  grant itself so there is no second list that can drift;
- loads reviewers, approval/rejection signals and the repo descriptor from the manifest,
  read-only and failure-tolerant (a missing manifest simply means no reviewers configured, and the
  scheduler reports the empty queue);
- seeds `tick_report`, `tick_handled`, `completed_tasks`, `failed_tasks` and `messages`.

### `format_output()`

[adapters.py:L164-L177](file:///Users/alvac/aoc/graphs/coding/adapters.py#L164-L177), delegating to
[`format_tick_report()`](file:///Users/alvac/aoc/graphs/coding/adapters.py#L151-L161)

Renders `error_message` as `🛑 Coding tick error: …` if set, otherwise joins the non-empty
`tick_report` lines.

> [!NOTE]
> **Empty output is meaningful, not a bug.** The scheduled runner treats empty stdout as "post
> nothing", which is what keeps a channel usable when the queue is idle 287 times out of 288 a day.

### `create_graph(checkpointer=None, **kwargs)`

[graph.py:L22-L83](file:///Users/alvac/aoc/graphs/coding/graph.py#L22-L83)

The loader hands every graph a checkpointer
([graph_builder.py:L137-L144](file:///Users/alvac/aoc/core/agent/graph_builder.py#L137-L144)), so
the parameter is accepted — but it is **deliberately ignored**, and the workflow always compiles
with `checkpointer=None`. It is not silently honoured, because a checkpoint would key durable state
to the caller's session again, which is exactly the resume problem this design removed. Durable
state lives in the manifest, git and GitHub instead.
[test_graph.py:L19-L44](file:///Users/alvac/aoc/tests/graphs/coding/test_graph.py#L19-L44) asserts
both that `graph.checkpointer is None` and that a supplied `MemorySaver` is refused.

### How a tick is driven

- **Scheduled:** [scripts/coding_tick.py](file:///Users/alvac/aoc/scripts/coding_tick.py) is run by
  the `script-executor` cron every five minutes. It discovers every project manifest, calls
  `create_graph().ainvoke(prepare_input(...))` once per `--max-tasks`, and prints only the tick
  report — the runtime's own chatter is captured and discarded so it does not become the message.
- **Interactive:** [tools/graph_call.py](file:///Users/alvac/aoc/tools/graph_call.py) invokes the
  graph through the loader's registered `prepare_input`. The `coding` graph belongs to
  `software-planner` ([agents/main/AGENTS.md](file:///Users/alvac/aoc/agents/main/AGENTS.md#L31)).
- **Operator:** [scripts/coding_admin.py](file:///Users/alvac/aoc/scripts/coding_admin.py) and
  [tools/graph_status.py](file:///Users/alvac/aoc/tools/graph_status.py) go through
  `utils/control.py` — manifest writes only, never an execution path of their own.

## Files

| Path | Description |
|---|---|
| [graph.py](file:///Users/alvac/aoc/graphs/coding/graph.py) | The topology: six nodes, the `_router` factory, and `create_graph()` compiling without a checkpointer. |
| [schemas.py](file:///Users/alvac/aoc/graphs/coding/schemas.py) | `CodingState`, `TaskEnvelope`, `TaskError`, `RepoDescriptor`, and the status/stage vocabularies. |
| [adapters.py](file:///Users/alvac/aoc/graphs/coding/adapters.py) | `prepare_input()`, `format_tick_report()`, `format_output()`, and the `graph.json`/manifest settings readers. |
| [graph.json](file:///Users/alvac/aoc/graphs/coding/graph.json) | Graph id, description, and the `tools` grant (`bash` and `filesystem`, scoped to `workspaces/runs` and `pkm/wiki/software`) that preflight asserts against. |
| [__init__.py](file:///Users/alvac/aoc/graphs/coding/__init__.py) | Package marker. |
| [nodes/](file:///Users/alvac/aoc/graphs/coding/nodes/__init__.py) | The six stages of a tick, re-exported in execution order. |
| [nodes/scheduler.py](file:///Users/alvac/aoc/graphs/coding/nodes/scheduler.py) | Reclaim, preflight, select, claim, provision, set up, and route by stage. |
| [nodes/implement.py](file:///Users/alvac/aoc/graphs/coding/nodes/implement.py) | The only node that calls the LLM to write code. |
| [nodes/verify.py](file:///Users/alvac/aoc/graphs/coding/nodes/verify.py) | Runs the verification command; classifies environment vs. verification failure. |
| [nodes/audit.py](file:///Users/alvac/aoc/graphs/coding/nodes/audit.py) | Advisory anti-pattern review of the diff; always continues to publish. |
| [nodes/publish.py](file:///Users/alvac/aoc/graphs/coding/nodes/publish.py) | Commit, push, PR upsert, review-context comment. Deterministic, idempotent, no LLM. |
| [nodes/sync_review.py](file:///Users/alvac/aoc/graphs/coding/nodes/sync_review.py) | Reads the decision off GitHub, merges or sends work back, tears down. |
| [utils/](file:///Users/alvac/aoc/graphs/coding/utils/__init__.py) | Support modules — see [Utilities](#utilities). |
| [prompts/](file:///Users/alvac/aoc/graphs/coding/prompts/__init__.py) | Prompt builders for the Goldfish workers. |
| [prompts/coder_prompt.py](file:///Users/alvac/aoc/graphs/coding/prompts/coder_prompt.py) | Coder worker prompt: execution boundary, allowed files, acceptance criteria, plus targeted retry blocks for test output, critic feedback and human feedback. |
| [prompts/critic_prompt.py](file:///Users/alvac/aoc/graphs/coding/prompts/critic_prompt.py) | Critic prompt: spec + diff against the four-item anti-pattern checklist. |
| [prompts/spec_validator_prompt.py](file:///Users/alvac/aoc/graphs/coding/prompts/spec_validator_prompt.py) | Spec validator prompt. Not used by any node — consumed by [tools/spec_validator.py](file:///Users/alvac/aoc/tools/spec_validator.py) before a task is queued. |

Tests live in [tests/graphs/coding/](file:///Users/alvac/aoc/tests/graphs/coding/) — one module per
node and per utility, plus
[test_graph.py](file:///Users/alvac/aoc/tests/graphs/coding/test_graph.py), which drives the
*compiled* graph end to end. Only a compiled run catches a route that no declared channel carries,
and it also enforces that no node both calls the LLM and mutates a remote.

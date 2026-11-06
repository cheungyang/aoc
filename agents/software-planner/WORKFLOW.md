# software-planner: how its code works

## coding_attention

The scan behind the `coding-queue-triage` schedule. It reads every project's
`pkm/wiki/software/<project>/build_request.json` and wakes Elephant only when
the coding queues need his decision. It runs only on the host that ticks: only
that host's manifests are current.

What needs Elephant, by kind:

- `halt`: a task the graph halted. Its last error's kind and stage say why.
- `review_budget`: a halt with review feedback still pending. The review
  arrived after the implement budget was spent, so the feedback was never
  worked on.
- `tick_error`: ticks stop before they claim any task: a preflight that
  fails, a repository that can't be resolved. The coding scheduler records it
  at the manifest's top level.
- `deploy_failed`: the project's deployment failed — the deploy itself, its
  `deploy` block, or no answer before its timeout. The deploy node records it
  at the manifest's top level, in `deployment`, until a merge or a `redeploy`
  asks again.
- `review_dormant`: a PR awaiting review that the tick stopped checking. A
  reminder falls due `remind_after_hours` after the tick stopped, then one
  every `remind_after_hours` (0: never).

Done tasks, tasks you failed or skipped yourself, work the graph is still
retrying, and a deployment that is pending or live never wake him.

Each item has an event time: when it happened. It's new when that's later
than Elephant's last successful check. The scan fires only for new items, so a
handled halt never wakes him twice. A turn that failed leaves its items new
for the next run.

A task halted with review feedback pending is a `review_budget`, and it's new
until a successful check after its halt:

```python
>>> from agents.software_planner.lib.coding_queue import queue_items
>>> rules = {"remind_after_hours": 24, "max_fixes_per_task": 2}
>>> search = {"task_id": "feat_02_search", "status": "halted", "stage": "provisioned",
...           "attempts": {"implement": 3}, "review_feedback": ["@reviewer: sort the results by date"],
...           "last_error": {"kind": "llm", "stage": "implement", "at": 1000.0,
...                          "message": "`feat_02_search` exhausted its implement budget (3/3)."}}
>>> [item] = queue_items({"queue": [search]}, "recipe-box", now=8200.0, rules=rules)
>>> item.kind, item.event_at
('review_budget', 1000.0)
>>> item.is_new(last_success_at=900.0), item.is_new(last_success_at=1200.0)
(True, False)
```

Each halted task also says how many fixes Elephant has made to it, from its
`planner_fixes`. At `max_fixes_per_task` the finding says the limit is
reached:

```python
>>> fixed_twice = dict(search, planner_fixes={"count": 2, "log": ["...", "..."]})
>>> queue_items({"queue": [fixed_twice]}, "recipe-box", now=8200.0, rules=rules)[0].at_fix_limit
True
```

A tick error is read from the manifest's top level:

```python
>>> manifest = {"tick_error": {"stage": "preflight", "at": 2000.0,
...                            "message": "the push identity can't push to example/recipe-box"},
...             "queue": []}
>>> [(item.kind, item.stage, item.event_at) for item in queue_items(manifest, "recipe-box", now=8200.0, rules=rules)]
[('tick_error', 'preflight', 2000.0)]
```

So is a failed deployment. Its stage is the backend, and the finding carries
the log to read first:

```python
>>> manifest = {"deployment": {"status": "failed", "backend": "github_pages",
...                            "log_url": "https://github.com/example/recipe-box/actions/runs/42",
...                            "last_error": {"stage": "deploy", "kind": "deploy", "at": 3000.0,
...                                           "message": "deploying `4f2a9c1` to GitHub Pages failed: build failed."}},
...             "queue": []}
>>> [deploy] = queue_items(manifest, "recipe-box", now=8200.0, rules=rules)
>>> deploy.kind, deploy.stage, deploy.event_at
('deploy_failed', 'github_pages', 3000.0)
>>> deploy.details(now=8200.0)[-1]
'log: https://github.com/example/recipe-box/actions/runs/42'
```

The tick stopped checking a PR at hour 0. With `remind_after_hours` 24,
nothing is due at hour 20, and at hour 50 the latest reminder fell due at
hour 48:

```python
>>> from agents.software_planner.lib.coding_queue import dormant_event
>>> hour = 3600
>>> print(dormant_event(poll_until=0, now=20 * hour, every_hours=24))
None
>>> dormant_event(poll_until=0, now=50 * hour, every_hours=24) / hour
48.0
```

Every item names the queued tasks that can't start until it's done: its
dependents, theirs, and so on.

```python
>>> from agents.software_planner.lib.coding_queue import waiting_behind
>>> queue = [{"task_id": "feat_01_data", "status": "awaiting_review"},
...          {"task_id": "feat_02_list", "status": "queued", "dependencies": ["feat_01_data"]},
...          {"task_id": "feat_03_page", "status": "queued", "dependencies": ["feat_02_list"]}]
>>> waiting_behind(queue, "feat_01_data")
('feat_02_list', 'feat_03_page')
```

The findings carry each new item in full, then the still-open ones in a line
each:

```python
>>> from agents.software_planner.lib.coding_queue import findings
>>> print(findings([item], [], now=8200.0))
New since your last check (1):
1. recipe-box/feat_02_search · review_budget
   halted at stage provisioned 2 h ago · attempts: implement 3
   last error (llm at implement): `feat_02_search` exhausted its implement budget (3/3).
   review feedback still pending:
   - @reviewer: sort the results by date
   fixes so far: 0 of 2
```

A waiting PR's finding also gives the `poll_until` value that would reopen
the tick's check for an hour, so no one has to compute a timestamp.

What it can't read is an issue. On a quiet run the scheduler posts it in
#software-dev, once per issue per day. On a run with work it rides along in
the findings: for example, a manifest that isn't valid JSON.

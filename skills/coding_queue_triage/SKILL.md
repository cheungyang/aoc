---
name: coding_queue_triage
description: Triages what the coding queues need from you, from the coding_attention findings: a halted task, ticks that stop before claiming any task, a failed deployment, or a PR the tick stopped checking. Prepares the fix that is yours to make, posts it with the one decision that is the user's, and applies it when they say go.
---
## When to Use
Your `coding-queue-triage` schedule runs with `<findings source="coding_attention">`: the items new since your last successful check, then the ones still open. Also when the user replies to one of those posts, or asks what happened to a coding task.

## Workflow
1. Work from the findings. They carry each item's last error, pending review feedback, PR, spec, the tasks waiting behind it, and your fixes so far. Read only what an item names: its spec, and `graph_status(graph_name="coding")` when you need more of the task than the findings give. Don't re-scan the queues.
2. Find each new item's fix in the table below, by its kind and its last error's kind (`llm at implement` is kind `llm`). An item at its fix limit (the finding says "fix limit reached") goes to the user with what you tried, whatever its kind.
3. Prepare the fix. A changed spec is a new version beside the old one (`Search_v2.md` after `Search_v1.md`), never an overwrite: write it to `pkm/wiki/software/<project>/specs/`, then run `spec_validator` on it.
4. Change nothing else from a scheduled run. Applying the fix is the user's call: post it and ask. When they say go, apply it as in Applying a Fix.
5. Post one message for the whole run (The Message).

| Kind · last error | The fix you prepare | Hand it to the user when |
|---|---|---|
| `review_budget` | Fold the pending review feedback into a new spec version, then re-queue the task | the feedback conflicts with what the spec is for |
| `halt` · `verification` (its tests failed 3 times; the output is in the last error) | In a new spec version: tighten the acceptance criteria, add the `dependencies` the code imports from (the most common cause), fix `allowed_files`, or split the task. Then re-queue | the failure shows the design itself is wrong |
| `halt` · `llm` (its budget ran out, no feedback) | Same as `verification`. The reason may have been overwritten, so read the PR or branch first | same as `verification` |
| `halt` · `environment` | Fix `setup_command` or `verification_command`, then re-queue. `setup_command` stays idempotent, non-interactive and install-only: a setup that "changed or created files that are not ignored" in the E2B sandbox scaffolds or rewrites `package.json`, so make it `npm ci` (or the project's equivalent) and commit what it generated. `verification_command` stays a pure test run, with no install in it. Never change the spec: the code isn't what failed | it needs secrets, system packages, more memory (exit 137) or a sandbox template |
| `halt` · `config` | Fix the manifest field, such as a missing `verification_command`, then re-queue | it needs a tool or permission grant, the notes are missing, it pushed to the default branch, or "the `e2b` verify backend isn't set up on this host" (the host needs its E2B key: `scripts/coding_e2b_check.py` says what's missing) |
| `halt` · `privacy` | A new spec version that requires made-up data only, then re-queue once | it's the task's second privacy halt |
| `halt` · `git`, `github`, `infra` | Re-queue once, if it reads as transient | otherwise always: you can't fix infrastructure |
| `tick_error` | Nothing you can fix: name the cause in one line (credentials, repository access, the tool roster) | always |
| `deploy_failed` (the project's deployment; its error kind is `deploy`, `config` or `infra`) | Read the log link. A build or test failure in the deploy workflow is a code problem: prepare a spec for a fix task, to queue when the user says go. A `config` error is a manifest field: prepare the `deploy` block change. The next merge deploys again | the workflow, the Pages settings or the hosting needs changing, or it's `infra`: always. Tell the user `scripts/coding_admin.py --project <project> redeploy` checks it again once fixed |
| `review_dormant` | Remind the user: the PR, how long it has waited, what waits behind it. Ask them to tell you once they've reviewed it on GitHub | never: the reminder is the message |

## Applying a Fix
Only after the user says go:
- Only a task whose `status` is `halted`, or `awaiting_review` for a PR the user has reviewed. Never one with a `lease_owner`: the tick is working on it.
- One `filesystem` `replace_block` on that task's entry in `pkm/wiki/software/<project>/build_request.json`. Read the entry first and copy the lines you change exactly. If the tick changed them since, the block won't match and the edit fails: read it again.
  - To re-queue: `"status": "queued"`, `"attempts": {}`, `"last_error": null` (what a retry through `scripts/coding_admin.py` writes), plus what the fix changes: `spec_path`, `setup_command`, `verification_command`, `allowed_files`, `dependencies`. In the same edit, add one to `planner_fixes`: `{"count": 1, "log": ["2026-11-03 17:10 · folded the review into spec v2, re-queued"]}`.
  - For a PR the user has reviewed: `"poll_until": null`, so the next tick reads their decision off GitHub.
- For a `deploy_failed` `config` error: one `replace_block` on the manifest's top-level `deploy` block. Never edit `deployment`: the graph owns it, and the next merge or the user's `redeploy` asks again.
- Never `overwrite` `build_request.json`: rewriting the whole file from an earlier read loses the tick's writes.
- Everything else is the user's, through `scripts/coding_admin.py`: rewinding a task to an earlier stage, reset, abort, skip and redeploy.

## The Message
One message per run, never one per item:
- one line per new item: `project/task`, what happened in plain words, and the fix you prepared (the new spec version, or the exact field and value);
- then the one decision you need, as a single question, or a poll when there are options. If nothing needs the user, say so in the last line;
- no JSON or manifest dumps.

## Rules
- Never approve, merge, close or comment on a PR: GitHub is the only approval surface, and approving is the user's.
- Never copy lines the privacy check flagged into a spec: specs reach the worker, and its summary becomes a public PR body.
- You still write no code: every fix is a spec or a manifest change.

## Example
Findings: `recipe-box/feat_02_search · review_budget`, halted at stage provisioned, attempts implement 3, review feedback "@reviewer: sort the results by date", fixes so far 0 of 2. You read `Search_v1.md`, write `Search_v2.md` with the sort rule and an acceptance criterion for it, and `spec_validator` passes. You post: "recipe-box/feat_02_search: the review came in after its 3 attempts were used up, so I folded 'sort by date' into Search_v2.md. Want me to point the task at v2 and re-queue it?" The user says go: one `replace_block` sets its `spec_path` to Search_v2.md, `status` to queued, `attempts` to {} and `last_error` to null, and `planner_fixes` to a count of 1.

## Required Tools
- `filesystem`: to read specs and the manifest, write a new spec version, and apply a fix with `replace_block`, in `pkm/wiki/software/`.
- `spec_validator`: to check a new spec version before you recommend it.
- `graph_status`: to read a task in more detail than the findings give.

---
name: dream
description: Skill for consolidating daily memory logs into shared and private memory entries, returning entry operations as IPC XML.
---
## Overview
The dream skill is a daily consolidation routine. The dream standup hands you, inside a `<dream_input>` block, your unprocessed daily memory logs and every memory entry you may act on:
- the shared **Profile** (`tag="profile"`), which every agent sees;
- every shared **topic** (`food`, `travel`, …), each seen by the agents subscribed to it;
- your own **private** memory (`tag="private"`) and **feedback** (`tag="feedback"`).

You decide what is worth remembering, how to phrase it and which tag fits. You reply with **operations on entries**; the standup script validates them, routes each by its tag, enforces the size budgets, archives what leaves and deletes the processed logs. You never touch the filesystem. Everything you keep is injected into system prompts on every turn, so it must stay fiercely concise and actionable.

## When to Use
This skill is triggered EXCLUSIVELY by the dream standup. Do not trigger this organically during standard conversations.

## Boundaries & Guardrails
- **No Tools**: Everything you need is in `<dream_input>`. Do not call `filesystem` or any other tool, and do not emit a `<system_memory_log>`.
- **Refer to Entries by Id**: Each `<entry>` has an `id` (`e7`). To keep, change or remove an existing entry, use its id. Ids exist only for this reply.
- **Add Only What Is New**: Use `add` only for a fact **no entry already covers** — in any topic, not just yours. If an entry already says it, `confirm` it; if it's partly wrong or outdated, `update` it. Two entries for one fact is a defect.
- **Tags Come From the List**: Choose the tag for an `add` from `<tags>`. Never invent one. If nothing fits, use `private`.
- **Profile Is for Stable Facts Only**: `profile` reaches every agent. Use it only for durable facts about the user (identity, family, location, timezone, health constraints, standing preferences). Never put a current situation, an event or anything sensitive and temporary there — at most two Profile changes per dream are applied.
- **Feedback Is for Corrections Only**: `feedback` is a rule the user told you to follow or a correction of your behaviour. Facts about the user's work or life are not feedback.
- **Time-Bound Facts Need `until`**: Anything describing an event or a current situation (a trip, a visit, a leave, "this quarter's focus") MUST carry `until="YYYY-MM-DD"`, the date after which it no longer applies. It then expires by itself.
- **Retire What No Longer Applies**: Retire an entry when it describes a dated event that has passed, a transient state whose date has moved on, a one-time correction that is now simply how things are, or when a newer entry supersedes it. Give a short `reason`.
- **Review Stale Entries**: Entries marked `stale="true"` haven't been confirmed for 90 days. Confirm each one that is still true, retire each one that isn't. An entry you leave unmentioned is kept.
- **Conciseness is Critical**: One sentence per fact, straight to the point. No conversational filler.
- **Actionable Extractions**: Only record what improves future decisions or personalisation. Ignore routine operations and standard task completions.
- **Formatting**: The reply MUST be only the XML below. YOU MUST NOT OUTPUT CONVERSATIONAL TEXT outside it.

## Workflow

### 1. Read the Input
`<dream_input>` holds `<tags>` (every tag you may use, with what it covers), `<entries>` (`<entry id="…" tag="…">fact</entry>`) and `<memory_logs>` (one `<log name="YYYY-MM-DD.md">` per daily log).

### 2. Extract & Decide
For each thing in the logs worth keeping:
- **Learnings & precedents** about how to do your job → `private`. Only *what/how* made a task succeed, *why* something failed, or a decision that serves as a precedent.
- **Behavioural corrections** from the user → `feedback`, phrased as a direct command.
- **Facts about the user** → the topic that fits, or `profile` if every agent needs it.

Check `<entries>` first: confirm or update an existing entry rather than adding a new one.

### 3. Reply (IPC Format)
- **Dreamed**: at least one operation.
- **No new memories**: nothing in the logs is worth keeping and no entry needs to change. Send an empty `<ops/>`. The logs are still deleted.

```xml
<dream_response>
  <status>Dreamed</status>
  <ops>
    <add tag="food">Avoiding dairy; oat milk is fine.</add>
    <add tag="family" until="2026-12-05">Parents visiting until early December.</add>
    <confirm id="e7"/>
    <update id="e3">Runs 3x/week, mornings.</update>
    <retire id="e9" reason="trip ended"/>
  </ops>
  <errors>None</errors>
  <learnings>One concise line to show in the daily standup.</learnings>
</dream_response>
```

Write real content, never the bracketed descriptions of a template: an operation whose text is `[...]` is rejected as an echoed placeholder.
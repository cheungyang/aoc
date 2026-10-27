# INSTRUCTIONS: Wiki Workflows

You have two primary scheduled workflows. You must strictly follow the workflow that matches your trigger prompt.

## WORKFLOW A: Daily Ingestion & Gap Analysis (Daily at 7:00 AM)

### Phase 1: The Daily Weave (Autonomous)
1. **Check Inbox:** Scan `pkm/inbox/` for new articles. If empty, terminate the run immediately without notifying the user.
2. **Ingest & Map (Crucial):** If populated, use the `wiki_ingest` skill to process the notes. 
3. **Mechanical Linking:** You MUST follow the `wiki_query` skill to search the vault for concepts related to the ingested article. Extract the exact file paths from the `wiki_query` results. Then, rewrite the ingested text to embed standard Markdown links (e.g., `[Concept Name](pkm/wiki/concept.md)`) to connect the new article to the existing graph. Move the finished file to `pkm/wiki/`.

### Phase 2: Summary & Gap Check
After completing Phase 1, you must STOP processing files and message the user in `#pkm-wiki` with a structured synthesis:
1. **Summary:** Briefly explain what you ingested and the links you created.
2. **Priority Alignment:** Explain how this new information connects to the user's current priorities (based on the Profile and topics in your prompt).
3. **Knowledge Gap Identification:** Name each missing concept plainly, with why it matters. E.g., *"This article relies heavily on concept [X], which is missing from our graph."* Don't offer to research it or to pass it on: your teammates pick gaps up on their own.

### Phase 3: Feedback (When the User Replies)
- **Proactive Learning (Memory Trigger):** If the user reveals new interests, shifts in priorities, or corrects your assumptions about their goals, you MUST record this in a `<system_memory_log>` block in that same reply. Do not forget this step.

---

## WORKFLOW B: Weekly Linting & Taxonomy Cleanup (Fridays at 10:00 AM)

1. **Check Queue:** Review `pkm/wiki/pending_lint.json`.
2. **Evaluate:** Use the `wiki_lint` skill to analyze structural anomalies (e.g., orphan notes, monolithic pages needing a split).
3. **Propose Agenda:** Ping the user in `#pkm-wiki` with a structured agenda of cleanup tasks. Propose splits for overly dense pages to optimize vector retrieval.
4. **Execute:** Wait for the user's approval before modifying any files.
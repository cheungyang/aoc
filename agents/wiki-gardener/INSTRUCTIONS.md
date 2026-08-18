# INSTRUCTIONS: Wiki Workflows

You have two primary scheduled workflows. You must strictly follow the workflow that matches your trigger prompt.

## WORKFLOW A: Daily Ingestion & Gap Analysis (Daily at 7:00 AM)

### Phase 1: The Daily Weave (Autonomous)
1. **Check Inbox:** Scan `pkm/inbox/` for new articles. If empty, terminate the run immediately without notifying the user.
2. **Ingest & Map (Crucial):** If populated, use the `wiki_ingest` skill to process the notes. 
3. **Mechanical Linking:** You MUST use the `wiki_query` skill to search the vault for concepts related to the ingested article. Extract the exact file paths from the `wiki_query` results. Then, rewrite the ingested text to embed standard Markdown links (e.g., `[Concept Name](pkm/wiki/concept.md)`) to connect the new article to the existing graph. Move the finished file to `pkm/wiki/`.

### Phase 2: The Sparring Prompt (Interactive Hold Point)
After completing Phase 1, you must STOP processing files and message the user in `#pkm-wiki` with a structured synthesis:
1. **Summary:** Briefly explain what you ingested and the links you created.
2. **Priority Alignment:** Explain how this new information connects to the user's current priorities (based on your `CONTEXT.md`).
3. **Knowledge Gap Identification:** Highlight missing context. E.g., *"This article relies heavily on concept [X], which is missing from our graph."*
4. **The Question (Strict Hold Point):** Ask the user: *"Shall I queue a request for the `topic-researcher` to investigate [X]?"* Wait for their reply.

### Phase 3: Feedback & Delegation (Post-Approval)
Once the user replies:
- **Proactive Learning (Memory Trigger):** If the user reveals new interests, shifts in priorities, or corrects your assumptions about their goals, you MUST immediately load and execute the `memory` skill to record this into your context log. Do not forget this step.
- **Delegation:** If they approve the research gap, format a structured XML IPC payload summarizing the required research, and use the `filesystem` tool to append it to `pkm/wiki/research_requests.md`.
- **Completion:** Acknowledge the delegation and close out the session.

---

## WORKFLOW B: Weekly Linting & Taxonomy Cleanup (Fridays at 10:00 AM)

1. **Check Queue:** Review `pkm/wiki/pending_lint.json`.
2. **Evaluate:** Use the `wiki_lint` skill to analyze structural anomalies (e.g., orphan notes, monolithic pages needing a split).
3. **Propose Agenda:** Ping the user in `#pkm-wiki` with a structured agenda of cleanup tasks. Propose splits for overly dense pages to optimize vector retrieval.
4. **Execute:** Wait for the user's approval before modifying any files.
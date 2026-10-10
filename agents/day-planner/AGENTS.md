# AGENTS.md

## Daily Morning Workflow

You must execute your morning routine in this strict, paced order, waiting for the user's response between phases.

### Phase 1: The Energy & Reflection Check
1. **Data Gathering**: 
   - Use the `filesystem` tool to read yesterday's daily journal (e.g., `pkm/vault/journals/daily/YYYY-MM-DD.md`) to find fleeting notes.
   - Use the `filesystem` tool to read the current week's journal (e.g., `pkm/vault/journals/weekly/YYYY-Www.md`) to understand the overarching weekly theme and goals.
2. **The Check-In**: 
   - Ask the user for their current energy level (e.g., 1-10 or qualitative).
   - Formulate a deep, thought-provoking question that connects yesterday's fleeting notes to the current week's theme. 
3. **Wait for the user's response.**

### Phase 2: The Intention Check
1. Ask the user: "What are your intentions for today? For example, how do you want to show up? (e.g., as an efficient leader, a present husband, to conquer Project A?)"
2. **Wait for the user's response.**

### Phase 3: Task & Inbox Presentation (Literal, Grounded, & Justified)
1. **Gather Task Data**:
   - Execute the `list_impactful_actions` skill to retrieve important, needle-moving project work.
   - Execute the `check_recurring_chores` skill to identify flagged administrative duties.
2. **Execute Inbox Triage**:
   - Execute the `highlight_actionable_emails` skill to retrieve a clean, curated summary of actionable, critical, and VIP emails from the last 3 days.
3. **Present the Triaged List**:
   Synthesize the data and present the user with a realistic slate of tasks, categorized strictly into:
   - **a) Urgent Matters**: Tasks that are due today/soon and carry a High/Highest priority (🔺, ⏫).
   - **b) Important Matters**: The top needle-moving tasks from active projects.
   - **c) Inbox Triage**: The summarized list of emails retrieved from `highlight_actionable_emails` (including why they were flagged).
   - **d) Administrative / Chores**: The flagged items from `check_recurring_chores`.
4. **The "Why"**: For every task presented, explicitly state *why* it is being recommended. Connect the rationale back to the user's stated energy level, today's intentions, and the objective priority data. 
5. **Crucial Rule**: List the tasks literally. Do not hallucinate aspirational goals.

### Phase 4: Coaching & Nudging
1. Ask the user which of the presented tasks they commit to tackling today.
2. **Wait for the user's response.**
3. Review their selection. If they are ignoring urgent/high-priority items in favor of low-impact work, gently nudge them by providing constructive feedback on how their time might be better spent. Do not aggressively debate; offer the observation as a supportive coach, and accept their final decision.

### Phase 5: Capacity & Time Estimation Plan
1. Check the current day of the week. Factor in the user's schedule: Mondays, Wednesdays, and Thursdays are packed with meetings, meaning less time for deep work. Tuesdays and Fridays are more open.
2. Evaluate the tasks they committed to against their "50/50 plan" and their daily meeting capacity. 
3. If the day is tight, explicitly advise them to scale back. If a task is too large, suggest how to break it down into smaller, actionable sub-tasks instead of just partially committing to it. Ensure they are set up for a realistic win rather than inevitable failure. Do NOT recommend or suggest using the `- [/]` syntax.
4. **WIP Signal (`- [/]`)**: If the user explicitly uses the `- [/]` syntax in their reply, understand this as a signal that they are *actively* working on that item right now.
5. **Wait for final confirmation of the adjusted, time-estimated plan.**

### Phase 6: Daily Summary Logging
- Once the final, time-estimated plan is agreed upon, load and execute the `write_daily_intention` skill to log the final morning reflection, energy level, intentions, and agreed-upon tasks (preserving any `- [/]` syntax the user provided) into today's journal entry.
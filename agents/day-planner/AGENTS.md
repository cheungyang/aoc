# AGENTS.md

## Daily Workflow

### 1. Execute `list_impactful_actions`
- Load and execute the `list_impactful_actions` skill to scan the user's PKM vault. 
- This will retrieve categorized, highly impactful projects and uncompleted tasks based on current urgency and the weekly focus.

### 2. Execute `check_recurring_chores` (Administrative Check)
- Load and execute the `check_recurring_chores` skill.
- This will analyze pending recurring tasks against the user's historical behavioral cadence and flag any chores where the user is behind their usual schedule or approaching the hard deadline.

### 3. The Suggestion Phase & Administrative Reminders
- Based on the data returned from Step 1, synthesize and list exactly **5 overall impactful things** for the user to execute today (pulling the best items across all buckets).
- Provide a brief, motivational case for *why* these 5 move the needle.
- **Administrative Reminders**: If Step 2 returned any flagged time-sensitive chores, append them at the very end of your suggestion brief under a distinct `### Administrative Reminders` header. Do NOT debate these chores; simply inform the user as a soft signal.

### 4. The Intention Inquiry & Feedback Loop
- Ask the user: "What are your intentions and goals for today?"
- **Wait for response.**
- *Continuous Learning*: If the user provides comments on why certain tasks or projects are more or less impactful than others, you MUST load and execute the `memory` skill to log this preference.

### 5. Critique & Debate
- Compare the user's intentions to your Top 5 recommendations.
- Respectfully challenge the user if they ignore critical high-priority items in favor of low-impact work. (Note: Do not debate the administrative reminders).
- Reach a final agreement on the day's plan.

### 6. Daily Summary Logging
- Once the final plan is agreed upon, load and execute the `write_daily_intention` skill to log the final result.
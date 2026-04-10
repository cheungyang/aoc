---
name: write_daily_intention
description: Skill for generating the user's daily journal and logging the agreed-upon daily intentions and recommendations.
---
## Overview
This skill handles the creation and updating of the user's daily fleeting journal. It ensures the PKM vault is synced, initializes the daily note from a template if needed (resolving dynamic syntax), and appends the agent's recommended daily plan.

## Workflow

### Step 0: Sync Remote (Pre-Flight)
Always ensure the local vault is up to date before making changes.
- Use the `git` tool to perform a `pull` action on the `pkm` repository.

### Step 1: Check & Initialize Daily Note
Determine if today's note already exists.
- **Target Path**: `vault/journals/fleeting/YYYY-MM-DD.md` (Replace YYYY-MM-DD with today's date).
- Use the `obsidian` tool (`read` action) to check if the file exists.
- **If the file does NOT exist:**
  1. Use the `obsidian` tool (`read` action) to load the template located at `templates/"Fleeting Pages".md`.
  2. **Templater Resolution**: You must manually parse the template text and replace ALL Obsidian Templater syntax (`<% tp... %>`) with the correct static values. 
     - *Example:* Replace `<% tp.date.now("w")-1 %>` with the integer representing last week's ISO week number.
     - Replace any date/title syntax with today's actual date.
  3. Use the `obsidian` tool (`write` action) to save the fully resolved text into the target path (`vault/journals/fleeting/YYYY-MM-DD.md`).

### Step 2: Append Recommendations
Add the day's plan to the journal.
- Format the agreed-upon daily intention and the agent's top recommendations cleanly.
- Prepend your formatted text with the header: `#### Agent's recommendation`. 
- Ensure you include empty newlines before the header so it renders cleanly in markdown.
- Use the `obsidian` tool (`append` action) to add this block to the very bottom of the `YYYY-MM-DD.md` file.

### Step 3: Sync Remote (Post-Flight)
Upload the new journal entries back to the remote repository.
- Use the `git` tool to perform a `add` action on the newly created file, then a `push` action on the `pkm` repository with a message.

## Required Tools
- `git`: Required to `pull`, `add`, and `push` the `pkm` repository to maintain sync and prevent merge conflicts.
- `obsidian`: Required to `read` templates, `write` new daily notes, and `append` recommendations within the `pkm` vault.
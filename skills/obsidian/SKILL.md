---
name: obsidian
description: Skill for understanding how to use the pkm-oc vault with Obsidian operations.
---
## Overview
This skill guides agents on how to use the `pkm-oc` vault to access memory and feedback using the `obsidian` tool.
The tool requires `vault_id` (always`"pkm-oc"` for agent memory and feedback) and `agent_id` for permission verification.

## Workflow

### 1. Read Consolidated Memory
To read the consolidated memory for an agent, read the file at `agents/<agent_name>/memory.md`.

**Command:**
`obsidian(action="read", vault_id="pkm-oc", path="agents/<agent_name>/memory.md", agent_id="<agent_id>")`

### 2. Read Daily Memory
Daily memory files are located in the `agents/<agent_name>/memory` directory and are prefixed by the date (e.g., `YYYY-MM-DD`).
Instead of exploring files, the agent should search for the file using the date as a filter.

**Workflow:**
1. List files and filter by date:
   `obsidian(action="search", vault_id="pkm-oc", path="agents/<agent_name>/memory", args="<date>", agent_id="<agent_id>")`
2. Locate the filename from the search results.
3. Read the file using:
   `obsidian(action="read", vault_id="pkm-oc", path="agents/<agent_name>/memory/<filename>", agent_id="<agent_id>")`

### 3. Read Feedback
Feedback follows a similar pattern to memory.

#### Consolidated Feedback
Read consolidated feedback from human at `agents/<agent_name>/feedback.md`.

**Command:**
`obsidian(action="read", vault_id="pkm-oc", path="agents/<agent_name>/feedback.md", agent_id="<agent_id>")`

#### Daily Feedback
Read daily feedback from the `agents/<agent_name>/feedback` directory.
1. Search/List files filtered by date:
   `obsidian(action="search", vault_id="pkm-oc", path="agents/<agent_name>/feedback", args="<date>", agent_id="<agent_id>")`
2. Locate the filename from the search results.
3. Read the file using:
   `obsidian(action="read", vault_id="pkm-oc", path="agents/<agent_name>/feedback/<filename>", agent_id="<agent_id>")`

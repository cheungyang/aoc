---
name: obsidian
description: Skill for understanding how to use the pkm-oc vault with Obsidian commands.
---
## Overview
This skill guides agents on how to use the `pkm-oc` vault to access memory and feedback.

## Workflow

### 1. Read Consolidated Memory
To read the consolidated memory for an agent, use the `obsidian_command` to read the file `agents/<agent_name>/memory.md`.

**Command:**
`obsidian_command(args="read path='agents/<agent_name>/memory.md'")`

### 2. Read Daily Memory
Daily memory files are located in the `agents/<agent_name>/memory` directory and are prefixed by the date (e.g., `YYYY-MM-DD`). However, the filenames may not be consistent beyond the prefix.
Instead of exploring files, the agent should search for the file using the date.

**Workflow:**
1. Search for the file using:
   `obsidian_command(args="search path='agents/<agent_name>/memory' query='<date>'")`
2. Locate the filename from the search results.
3. Read the file using:
   `obsidian_command(args="read path='agents/<agent_name>/memory/<filename>'")`

### 3. Read Feedback
Feedback follows a similar pattern to memory.

#### Consolidated Feedback
Read consolidated feedback from human at `agents/<agent_name>/feedback.md`.

**Command:**
`obsidian_command(args="read path='agents/<agent_name>/feedback.md'")`

#### Daily Feedback
Read daily feedback from the `agents/<agent_name>/feedback` directory.
1. Search for the file using:
   `obsidian_command(args="search path='agents/<agent_name>/feedback' query='<date>'")`
2. Locate the filename from the search results.
3. Read the file using:
   `obsidian_command(args="read path='agents/<agent_name>/feedback/<filename>'")`

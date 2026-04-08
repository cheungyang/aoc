---
name: notebooklm
description: Skill for managing NotebookLM notebooks and sources using nlm tool.
---
## Overview
This skill guides agents on how to manage NotebookLM notebooks and sources using the `nlm` tool, which wraps `notebooklm-cli`.

## Use Cases

### 1. List all notebooks
To list all notebooks in the account.

**Command:**
`nlm(command="notebook list")`

Example:
`nlm(command="notebook list")`

### 2. Create a notebook
To create a new notebook with a given name.

**Command:**
`nlm(command="notebook create '<name>'")`

Example:
`nlm(command="notebook create 'AI Research'")`

### 3. Add source into notebook
To add a source (e.g., a URL) into a specific notebook. You need the `notebook_id`.

**Command:**
`nlm(command="source add <notebook_id> --url '<url>'")`

Example:
`nlm(command="source add abc123-def456 --url 'https://example.com'")`

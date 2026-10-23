---
name: setup_session
description: Prepares the destination file for an incoming voice or note-taking session.
---

Before the user starts rambling, you must determine where the notes will be saved.

1. Ask the user: "Should I start a new note, or append to an existing one?"
2. If **New Note**:
   - Generate a timestamped filename using the new_file_prefix from your RULES.md (e.g., `pkm/inbox/Voice-Note-YYYY-MM-DD-HHMM.md`).
   - Call `filesystem` to `write` the initial empty file or basic header.
   - Confirm you are ready to listen.
3. If **Append**:
   - Ask for a keyword or topic to locate the file.
   - Use `filesystem` `grep` or `find` in `pkm/vault/` to locate the target file.
   - Present the exact path to the user and ask for confirmation. Do not proceed until they approve the path.

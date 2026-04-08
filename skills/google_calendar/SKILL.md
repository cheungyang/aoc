---
name: google_calendar
description: Skill for managing Google Calendar events using gog tool.
---
## Overview
This skill guides agents on how to manage Google Calendar events using the `gog` tool, which wraps `gogcli`.

## Use Cases

### 1. Find events from from_date to to_date
To find events within a specific date range, use the `calendar events` command with `--from` and `--to` flags. Times should be in RFC3339 format (e.g., `2026-04-07T00:00:00Z`).

**Command:**
`gog(command="calendar events primary --from <from_date> --to <to_date>")`

Example:
`gog(command="calendar events primary --from 2026-01-01T00:00:00Z --to 2026-01-08T00:00:00Z")`

### 2. Create event
To create an event, provide summary, start time, end time, and optionally color.
Use `gog calendar colors` to list available color IDs.

**Command:**
`gog(command="calendar create primary --summary '<summary>' --from <start_time> --to <end_time> --color <color_id>")`

Example:
`gog(command="calendar create primary --summary 'Project Sync' --from 2026-01-15T10:00:00Z --to 2026-01-15T11:00:00Z")`

### 3. Update event
To update an event, you need the `calendarId` (e.g., `"primary"`) and the `eventId`. You can get the `eventId` by listing events or searching.

**Command:**
`gog(command="calendar update primary <eventId> --summary '<new_summary>' --from <new_start_time> --to <new_end_time>")`

Example:
`gog(command="calendar update primary 12345 --summary 'Updated Project Sync'")`

### 4. Delete event
To delete an event, you need the `calendarId` and `eventId`.

**Command:**
`gog(command="calendar delete primary <eventId>")`

Example:
`gog(command="calendar delete primary 12345")`

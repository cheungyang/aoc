---
name: web_browsing
description: Operate the user's desktop Agent Chrome to stage bookings (restaurant tables, event seats) under user supervision, ending with a Markdown report.
---
## Overview
The `browser` tool drives a Chrome window on the user's desktop ("Agent Chrome"), where the booking sites are already logged in. The user can watch the window, type into it, and always makes the final commit click. Use it for multi-step interactions: searching availability, picking a slot, filling contact details. To merely read pages, use `web_search` instead: the browser is slow and every step costs a vision-model call.

## Boundaries & Guardrails
- **The "Final Click" Rule**: NEVER commit money or a booking (Reserve, Book, Confirm, Pay, Purchase). Stage everything up to the confirmation page, then hand over: the user clicks it in the Agent Chrome window.
- **Personal details**: Only the placeholders the tool reports (e.g. `full_name`, `phone`, `email`) may be used; refer to them by name in the goal ("fill contact details with full_name, email and phone"). Never ask the user to paste card numbers or passwords into chat, and never put them in a goal.
- **Their browser, their tabs**: Never ask the tool to close tabs or the window.
- **Formatting**: Reply in plain Markdown, never XML: your reply is shown in Discord, even when the Concierge relays the request.

## Workflow

### Step 1: Pin down the request
Before browsing, make sure you have: site or venue, date, time window, party size or seat count, and preferences (seating, section, price cap). Ask once for anything missing; default party size may come from the `default_party_size` placeholder.

### Step 2: Formulate the goal
One specific, self-contained goal string, ending with where to stop.
- *Restaurant:* "On resy.com open Balthazar (New York). Find a table for 2 on Fri Oct 24 between 7 and 8:30 PM. Pick the slot closest to 7:30, fill contact details with full_name, email and phone, then STOP on the final reserve page without clicking Reserve."
- *Event:* "On ticketmaster.com open the Oct 30 Khruangbin show at Hollywood Bowl. Find the 2 best adjacent seats under $150 each, add them to the cart, proceed to checkout, then STOP before payment."

### Step 3: Execute
- Call `browser` with the goal (raise `max_steps` up to 60 only for long flows).
- If it reports the desktop is unreachable, tell the user to open Agent Chrome (or wake the Mac) and offer to retry. Do not fall back to guessing.

### Step 4: Hand off and continue
The tool stops when it needs the user: a login, a verification code, a CAPTCHA, an unspecified choice, or the final click.
- Tell the user exactly what is needed and that the tab is open in Agent Chrome.
- If they solve it in the window or answer in chat, call `browser` again with a goal that continues on the current page ("On the current Resy page, choose the 7:45 PM slot, then stop on the reserve page").

### Step 5: Report
Reply in plain Markdown:
- **First line**: what the user must do now (e.g. "Ready for you: click *Reserve* in Agent Chrome").
- What was staged: venue/event, date, time, party size/seats, price, any fees or cancellation policy shown.
- The final URL and any errors (timeouts, captcha blocks, no availability, plus alternatives seen).

**Memory Trigger**: End with a `<system_memory_log>` block recording what you learned about the site's flow (e.g. "Resy shows the cancellation policy only after selecting a slot"), to speed up future bookings.

## Required Tools
- `browser`: Operates the user's desktop Agent Chrome.
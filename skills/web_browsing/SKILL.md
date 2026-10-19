---
name: web_browsing
description: Operate the user's desktop Agent Chrome to stage bookings (restaurant tables, event seats) under user supervision, handling an offline desktop openly, ending with a Markdown report.
---
## Overview
The `browser` tool drives a Chrome window on the user's Windows desktop ("Agent Chrome"), where the booking sites are already logged in. The user can watch the window, type into it, and always makes the final commit click. Use it for multi-step interactions: searching availability, picking a slot, filling contact details. To merely read pages, use `web_search` instead: the browser is slow and every step costs a vision-model call.

**The desktop is often off.** That is normal, not a failure to work around. The user turns Agent Chrome on with the "Agent Chrome (AOC)" shortcut when they want you to book.

## Boundaries & Guardrails
- **The "Final Click" Rule**: NEVER commit money or a booking (Reserve, Book, Confirm, Pay, Purchase). Stage everything up to the confirmation page, then hand over: the user clicks it in the Agent Chrome window.
- **Fill, never invent**: Fill every form field that matches a placeholder the tool reports (e.g. `full_name`, `phone`, `email`, `postal_code`); say "fill all contact details from the available placeholders" in the goal. Optional fields without a placeholder stay empty. A required field without one means stop and ask the user. Never guess a value, and never ask for card numbers or passwords in chat.
- **Offline is reported, not hidden**: Never pretend a booking is in progress, never retry on your own, never switch to another way of booking without the user.
- **Their browser, their tabs**: Never ask the tool to close tabs or the window.
- **Formatting**: Reply in plain Markdown, never XML: your reply is shown in Discord, even when the Concierge relays the request.

## Workflow

### Step 1: Pin down the request
Before browsing, make sure you have: site or venue, date, time window, party size or seat count, and preferences (seating, section, price cap). Ask once for anything missing; default party size may come from the `default_party_size` placeholder.

### Step 2: Formulate the goal
One specific, self-contained goal string, ending with where to stop.
- *Restaurant:* "On resy.com open Balthazar (New York). Find a table for 2 on Fri Oct 24 between 7 and 8:30 PM. Pick the slot closest to 7:30, fill all contact details from the available placeholders, then STOP on the final reserve page without clicking Reserve."
- *Event:* "On ticketmaster.com open the Oct 30 Khruangbin show at Hollywood Bowl. Find the 2 best adjacent seats under $150 each, add them to the cart, proceed to checkout, then STOP before payment."

### Step 3: Execute
Call `browser` with the goal (raise `max_steps` up to 60 only for long flows). Errors start with a status:

| Status | Meaning | What you do |
|---|---|---|
| `DESKTOP_OFFLINE` | Agent Chrome is not running (PC off, asleep, or window closed). Nothing was done. | Go to Step 3a. |
| `DESKTOP_DISCONNECTED` | The desktop went away mid-task. Nothing was submitted. | Go to Step 3a; also say how far it got (step, page). |
| `NOT_CONFIGURED` | Setup problem (file missing or invalid). | Quote the message; point to INSTALL.md "Agent Browser". Do not retry. |

### Step 3a: Desktop offline: say so, keep the request, wait
1. **First line, plainly**: "Your desktop's Agent Chrome is offline, so I can't book this yet." (or "...went offline mid-booking, after <page>").
2. **Keep the request**: restate the exact booking (venue/event, date, time, party size, preferences) so nothing is lost.
3. **Ask** the user to open "Agent Chrome (AOC)" on their desktop and reply when it is on.
4. **Wait.** One offline result is the answer: do not call `browser` again until the user says it is ready. No retry loops, no scheduled re-attempts.
5. When the user says it is on: call `browser(check_only=True)`. `ONLINE` means run the saved goal (after a disconnect, continue from the page it reached). Still offline means say so again and keep waiting.

In scheduled runs, never start a booking: offer it in the message instead.

### Step 4: Hand off and continue
The tool stops when it needs the user: a login, a verification code, a CAPTCHA, a missing required detail, an unspecified choice, or the final click.
- Tell the user exactly what is needed and that the tab is open in Agent Chrome.
- If they solve it in the window or answer in chat, call `browser` again with a goal that continues on the current page ("On the current Resy page, choose the 7:45 PM slot, then stop on the reserve page").

### Step 5: Report
Reply in plain Markdown:
- **First line**: what the user must do now (e.g. "Ready for you: click *Reserve* in Agent Chrome", or the offline notice).
- What was staged: venue/event, date, time, party size/seats, price, any fees or cancellation policy shown.
- Fields left empty or needing the user, and any site pre-fill that differed from the saved details.
- The final URL and any errors (timeouts, captcha blocks, no availability, plus alternatives seen).

**Memory Trigger**: End with a `<system_memory_log>` block recording what you learned about the site's flow (e.g. "Resy shows the cancellation policy only after selecting a slot"), to speed up future bookings.

## Required Tools
- `browser`: Operates the user's desktop Agent Chrome; `check_only=True` checks whether it is on.
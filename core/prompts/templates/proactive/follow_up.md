This is your follow-up #{{id}}: work you told the user you'd keep doing. Nobody asked you for a message now: decide whether this check is worth one. If it is, it's yours, posted in your own channel.

It is {{now}}.

The goal: {{goal}}
{{#close_when}}
It ends early when: {{close_when}}
{{/close_when}}
You opened it on {{opened}}, and {{checks}}. It runs until {{until}}.
{{#last_report}}

What you last told the user, on {{reported}}:
"{{last_report}}"
{{/last_report}}

{{check}}

Rules:
- Write in the first person, in your own voice.
- One short message, at most 1,500 characters, with at most one question, or a poll.
- If nothing is new since what you last told the user, or it isn't worth a message, reply with exactly <silent/> and nothing else. Then nothing is posted.
- If the goal is met, or can't be met anymore, close this follow-up (follow_up with op "close", id {{id}} and the reason), and say so in your message.

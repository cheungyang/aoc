<playbook>
You turn a Discord post into what its author says aloud. The user hasn't read the post. They will hear it by voice instead, in the author's voice, as the author talking to them. It plays right after a greeting that says who's speaking, so don't introduce yourself.

In <speech>:
{{speech_rules}}
{{question_rule}}
</playbook>

<current_state>
Author: {{name}}
Channel: #{{channel}}
<post>
{{post}}
</post>
</current_state>

<assigned_task>
Write what {{name}} says: the post is your reply. Output ONLY one <speech>...</speech> block, with nothing before or after it.
</assigned_task>

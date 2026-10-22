<playbook>
You match what a user, talking by voice, asked to be switched to against the agents and channels they can switch to. Allow for speech-to-text mistakes in names. A phrase that is a request rather than whom to talk to matches no one: a place, a time or something to be done, as in "the airport on Friday" or "Daisy and add a task".
</playbook>

<current_state>
The user asked to be switched to: "{{phrase}}"
They can be switched to these (one per line: key | who answers | channel | what they do):
{{targets}}
</current_state>

<assigned_task>
Output ONLY <payload>KEY</payload>, with the key of the one the user most likely means, or <payload>none</payload> if no one fits.
</assigned_task>

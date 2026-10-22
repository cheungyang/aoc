<voice_mode>
This is a scheduled run. Your reply is posted to the text channel; the user may hear it later, by voice, when they switch to you.
It plays right after a greeting that says who's speaking, so don't introduce yourself.

Begin your final response with a <speech>...</speech> block, then write your normal text reply.

In <speech>:
{{> voice/_speech_rules}}
{{> voice/_question_rule}}

After </speech>, follow your usual formatting rules. The text reply stays complete.
</voice_mode>

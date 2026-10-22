<playbook>
You are the Content Creator drafting social media publication copy for the topic '{{topic}}'.

Draft engaging social post title, caption, vocabulary pronunciation tips (in the languages the creator instructions name), and hashtags strictly following the creator instructions.

Output ONLY a machine-readable XML payload wrapped in <payload>...</payload> adhering strictly to the schema below without any conversational text outside the tags.
{{#instructions_text}}

--- CREATOR INSTRUCTIONS ---
{{instructions_text}}
{{/instructions_text}}
</playbook>

<current_state>
Topic / Word: `{{topic}}`
Project Path: `{{project_path}}`
Output Path: `{{output_path}}`
Target Copy MD Path: `{{copy_path}}`
Target Copy JSON Path: `{{copy_json_path}}`
{{#revision_feedback}}
--- HUMAN REVISION INSTRUCTIONS FOR COPY ---
{{revision_feedback}}
{{/revision_feedback}}
</current_state>

<assigned_task>
Draft the publication social copy and vocabulary tips for '{{topic}}'.

MANDATORY OUTPUT FORMAT:
You MUST format your entire response within <payload>...</payload> using the following exact XML schema (fill each tag with your generated result):
<payload>
<status>{success|error}</status>
<error>{error_details_if_any_else_empty}</error>
<copy_path>{copy_path}</copy_path>
<caption_text>{engaging_caption_content}</caption_text>
<hashtags>{space_separated_hashtags}</hashtags>
<vocabulary>{vocabulary_pronunciation_notes}</vocabulary>
</payload>
</assigned_task>

Dream: consolidate your memory logs into memory entries, following these rules.
<dream_rules>
{{rules}}
</dream_rules>
Your memory logs and every memory entry you may act on are inlined below. Do not use any tools: the files are read and written for you. Do not emit a <system_memory_log>.
{{dream_input}}
Reply with only this XML. Refer to existing entries by id; use add only for a fact no entry already covers. Entries marked stale="true" must be confirmed or retired.
<dream_response>
  <status>Dreamed or No new memories</status>
  <ops>
    <add tag="one tag from the list" until="YYYY-MM-DD, only for time-bound facts">one-sentence fact</add>
    <confirm id="entry id"/>
    <update id="entry id">corrected fact</update>
    <retire id="entry id" reason="why"/>
  </ops>
  <errors>None</errors>
  <learnings>one-line highlight for the standup</learnings>
</dream_response>

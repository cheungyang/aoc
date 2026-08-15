# Operating Instructions

## Workflow in LangGraph
You will be orchestrated by the `content_creation` LangGraph pipeline:

1. **Audit Video Plot (`audit_video_plot`)**:
   - Read the `{qc_playbook_path}` provided dynamically for the active project (no default paths are assumed).
   - Audit the provided Video Plot Markdown against every rule listed in the playbook (e.g., strict camera movements, restricted AI text hallucination, specific timing requirements).
   - Reply `VERDICT: APPROVED` if compliant, or `VERDICT: REJECTED` with specific instructions.

2. **Extract & QC Video Frames (`extract_and_qc_frames`)**:
   - Read the `{qc_playbook_path}` to understand what artifacts to look for.
   - Analyze the extracted frames from the generated video. Check for visual distortions, incorrect articulation, or prompt bleed.
   - Reply `VERDICT: APPROVED` if all frames pass, or `VERDICT: REJECTED` detailing the exact distortion.

3. **Polish Copy (`draft_and_save_copy`)**:
   - Polish the drafted caption/copy for hooks, engagement, formatting, and alignment with the project's brand voice before final delivery.
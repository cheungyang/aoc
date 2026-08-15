# Operating Instructions

## Workflow in LangGraph
You will be orchestrated by the `content_creation` LangGraph pipeline:

1. **Audit Video Plot (`audit_video_plot`)**:
   - Read the `{qc_playbook_path}` provided dynamically for the active project (no default paths are assumed).
   - Audit the provided Video Plot Markdown against every rule listed in the playbook (e.g., strict camera movements, restricted AI text hallucination, specific timing requirements).
   - Reply `VERDICT: APPROVED` if compliant, or `VERDICT: REJECTED` with specific instructions.

2. **Extract & QC Video Frames (`extract_and_qc_frames`)**:
   - Read the `{qc_playbook_path}` to understand what artifacts and delivery criteria to enforce.
   - **Visual Plate Quality**: Analyze the extracted frames from the generated video. Check for visual distortions, incorrect articulation, or prompt bleed.
   - **Text Overlay Visibility**: Check whether the text overlay is clearly seen in the video keyframes, properly positioned, legible, and accurate.
   - **Audio Track Verification**: Check that the external audio stream is present and verified in the remixed video deliverables.
   - **Targeted Verdicts**:
     - `VERDICT: APPROVED` if all visual plate, text overlay, and audio criteria pass.
     - `VERDICT: REJECTED TARGET: VISUAL_PLATE` if underlying video generation/motion has defects (re-routes to fresh visual plate generation).
     - `VERDICT: REJECTED TARGET: REMIX` if visual plate is good but text overlay or audio track has defects (re-routes directly to remix without re-rendering visual plate).
     - `VERDICT: REJECTED TARGET: BOTH` if both visual plate and audio/text have defects.

3. **Polish Copy (`draft_and_save_copy`)**:
   - Polish the drafted caption/copy for hooks, engagement, formatting, and alignment with the project's brand voice before final delivery.
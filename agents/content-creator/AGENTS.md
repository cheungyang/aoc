# Operating Instructions

## Workflow in LangGraph
You will be orchestrated by the `content_creation` LangGraph pipeline:

1. **Setup & 1-Shot Base Image Generation (`setup_and_generate_image`)**:
   - Read the `{manifest_path}` and `{creator_instructions_path}` provided to you.
   - Extract the master character prefix or stylistic locks defined for the project.
   - Draft the base image generation prompt combining the project's prefix and the specific topic scene.

2. **Draft Video Plot (`draft_video_plot`)**:
   - Read the `{creator_instructions_path}`.
   - Draft the Video Plot Markdown following the exact template and constraints (e.g., aspect ratio, camera movement, negative prompts) demanded by the project.
   - Output the complete markdown document.

3. **Incorporate Feedback**:
   - If the Brand Editor rejects your video plot or copy, carefully read the revision notes and re-draft your output to explicitly solve the violation.

4. **Draft Copy (`draft_and_save_copy`)**:
   - Draft the publication caption/copy for the finalized asset, adhering to the project's voice and required tags/CTAs.
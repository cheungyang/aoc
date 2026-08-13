# Operating Instructions

## Workflow in LangGraph
You will be orchestrated by a LangGraph script. 
1. **Brainstorming Phase**: Read the Character Sheet via `obsidian.read`. Draft 5 image prompts combining the character likeness with the "New Word". Pass these to the `brand-editor`.
2. **Image Generation Phase**: Once approved by the editor, use the `dalle_image_generator` skill to create the images.
3. **Motion Draft Phase**: After the user selects an image, draft a motion prompt for Runway. Pass to `brand-editor` for QC.
4. **Video Generation Phase**: Once motion is approved, use `runway_video_animator` skill to generate the final video.
5. **Copywriting Phase**: Draft an Instagram caption and pass to the `brand-editor` for final polish.
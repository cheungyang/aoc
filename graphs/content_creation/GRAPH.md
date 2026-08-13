---
name: content_creation
description: Orchestrates content-creator and brand-editor to brainstorm ideas, review image prompts, generate 5 DALL-E images, pause for human image selection, draft and review Runway motion prompts, generate video, and polish Instagram copy.
---
## Overview
This graph orchestrates the end-to-end Toddler Tales media studio pipeline:
1. **Draft Image Prompts**: `content-creator` reads the character sheet and drafts 5 image prompts.
2. **Review Image Prompts**: `brand-editor` reviews prompts for character consistency and Pixar style.
3. **Generate Images**: `content-creator` executes `dalle_image_generator` to generate 5 images.
4. **Human Image Selection**: Graph pauses (`interrupt_before`) for the user to review and pick one image.
5. **Draft Motion Prompt**: `content-creator` drafts Runway Gen-3 motion prompt for the chosen image.
6. **Review Motion Prompt**: `brand-editor` reviews motion prompt to enforce Gen-3 guardrails and save API costs.
7. **Generate Video**: `content-creator` executes `runway_video_animator` to animate the image into a `.mp4` video.
8. **Draft Copy**: `content-creator` drafts engaging Instagram caption.
9. **Polish Copy**: `brand-editor` polishes copy for virality, emoji placement, and hashtags.
10. **Final Delivery**: Outputs all 5 generated images, selected image, final video, and Instagram copy.

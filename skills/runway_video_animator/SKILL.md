---
name: runway_video_animator
description: Executes API calls to Runway Gen-3 for animating static images into high-quality videos based on motion prompts.
---
## Overview
This skill grants the agent the ability to programmatically pass an image file path and a motion prompt to the Runway API, wait for the render completion, and save the resulting `.mp4` video to the user's vault.

## Required Tools
- `runway_video_animator`: Python tool to handle the API payload, asynchronous polling, downloading, and file-saving logic.
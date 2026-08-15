---
name: content_creation
description: Multi-turn instruction-driven media generation studio with Sqlite state persistence, asset versioning (_v1, _v2, ...), intelligent HITL revision routing, Red Team QC audits, and continuous execution_log.md logging.
---
## Overview
This graph orchestrates a multi-turn, instruction-driven media generation pipeline driven by project markdown documents (`manifest_path`, `creator_instructions_path`, `qc_playbook_path`).

> [!IMPORTANT]
> **No Default Paths:** All default root paths (e.g. `words/`) have been removed. The orchestrating agent or user **must** provide the project path (`project_dir`) and/or output path (`output_dir`) along with the `topic` to initialize the flow (e.g. `graph_call(graph_name="content_creation", query="topic: fish, project_dir: pkm/wiki/software/ayla-first-words")`). If neither is provided, initialization halts and requests the required path parameters.

1. **Setup & 1-Shot Base Image (`setup_and_generate_image`)**: Content Creator reads the project manifest and creator instructions located dynamically in `{project_dir}` to generate the 1-shot base image to `{output_dir}/{topic}_image_v{N}.jpg`.
2. **Draft Video Plot (`draft_video_plot`)**: Content Creator drafts `{topic}_video_plot_v{N}.md` strictly following the instructions and persists directly to disk.
3. **Dual-Asset QC Audit (`audit_video_plot`)**: Brand Editor audits **BOTH** the Base Image and Video Plot against QC playbook rules:
   - **Image Failure**: Increments `image_version` and loops back to Step 1 (`setup_and_generate_image`).
   - **Plot Failure**: Increments `video_plot_version` and loops back to Step 2 (`draft_video_plot`).
   - **Pass**: Transitions forward to Gate 1.
4. 🛑 **HITL Gate 1: Image & Video Plot Approval (`hitl_image_and_plot_approval`)**: Pauses for user to review base image and approved video plot.
   - **Approval** (`"approved"`, `"go"`): Proceeds to video generation.
   - **Image Feedback** (`"make hair curlier"`): Increments `image_version` and loops back to Step 1.
   - **Plot Feedback** (`"slower camera zoom"`): Increments `video_plot_version` and loops back to Step 2.
   - **Ambiguous Feedback**: Prompts user for clarification without losing state.
5. **Generate Visual Plate (`generate_visual_plate`)**: Content Creator generates the raw video visual plate to `{output_dir}/{topic}_raw_video_v{N}.mp4` using the approved motion prompt and explicitly verifies non-zero byte size on disk.
6. **Remix Video (`remix_video`)**: Content Creator uses `remix_video` to overlay the external audio track (`{topic}_wav.wav`) and styled text overlay onto the visual plate, outputting the master video `{output_dir}/{topic}_video_v{N}.mp4`.
7. **Extract & QC Video Frames (`extract_and_qc_frames`)**: Brand Editor audits the remixed deliverables:
   - **Audio Verification**: Automated FFmpeg check confirming presence and mapping of the audio stream.
   - **Text Overlay Visibility**: Inspects extracted keyframes across text display timestamps to confirm legible, centered, and accurate text.
   - **Visual Fidelity**: Audits keyframes against QC playbook criteria.
   - **Targeted Re-routing**:
     - **Video Generation Issue** (`TARGET: VISUAL_PLATE`): Distortions, warping, or motion flaws re-route back to Node 4 (`generate_visual_plate`).
     - **Audio/Text Overlay Issue** (`TARGET: REMIX`): Missing text, illegible font, missing audio track, or timing flaws re-route back directly to Node 5 (`remix_video`) without re-rendering the visual plate.
   - **Retry Exhaustion**: If `video_qc_attempts >= max_video_reviews` without passing QC, hard blocks at `hitl_video_qc_failure_intervention` requiring manual intervention.
8. **Draft & Save Copy (`draft_and_save_copy`)**: Content Creator drafts publication copy, Brand Editor polishes for alignment with playbook, and writes to `{output_dir}/{topic}_copy_v{N}.md`.
9. 🎉 **HITL Gate 2: Final Package Review & Approval (`hitl_final_package_approval`)**: Final 1-click delivery review (verifies physical disk presence of all assets).
   - **Approval** (`"approved"`): Completes delivery.
   - **Copy Feedback** (`"change hashtags"`): Increments `copy_version` and loops back to Step 8 (`draft_and_save_copy`).
   - **Text/Audio Remix Feedback** (`"increase audio volume"`, `"change text font size"`): Increments `video_version` and loops directly back to Step 6 (`remix_video`) without re-rendering the visual plate.
   - **Video Motion Feedback** (`"slower camera movement"`): Increments `video_version` and loops back to Step 5 (`generate_visual_plate`).
   - **Ambiguous Feedback**: Prompts user for clarification.
10. **Continuous Audit Logging (`execution_log.md`)**: Full trace of prompts, QC audits, human feedback, and version progressions saved in `{output_dir}/execution_log.md`.

## Flowchart
```text
[START]
   │
   ▼
[1. setup_and_generate_image]   ──► (Content Creator: Generates Image v{N})
   │          ▲
   │ (Pass)   │ (QC Fail: Image Issue)
   ▼          │
[2. draft_video_plot]           ──► (Content Creator: Writes Plot v{N})
   │          ▲
   │ (Pass)   │ (QC Fail: Plot Issue)
   ▼          │
[3. audit_video_plot]           ──► (Brand Editor: Audits BOTH Image & Plot)
   │          │
   ├──────────┴─────────────────► (Image Fail -> Step 1 / Plot Fail -> Step 2)
   │
   │ (Pass)
   ▼
═══════════════════════════════════════════════════════════════════════
  🛑 [HITL GATE 1: Image & Video Plot Approval] (User reviews image & plot)
═══════════════════════════════════════════════════════════════════════
   │
   ├──► (Revise Image)  ──► [1. setup_and_generate_image] (v2, v3...)
   ├──► (Revise Plot)   ──► [2. draft_video_plot] (v2, v3...)
   ├──► (Clarify)       ──► [Clarification Prompt]
   │
   ▼ (Approved)
[4. generate_visual_plate] ◄────┐ (QC Fail: Visual Plate Issue)
   │          ▲                 │
   │          │ (Gate 2: Revise │
   │          │  Video Motion)  │
   ▼          │                 │
[5. remix_video] ◄──────────┐   │ (QC Fail: Audio/Text Issue)
   │          ▲             │   │
   │          │ (Gate 2:    │   │
   │          │  Text/Audio)│   │
   ▼          │             │   │
[6. extract_and_qc_frames] ─┴───┘ (Brand Editor: Verifies audio + text + keyframes)
   │
   ├──────► [🛑 HITL Video QC Intervention] (Fail & attempts >= max)
   │              │
   │ (Pass)       ├──► (Retry) ──► [4. generate_visual_plate] / [5. remix_video]
   │              └──► (Abort) ──► [END]
   ▼
[7. draft_and_save_copy] ◄──┐ (Gate 2: Revise Copy)
   │          ▲             │
   │ (Revise) │             │
   ▼          │             │
═══════════════════════════════════════════════════════════════════════
  🎉 [HITL GATE 2: Final Package Review & Approval] (User 1-click finalize)
═══════════════════════════════════════════════════════════════════════
   │
   ├──► (Revise Copy)       ──► [7. draft_and_save_copy] (v2, v3...)
   ├──► (Revise Text/Audio) ──► [5. remix_video] (v2, v3...)
   ├──► (Revise Video)      ──► [4. generate_visual_plate] (v2, v3...)
   ├──► (Clarify)           ──► [Clarification Prompt]
   │
   ▼ (Approved)
 [END]
```


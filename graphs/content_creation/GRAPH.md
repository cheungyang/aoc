---
name: content_creation
description: Multi-turn instruction-driven media generation studio with Sqlite state persistence, autonomous exception routing, decoupled headless QC, and deterministic disk asset versioning.
---
## Overview
This graph orchestrates a multi-turn, instruction-driven media generation pipeline driven by project markdown documents (`manifest_path`, `creator_instructions_path`, `qc_playbook_path`). It is composed of three cleanly decoupled subgraphs: Ideation, Video Production, and Copywriting.

> [!IMPORTANT]
> **No Default Paths:** All default root paths (e.g. `words/`) have been removed. The orchestrating agent or user **must** provide the project path (`project_dir`) and/or output path (`output_dir`) along with the `topic` to initialize the flow (e.g. `graph_call(graph_name="content_creation", query="topic: fish, project_dir: pkm/wiki/software/ayla-first-words")`). If neither is provided, initialization halts and requests the required path parameters.

### 1. Ideation Subgraph
1. **Setup & 1-Shot Base Image (`setup_and_generate_image`)**: Content Creator reads the project manifest and creator instructions located dynamically in `{project_dir}` to generate the 1-shot base image. Asset versions are deterministically derived by scanning disk reality (e.g. `glob.glob`).
2. **Draft Video Plot (`draft_video_plot`)**: Content Creator drafts the plot strictly following the instructions. This node returns a strict **Pydantic Model (`VideoPlot`)** which is dual-published to disk as both machine-readable JSON and human-readable Markdown.
3. **Dual-Asset QC Audit (`audit_video_plot`)**: Brand Editor audits **BOTH** the Base Image and Video Plot against QC playbook rules using a structured **Pydantic Model (`PlotAudit`)**.
   - **Image Failure**: Loops back to Step 1.
   - **Plot Failure**: Loops back to Step 2.
   - **Pass**: Transitions forward to Gate 1.
4. 🛑 **HITL Gate 1: Image & Video Plot Approval (`hitl_image_and_plot_approval`)**: Presents the base image and plot to the user, then pauses via standard `interrupt_after` for approval.
   - **Approval**: Proceeds to Video Production.
   - **Image Feedback**: Loops back to Step 1.
   - **Plot Feedback**: Loops back to Step 2.
   - **Ambiguous Feedback**: Prompts user for clarification without losing state.

### 2. Video Production Subgraph
5. **Generate Visual Plate (`generate_visual_plate`)**: Generates the raw video visual plate using the approved motion prompt. If API timeout or ffmpeg crashes occur, the exception is caught by Native Exception Routing.
6. **Remix Video (`remix_video`)**: Overlays external audio (`{topic}_wav.wav`) and styled text overlay onto the visual plate.
7. **Extract & QC Video Frames (`extract_and_qc_frames`)**: 
   - **Audio Verification**: Automated `audio_stream_probe` tool confirming presence of audio.
   - **Text Verification**: Automated `video_ocr_validator` confirming legible, accurate text in keyframes.
   - **Targeted Re-routing**:
     - **Visual Plate Issue**: Distortions/motion flaws re-route back to Node 5.
     - **Remix Issue**: Missing text/audio flaws re-route back to Node 6 without re-rendering the base video.
8. **Autonomous Debugger (`autonomous_debugger`)**: Native Exception Routing catches exceptions from Node 5, 6, or 7. The debugger manages retries up to 3 times before halting.
9. **Fail-Fast Headless QC (`fail_fast_video_qc`)**: If automated video QC exhaustion occurs, the graph halts headlessly without halting for a legacy HITL intervention.

### 3. Copywriting Subgraph
10. **Draft & Save Copy (`draft_and_save_copy`)**: Content Creator drafts publication copy via a **Pydantic Model (`FinalCopy`)** and writes to `{output_dir}` (dual-published to .json and .md).
11. 🎉 **HITL Gate 2: Final Package Review & Approval (`hitl_final_package_approval`)**: Final 1-click delivery review (pauses via `interrupt_after`).
    - **Approval**: Completes delivery.
    - **Copy Feedback**: Loops back to Copywriting.
    - **Video Feedback**: Loops back to Video Production.
    - **Ambiguous Feedback**: Prompts user for clarification.

## Flowchart
```text
[START]
   │
   ▼
[ IDEATION SUBGRAPH ]
   ├──► [setup_and_generate_image] ◄────┐
   │          │                         │ (Image/Plot Rejection)
   │          ▼                         │
   ├──► [draft_video_plot] (Structured) │
   │          │                         │
   │          ▼                         │
   └──► [audit_video_plot] (Structured) ┘
              │ (Pass)
              ▼
════════════════════════════════════════════════════════════════
  🛑 [HITL GATE 1] (Pauses via interrupt_after)
════════════════════════════════════════════════════════════════
              │ (Approved)
              ▼
[ VIDEO PRODUCTION SUBGRAPH ]
   ├──► [generate_visual_plate] ◄──────┐ (Retry logic / Visual rejections)
   │          │                        │ 
   │          ▼                        │ 
   ├──► [remix_video] ◄────────┐       │ (Remix rejections)
   │          │                │       │ 
   │          ▼                │       │ 
   └──► [extract_and_qc_frames]┼───────┘ 
              │                │
              │                ├──► [autonomous_debugger] (Exception routing up to 3x)
              │                └──► [fail_fast_video_qc]  (Exhaustion failure)
              │ (Pass)
              ▼
[ COPYWRITING SUBGRAPH ]
   └──► [draft_and_save_copy] (Structured)
              │
              ▼
════════════════════════════════════════════════════════════════
  🎉 [HITL GATE 2] (Pauses via interrupt_after)
════════════════════════════════════════════════════════════════
              │ (Approved)
              ▼
            [END]
```

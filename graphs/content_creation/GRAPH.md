---
name: content_creation
description: Generic instruction-driven media generation pipeline orchestrating content-creator and brand-editor to generate 1-shot base images, draft and audit video plots, gate with HITL approval, render visual plate video, extract and QC keyframes, draft and polish copy, and gate final package approval.
---
## Overview
This graph orchestrates a generic, instruction-driven media generation pipeline driven by project markdown documents (`manifest_path`, `creator_instructions_path`, `qc_playbook_path`):
1. **Setup & 1-Shot Base Image (`setup_and_generate_image`)**: Content Creator reads the project manifest and creator instructions to generate the 1-shot base image directly to `{output_dir}/{topic}_image.jpg`.
2. **Draft Video Plot (`draft_video_plot`)**: Content Creator drafts `{topic}_video_plot.md` strictly following the instructions and persists directly to `{output_dir}/{topic}_video_plot.md`.
3. **Audit Video Plot (`audit_video_plot`)**: Brand Editor audits `{topic}_video_plot.md` against QC playbook rules. Loops back if non-compliant.
4. 🛑 **HITL Gate 1: Image & Video Plot Approval (`hitl_image_and_plot_approval`)**: Pauses for user to review base image and approved video plot before video generation.
5. **Generate Visual Plate (`generate_visual_plate`)**: Content Creator generates the video plate to `{output_dir}/{topic}_video.mp4` using the approved motion prompt.
6. **Extract & QC Video Frames (`extract_and_qc_frames`)**: Brand Editor runs `extract_video_frames` at configurable timestamps (default `[1.0, 2.5, 4.0]s`) and audits keyframes against the QC playbook. Loops back if non-compliant.
7. **Draft & Save Copy (`draft_and_save_copy`)**: Content Creator drafts publication copy, Brand Editor polishes for alignment with playbook, and writes to `{output_dir}/{topic}_copy.md`.
8. 🎉 **HITL Gate 2: Final Package Review & Approval (`hitl_final_package_approval`)**: Final 1-click delivery review of base image, video plot, master visual plate, and publication copy.

## Flowchart
```text
[START]
   │
   ▼
[1. setup_and_generate_image]   ──► (Content Creator: Reads manifest & instructions -> Base Image)
   │
   ▼
[2. draft_video_plot]           ──► (Content Creator: Writes {topic}_video_plot.md)
   │
   ▼
[3. audit_video_plot]           ──► (Brand Editor: Audits against QC playbook)
   │          ▲
   │ (Fail)   │
   └──────────┘
   │ (Pass)
   ▼
═══════════════════════════════════════════════════════════════════════
  🛑 [HITL GATE 1: Image & Video Plot Approval] (User reviews image & plot)
═══════════════════════════════════════════════════════════════════════
   │ (Approved)
   ▼
[4. generate_visual_plate]      ──► (Content Creator: Generates video plate)
   │
   ▼
[5. extract_and_qc_frames]      ──► (Brand Editor: extract_video_frames + Playbook QC)
   │          ▲
   │ (Fail)   │
   └──────────┘
   │ (Pass)
   ▼
[6. draft_and_save_copy]        ──► (Content Creator drafts -> Brand Editor polishes ->
   │                                 Writes {topic}_copy.md)
   ▼
═══════════════════════════════════════════════════════════════════════
  🎉 [HITL GATE 2: Final Package Review & Approval] (User 1-click finalize)
═══════════════════════════════════════════════════════════════════════
   │
   ▼
 [END]
```


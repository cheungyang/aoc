---
name: content_creation
description: Multi-turn instruction-driven media generation studio with 3 high-cohesion macro nodes, modular subpackage organization, lean state schema, and deterministic disk asset versioning.
---
## Overview
This graph orchestrates a multi-turn, instruction-driven content creation pipeline driven by project markdown documents (`manifest_path`, `creator_instructions_path`, `qc_playbook_path`). It is organized into **3 high-cohesion macro nodes** across modular task packages (`ingestion`, `ideation`, `production`) with **2 Human-in-the-Loop (HITL) Decision Gates**.

> [!IMPORTANT]
> **No Default Paths:** All default root paths (e.g. `words/`) have been removed. The caller **must** provide the project path (`project_dir`) and/or output path (`output_dir`) along with the `topic` to initialize the flow (e.g. `graph_call(graph_name="content_creation", query="topic: cat, project_dir: pkm/wiki/software/ayla-first-words")`).

---

## 1. Pipeline Phases

### Phase 1: Audio Ingestion (`nodes/ingestion/`)
1. **Ingest Audio (`ingest_audio`)**: Ingests audio from query text, Discord attachments, or existing local directory files into `{output_dir}`.
2. **Ask For Audio (`ask_for_audio`)**: If no valid audio is detected, prompts the user to upload the audio clip and pauses for user input.

### Phase 2: Ideation Package (`nodes/ideation/`)
3. **Ideate Package (`ideate_package`)**: High-cohesion macro node that orchestrates:
   - **`generate_image.py`**: Generates or reuses the 1-shot base image from project guidelines.
   - **`draft_plot.py`**: Drafts structured `VideoPlot` via Gemini with exact image and audio data bindings (dual-published to `.md` and `.json`).
   - **`audit_plot.py`**: Self-contained Brand QC audit against `03_QC_Playbook.md` with up to 2 auto-corrections.
   - **Gate 1 Card**: Formats the presentation card with `<images><image path="..."/></images>` and plot preview.
4. 🛑 **HITL Gate 1: Image & Video Plot Approval**: Pauses via `interrupt_after` for human signoff:
   - **`approved`**: Proceeds to Production (`produce_deliverables`).
   - **`revise_image` / `revise_plot`**: Loops back internally to `ideate_package`.

### Phase 3: Deliverables Production (`nodes/production/`)
5. **Produce Deliverables (`produce_deliverables`)**: High-cohesion macro node that orchestrates:
   - **`render_plate.py`**: Generates raw motion plate (`raw_video_path`) via Google Veo 3.
   - **`remix_video.py`**: Muxes audio track and burns Chinese subtitles via FFmpeg into `remixed_video_path`.
   - **`verify_video.py`**: Extracts keyframe paths (`extracted_frames_path`), probes audio stream presence, and checks subtitle OCR.
   - **`draft_copy.py`**: Drafts publication social copy (`copy_path`) with vocabulary notes and hashtags (dual-published to `.md` and `.json`).
   - **Gate 2 Card**: Formats the final package card with image, video embed (`<videos><video path="..."/></videos>`), and publication copy preview.
6. 🎉 **HITL Gate 2: Final Package Review & 1-Click Approval**: Pauses via `interrupt_after` for final signoff:
   - **`approved`**: Completes delivery and transitions to `END`.
   - **`revise_copy` / `revise_video` / `revise_remix`**: Loops back to `produce_deliverables`.

---

## 2. Flowchart

```text
[START]
   │
   ▼
[ 1. ingest_audio ] ◄──────────────┐ (Upload Audio)
   │                               │
   ├──► (Missing Audio) ──► [ ask_for_audio ]
   │
   ▼ (Audio Verified)
[ 2. ideate_package ] ◄────────────┐
   │                               │
   ▼                               │ (revise_image / revise_plot)
═════════════════════════════════  │
  🛑 [HITL GATE 1]                 │
═════════════════════════════════  │
   │                               │
   ├── (Revision Requested) ───────┘
   │
   ▼ (approved)
[ 3. produce_deliverables ] ◄──────┐
   │                               │
   ▼                               │ (revise_video / revise_remix / revise_copy)
═════════════════════════════════  │
  🎉 [HITL GATE 2]                 │
═════════════════════════════════  │
   │                               │
   ├── (Revision Requested) ───────┘
   │
   ▼ (approved)
 [END]
```

---

## 3. Lean State Schema

```python
class ContentCreationState(TypedDict, total=False):
    # Context
    project_dir: str
    output_dir: str
    topic: str
    style: str  # '3D' | 'Ghibli' | 'Photorealistic'
    session_id: str
    thread_id: str
    messages: List[AnyMessage]
    error_message: str
    manifest_path: str
    creator_instructions_path: str
    qc_playbook_path: str
    execution_log_path: str

    # Asset Inputs & Deliverable Paths
    source_audio_path: str
    overlay_text: str
    image_path: str
    video_plot_path: str
    raw_video_path: str
    remixed_video_path: str
    extracted_frames_path: List[str]
    copy_path: str

    # Execution & Decisions
    video_plot_qc_passed: bool
    video_qc_passed: bool
    video_qc_attempts: int
    video_qc_feedback: str
    gate1_decision: str
    gate2_decision: str
    latest_human_feedback: str
    final_package: Dict[str, Any]
```

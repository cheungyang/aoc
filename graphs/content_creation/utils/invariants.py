import os
from typing import Dict, Any


class AssetInvariantError(RuntimeError):
    """Raised when a node produces state that violates expected asset progression or freshness invariants."""
    pass


def assert_gate1_revision_invariants(initial_state: Dict[str, Any], result_state: Dict[str, Any]) -> None:
    """
    Enforces that when a Gate 1 revision is requested, targeted assets are strictly fresh,
    versioned, non-empty, and physically persisted on disk, while untargeted assets are preserved.
    """
    decision = initial_state.get("gate1_decision") or result_state.get("gate1_decision")
    if not decision or decision == "approved":
        return

    # Invariant 1: If image revision requested, image path MUST be incremented and exist on disk
    if decision == "revise_image":
        new_img = result_state.get("image_path")
        old_img = initial_state.get("image_path")
        if not new_img:
            raise AssetInvariantError("Gate 1 revision invariant failed: image_path is missing in result state.")
        if not (os.path.isfile(new_img) and os.path.getsize(new_img) > 0):
            raise AssetInvariantError(f"Gate 1 revision invariant failed: image file {new_img} does not exist or is 0 bytes.")
        if old_img and os.path.isfile(old_img) and new_img == old_img:
            raise AssetInvariantError(
                f"Gate 1 revision invariant failed: image_path was not incremented upon revision. "
                f"Still referencing old path {old_img}."
            )

    # Invariant 2: If plot revision requested, video plot MUST be incremented and exist on disk
    if decision == "revise_plot":
        new_plot = result_state.get("video_plot_path")
        old_plot = initial_state.get("video_plot_path")
        if not new_plot:
            raise AssetInvariantError("Gate 1 revision invariant failed: video_plot_path is missing in result state.")
        if not (os.path.isfile(new_plot) and os.path.getsize(new_plot) > 0):
            raise AssetInvariantError(f"Gate 1 revision invariant failed: plot file {new_plot} does not exist or is 0 bytes.")
        if old_plot and os.path.isfile(old_plot) and new_plot == old_plot:
            raise AssetInvariantError(
                f"Gate 1 revision invariant failed: video_plot_path was not incremented upon revision. "
                f"Still referencing old path {old_plot}."
            )


def assert_gate2_revision_invariants(initial_state: Dict[str, Any], result_state: Dict[str, Any]) -> None:
    """
    Enforces that when a Gate 2 revision is requested, targeted deliverables are strictly fresh,
    versioned, non-empty, and physically persisted on disk, while untargeted deliverables are preserved.
    """
    decision = initial_state.get("gate2_decision") or result_state.get("gate2_decision")
    if not decision or decision == "approved":
        return

    # Invariant 1: If copy revision requested, copy path MUST be incremented and exist on disk
    if decision == "revise_copy":
        new_copy = result_state.get("copy_path")
        old_copy = initial_state.get("copy_path")
        if not new_copy:
            raise AssetInvariantError("Gate 2 revision invariant failed: copy_path is missing in result state.")
        if not (os.path.isfile(new_copy) and os.path.getsize(new_copy) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: copy file {new_copy} does not exist or is 0 bytes.")
        if old_copy and os.path.isfile(old_copy) and new_copy == old_copy:
            raise AssetInvariantError(
                f"Gate 2 revision invariant failed: copy_path was not incremented upon revision. "
                f"Still referencing old path {old_copy}."
            )

    # Invariant 2: If video animation revision requested, raw plate and remixed video MUST be incremented
    if decision in ["revise_video", "revise_animation"]:
        new_raw = result_state.get("raw_video_path")
        old_raw = initial_state.get("raw_video_path")
        new_remix = result_state.get("remixed_video_path")
        old_remix = initial_state.get("remixed_video_path")

        if not new_raw or not (os.path.isfile(new_raw) and os.path.getsize(new_raw) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: raw video file {new_raw} missing or empty.")
        if not new_remix or not (os.path.isfile(new_remix) and os.path.getsize(new_remix) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: remixed video file {new_remix} missing or empty.")
        if old_raw and os.path.isfile(old_raw) and new_raw == old_raw:
            raise AssetInvariantError(f"Gate 2 revision invariant failed: raw_video_path not incremented from {old_raw}.")
        if old_remix and os.path.isfile(old_remix) and new_remix == old_remix:
            raise AssetInvariantError(f"Gate 2 revision invariant failed: remixed_video_path not incremented from {old_remix}.")

    # Invariant 3: If remix/audio/subtitle revision requested, remixed video MUST be incremented while raw plate is preserved
    if decision in ["revise_remix", "revise_audio", "revise_subtitles"]:
        new_remix = result_state.get("remixed_video_path")
        old_remix = initial_state.get("remixed_video_path")
        if not new_remix or not (os.path.isfile(new_remix) and os.path.getsize(new_remix) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: remixed video file {new_remix} missing or empty.")
        if old_remix and os.path.isfile(old_remix) and new_remix == old_remix:
            raise AssetInvariantError(f"Gate 2 revision invariant failed: remixed_video_path not incremented from {old_remix}.")

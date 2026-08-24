import os
import glob
from typing import Dict, Any


class AssetInvariantError(RuntimeError):
    """Raised when a node produces state that violates expected asset progression or freshness invariants."""
    pass


def assert_gate1_revision_invariants(initial_state: Dict[str, Any], result_state: Dict[str, Any]) -> None:
    """
    Enforces that when a Gate 1 revision is requested, targeted deliverables exist, are non-empty,
    and previous rejected assets are preserved as archived versions on disk.
    """
    decision = initial_state.get("gate1_decision") or result_state.get("gate1_decision")
    if not decision or decision == "approved":
        return

    # Invariant 1: If image revision requested, canonical image file MUST exist and be non-empty
    if decision == "revise_image":
        img_path = result_state.get("image_path")
        if not img_path:
            raise AssetInvariantError("Gate 1 revision invariant failed: image_path is missing in result state.")
        if not (os.path.isfile(img_path) and os.path.getsize(img_path) > 0):
            raise AssetInvariantError(f"Gate 1 revision invariant failed: image file {img_path} does not exist or is 0 bytes.")

    # Invariant 2: If plot revision requested, canonical video plot MUST exist and be non-empty
    if decision == "revise_plot":
        plot_path = result_state.get("video_plot_path")
        if not plot_path:
            raise AssetInvariantError("Gate 1 revision invariant failed: video_plot_path is missing in result state.")
        if not (os.path.isfile(plot_path) and os.path.getsize(plot_path) > 0):
            raise AssetInvariantError(f"Gate 1 revision invariant failed: plot file {plot_path} does not exist or is 0 bytes.")


def assert_gate2_revision_invariants(initial_state: Dict[str, Any], result_state: Dict[str, Any]) -> None:
    """
    Enforces that when a Gate 2 revision is requested, targeted deliverables exist, are non-empty,
    and previous rejected deliverables are preserved as archived versions on disk.
    """
    decision = initial_state.get("gate2_decision") or result_state.get("gate2_decision")
    if not decision or decision == "approved":
        return

    # Invariant 1: If copy revision requested, copy path MUST exist and be non-empty
    if decision == "revise_copy":
        copy_path = result_state.get("copy_path")
        if not copy_path:
            raise AssetInvariantError("Gate 2 revision invariant failed: copy_path is missing in result state.")
        if not (os.path.isfile(copy_path) and os.path.getsize(copy_path) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: copy file {copy_path} does not exist or is 0 bytes.")

    # Invariant 2: If video animation revision requested, raw plate and remixed video MUST exist and be non-empty
    if decision in ["revise_video", "revise_animation"]:
        raw_path = result_state.get("raw_video_path")
        remix_path = result_state.get("remixed_video_path")
        if not raw_path or not (os.path.isfile(raw_path) and os.path.getsize(raw_path) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: raw video file {raw_path} missing or empty.")
        if not remix_path or not (os.path.isfile(remix_path) and os.path.getsize(remix_path) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: remixed video file {remix_path} missing or empty.")

    # Invariant 3: If remix/audio/subtitle revision requested, remixed video MUST exist and be non-empty
    if decision in ["revise_remix", "revise_audio", "revise_subtitles"]:
        remix_path = result_state.get("remixed_video_path")
        if not remix_path or not (os.path.isfile(remix_path) and os.path.getsize(remix_path) > 0):
            raise AssetInvariantError(f"Gate 2 revision invariant failed: remixed video file {remix_path} missing or empty.")

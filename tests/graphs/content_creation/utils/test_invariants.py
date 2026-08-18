import unittest
import tempfile
import os
from graphs.content_creation.utils.invariants import (
    AssetInvariantError,
    assert_gate1_revision_invariants,
    assert_gate2_revision_invariants
)


class TestInvariants(unittest.TestCase):

    def test_gate1_passes_when_image_is_incremented_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            v1_img = os.path.join(temp_dir, "cat_image.jpg")
            with open(v1_img, "wb") as f: f.write(b"V1_BYTES")

            v2_img = os.path.join(temp_dir, "cat_image_v2.jpg")
            with open(v2_img, "wb") as f: f.write(b"V2_BYTES")

            v1_plot = os.path.join(temp_dir, "cat_video_plot.md")
            with open(v1_plot, "w") as f: f.write("Plot v1")

            v2_plot = os.path.join(temp_dir, "cat_video_plot_v2.md")
            with open(v2_plot, "w") as f: f.write("Plot v2")

            initial_state = {
                "image_path": v1_img,
                "video_plot_path": v1_plot,
                "gate1_decision": "revise_image"
            }
            result_state = {
                "image_path": v2_img,
                "video_plot_path": v2_plot,
                "gate1_decision": "revise_image"
            }
            # Should not raise
            assert_gate1_revision_invariants(initial_state, result_state)

    def test_gate1_raises_when_image_is_not_incremented(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            v1_img = os.path.join(temp_dir, "cat_image.jpg")
            with open(v1_img, "wb") as f: f.write(b"V1_BYTES")

            v1_plot = os.path.join(temp_dir, "cat_video_plot.md")
            with open(v1_plot, "w") as f: f.write("Plot v1")

            initial_state = {
                "image_path": v1_img,
                "video_plot_path": v1_plot,
                "gate1_decision": "revise_image"
            }
            result_state = {
                "image_path": v1_img,  # Stale!
                "video_plot_path": v1_plot,
                "gate1_decision": "revise_image"
            }
            with self.assertRaises(AssetInvariantError) as ctx:
                assert_gate1_revision_invariants(initial_state, result_state)
            self.assertIn("was not incremented upon revision", str(ctx.exception))

    def test_gate2_passes_when_copy_is_incremented(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            v1_copy = os.path.join(temp_dir, "cat_copy.md")
            with open(v1_copy, "w") as f: f.write("Copy v1")

            v2_copy = os.path.join(temp_dir, "cat_copy_v2.md")
            with open(v2_copy, "w") as f: f.write("Copy v2")

            initial_state = {
                "copy_path": v1_copy,
                "gate2_decision": "revise_copy"
            }
            result_state = {
                "copy_path": v2_copy,
                "gate2_decision": "revise_copy"
            }
            assert_gate2_revision_invariants(initial_state, result_state)

    def test_gate2_raises_when_copy_is_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            v1_copy = os.path.join(temp_dir, "cat_copy.md")
            with open(v1_copy, "w") as f: f.write("Copy v1")

            initial_state = {
                "copy_path": v1_copy,
                "gate2_decision": "revise_copy"
            }
            result_state = {
                "copy_path": v1_copy,  # Stale!
                "gate2_decision": "revise_copy"
            }
            with self.assertRaises(AssetInvariantError) as ctx:
                assert_gate2_revision_invariants(initial_state, result_state)
            self.assertIn("was not incremented upon revision", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

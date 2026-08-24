import unittest
import tempfile
import os
from graphs.content_creation.utils.invariants import (
    AssetInvariantError,
    assert_gate1_revision_invariants,
    assert_gate2_revision_invariants
)


class TestInvariants(unittest.TestCase):

    def test_gate1_passes_when_image_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            img_file = os.path.join(temp_dir, "cat_image.jpg")
            with open(img_file, "wb") as f: f.write(b"V2_BYTES")

            plot_file = os.path.join(temp_dir, "cat_video_plot.md")
            with open(plot_file, "w") as f: f.write("Plot v1")

            initial_state = {
                "image_path": img_file,
                "video_plot_path": plot_file,
                "gate1_decision": "revise_image"
            }
            result_state = {
                "image_path": img_file,
                "video_plot_path": plot_file,
                "gate1_decision": "revise_image"
            }
            # Should not raise
            assert_gate1_revision_invariants(initial_state, result_state)

    def test_gate1_raises_when_image_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            img_file = os.path.join(temp_dir, "cat_image.jpg")  # Does not exist
            plot_file = os.path.join(temp_dir, "cat_video_plot.md")
            with open(plot_file, "w") as f: f.write("Plot v1")

            initial_state = {
                "image_path": img_file,
                "video_plot_path": plot_file,
                "gate1_decision": "revise_image"
            }
            result_state = {
                "image_path": img_file,
                "video_plot_path": plot_file,
                "gate1_decision": "revise_image"
            }
            with self.assertRaises(AssetInvariantError) as ctx:
                assert_gate1_revision_invariants(initial_state, result_state)
            self.assertIn("does not exist", str(ctx.exception))

    def test_gate2_passes_when_copy_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            copy_file = os.path.join(temp_dir, "cat_copy.md")
            with open(copy_file, "w") as f: f.write("Copy v2")

            initial_state = {
                "copy_path": copy_file,
                "gate2_decision": "revise_copy"
            }
            result_state = {
                "copy_path": copy_file,
                "gate2_decision": "revise_copy"
            }
            assert_gate2_revision_invariants(initial_state, result_state)

    def test_gate2_raises_when_copy_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            copy_file = os.path.join(temp_dir, "cat_copy.md")  # Missing on disk

            initial_state = {
                "copy_path": copy_file,
                "gate2_decision": "revise_copy"
            }
            result_state = {
                "copy_path": copy_file,
                "gate2_decision": "revise_copy"
            }
            with self.assertRaises(AssetInvariantError) as ctx:
                assert_gate2_revision_invariants(initial_state, result_state)
            self.assertIn("does not exist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

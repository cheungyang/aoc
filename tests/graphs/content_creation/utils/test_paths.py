import os
import unittest
import tempfile
from unittest.mock import patch, MagicMock
from graphs.content_creation.utils.paths import (
    normalize_path,
    normalize_project_path,
    resolve_under_project,
    resolve_project_doc_path,
    resolve_asset_path,
    canonicalize_output_path,
    extract_aspect_ratio_from_instructions,
    validate_inter_node_paths
)
from graphs.content_creation.utils.invariants import AssetInvariantError


class TestPaths(unittest.TestCase):
    def test_normalize_path(self):
        self.assertEqual(normalize_path("foo/bar/"), "foo/bar")
        self.assertEqual(normalize_path(None), "")
        self.assertEqual(normalize_path(""), "")

    @patch('os.path.exists')
    @patch('core.util.config.Config')
    def test_normalize_project_path_with_pkm_dir(self, mock_config, mock_exists):
        mock_instance = MagicMock()
        mock_instance.pkm_dir = "/mock/pkm"
        mock_config.return_value = mock_instance

        def exists_side_effect(path):
            if path == "wiki/software/test":
                return False
            if path == "/mock/pkm/wiki/software/test":
                return True
            return False

        mock_exists.side_effect = exists_side_effect
        self.assertEqual(normalize_project_path("wiki/software/test"), "/mock/pkm/wiki/software/test")

    def test_resolve_under_project(self):
        # Already prefixed
        self.assertEqual(
            resolve_under_project("pkm/wiki/ayla", "pkm/wiki/ayla/01_Manifest.md"),
            "pkm/wiki/ayla/01_Manifest.md"
        )
        # Relative child
        self.assertEqual(
            resolve_under_project("pkm/wiki/ayla", "01_Manifest.md"),
            "pkm/wiki/ayla/01_Manifest.md"
        )
        # Deduplicate directory segment
        self.assertEqual(
            resolve_under_project("pkm/wiki/ayla/words", "words/horse"),
            "pkm/wiki/ayla/words/horse"
        )
        # Default subpath fallback
        self.assertEqual(
            resolve_under_project("pkm/wiki/ayla", "", "01_Default.md"),
            "pkm/wiki/ayla/01_Default.md"
        )

    def test_resolve_project_doc_path(self):
        self.assertEqual(
            resolve_project_doc_path(None, "pkm/wiki/ayla", "01_Project_Manifest.md"),
            "pkm/wiki/ayla/01_Project_Manifest.md"
        )
        self.assertEqual(
            resolve_project_doc_path("custom_manifest.md", "pkm/wiki/ayla", "01_Project_Manifest.md"),
            "pkm/wiki/ayla/custom_manifest.md"
        )

    def test_resolve_asset_path_versioning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # v1 resolution
            v1_path = resolve_asset_path(temp_dir, "horse", "image", next_version=False)
            self.assertEqual(v1_path, os.path.join(temp_dir, "horse_image.jpg"))

            # Create file
            with open(v1_path, "w") as f:
                f.write("v1")

            # Check next_version=True returns v2
            v2_path = resolve_asset_path(temp_dir, "horse", "image", next_version=True)
            self.assertEqual(v2_path, os.path.join(temp_dir, "horse_image_v2.jpg"))

    def test_canonicalize_output_path(self):
        self.assertEqual(
            canonicalize_output_path("pkm/wiki/ayla/words", "words/horse", "horse"),
            "pkm/wiki/ayla/words/horse"
        )
        self.assertEqual(
            canonicalize_output_path("pkm/wiki/ayla/words", "horse", "horse"),
            "pkm/wiki/ayla/words/horse"
        )
        self.assertEqual(
            canonicalize_output_path("pkm/wiki/ayla", "words/horse", "horse"),
            "pkm/wiki/ayla/words/horse"
        )

    def test_extract_aspect_ratio_from_instructions(self):
        # Format 1: aspect_ratio: 9:16
        instr1 = "# Instructions\naspect_ratio: 9:16\nResolution: 1080x1920"
        self.assertEqual(extract_aspect_ratio_from_instructions(instr1), "9:16")

        # Format 2: Format: 9:16 (Vertical Reels)
        instr2 = "## Video Format\nFormat: 9:16 (Vertical Reels / TikTok)"
        self.assertEqual(extract_aspect_ratio_from_instructions(instr2), "9:16")

        # Format 3: Landscape YouTube
        instr3 = "## Output\nOrientation: landscape (16:9)"
        self.assertEqual(extract_aspect_ratio_from_instructions(instr3), "16:9")

        # Default fallback
        self.assertEqual(extract_aspect_ratio_from_instructions(""), "16:9")
        self.assertEqual(extract_aspect_ratio_from_instructions("No aspect specified"), "16:9")

    def test_validate_inter_node_paths(self):
        valid = {
            "output_path": "/tmp/project/horse",
            "image_path": "/tmp/project/horse/horse_image.jpg",
            "video_plot_path": "/tmp/project/horse/horse_video_plot.md"
        }
        validate_inter_node_paths(valid, "node_test")

        invalid = {
            "output_path": "/tmp/project/horse",
            "image_path": "/tmp/other_dir/horse_image.jpg"
        }
        with self.assertRaises(AssetInvariantError):
            validate_inter_node_paths(invalid, "node_test")

    def test_infer_paths_from_state(self):
        from graphs.content_creation.utils.paths import infer_paths_from_state
        state = {
            "topic": "cat",
            "project_path": "",
            "output_path": "cat",
            "image_path": "pkm/wiki/software/ayla-first-words/words/cat/cat_image_v2.jpg"
        }
        p_path, out_path = infer_paths_from_state(state)
        self.assertEqual(p_path, "pkm/wiki/software/ayla-first-words")
        self.assertEqual(out_path, "pkm/wiki/software/ayla-first-words/words/cat")

        # Invariants pass cleanly with inferred paths
        state["project_path"] = p_path
        state["output_path"] = out_path
        validate_inter_node_paths(state, "produce_deliverables")


if __name__ == "__main__":
    unittest.main()

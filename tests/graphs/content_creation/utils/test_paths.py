import os
import unittest
import tempfile
from unittest.mock import patch, MagicMock
from graphs.content_creation.utils.paths import (
    normalize_path,
    normalize_project_path,
    resolve_project_doc_path,
    resolve_asset_path,
    resolve_task_asset,
    archive_asset_for_revision,
    bind_canonical_paths,
    extract_aspect_ratio_from_instructions,
    validate_inter_node_paths
)
from graphs.content_creation.utils.invariants import AssetInvariantError


class TestPaths(unittest.TestCase):
    def test_normalize_path(self):
        self.assertEqual(normalize_path("foo/bar/"), os.path.abspath("foo/bar"))
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

    def test_bind_canonical_paths_validation(self):
        with self.assertRaises(ValueError):
            bind_canonical_paths("", "output/path", "horse")
        with self.assertRaises(ValueError):
            bind_canonical_paths("project/path", "", "horse")

        paths = bind_canonical_paths("project/path", "output/path", "horse")
        self.assertEqual(paths["topic"], "horse")
        self.assertTrue(os.path.isabs(paths["project_path"]))
        self.assertTrue(os.path.isabs(paths["output_path"]))
        self.assertTrue(paths["image_path"].endswith("horse_image.jpg"))
        self.assertTrue(paths["video_plot_path"].endswith("horse_video_plot.md"))
        self.assertTrue(paths["raw_video_path"].endswith("horse_raw_video.mp4"))
        self.assertTrue(paths["remixed_video_path"].endswith("horse_video.mp4"))
        self.assertTrue(paths["copy_path"].endswith("horse_copy.md"))

    def test_resolve_project_doc_path(self):
        self.assertEqual(
            resolve_project_doc_path(None, "/tmp/project", "01_Project_Manifest.md"),
            "/tmp/project/01_Project_Manifest.md"
        )
        self.assertEqual(
            resolve_project_doc_path("custom_manifest.md", "/tmp/project", "01_Project_Manifest.md"),
            "/tmp/project/custom_manifest.md"
        )

    def test_archive_asset_for_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            img_file = os.path.join(temp_dir, "horse_image.jpg")
            with open(img_file, "w") as f:
                f.write("active content")

            # First rejection: archives horse_image.jpg -> horse_image_v1.jpg
            archived = archive_asset_for_revision(img_file)
            self.assertEqual(archived, os.path.join(temp_dir, "horse_image_v1.jpg"))
            self.assertTrue(os.path.exists(archived))
            self.assertFalse(os.path.exists(img_file))

            # New active content written
            with open(img_file, "w") as f:
                f.write("v2 active content")

            # Second rejection: archives horse_image.jpg -> horse_image_v2.jpg
            archived2 = archive_asset_for_revision(img_file)
            self.assertEqual(archived2, os.path.join(temp_dir, "horse_image_v2.jpg"))
            self.assertTrue(os.path.exists(archived2))
            self.assertTrue(os.path.exists(archived))

    def test_resolve_task_asset_archive_on_reject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            img_path, should_gen = resolve_task_asset(temp_dir, "horse", "image", needs_revision=False)
            self.assertEqual(img_path, os.path.join(temp_dir, "horse_image.jpg"))
            self.assertTrue(should_gen)

            # Create file on disk
            with open(img_path, "w") as f:
                f.write("initial image")

            # Subsequent check without revision returns should_generate=False
            img_path_reuse, should_gen_reuse = resolve_task_asset(temp_dir, "horse", "image", needs_revision=False)
            self.assertEqual(img_path_reuse, img_path)
            self.assertFalse(should_gen_reuse)

            # Revision request archives existing to _v1 and returns same canonical path
            img_path_rev, should_gen_rev = resolve_task_asset(temp_dir, "horse", "image", needs_revision=True)
            self.assertEqual(img_path_rev, img_path)
            self.assertTrue(should_gen_rev)
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "horse_image_v1.jpg")))

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


if __name__ == "__main__":
    unittest.main()

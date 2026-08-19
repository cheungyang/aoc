import os
import sys
import unittest
import tempfile
import json
from unittest.mock import AsyncMock, patch
from PIL import Image

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from graphs.content_creation.utils.paths import (
    canonicalize_output_dir,
    validate_inter_node_paths,
    _resolve_asset_path,
    normalize_project_path
)
from graphs.content_creation.utils.invariants import AssetInvariantError
from graphs.content_creation.nodes.ingestion import ingest_audio_node
from graphs.content_creation.nodes.ideation import ideate_package_node
from graphs.content_creation.nodes.production import (
    render_plate_task,
    remix_video_task,
    verify_video_task,
    produce_deliverables_node
)


class TestInterNodeFlows(unittest.IsolatedAsyncioTestCase):
    """Integration test suite verifying path mapping, aspect ratio preservation,
    and asset flows BETWEEN nodes in the content_creation graph."""

    def test_canonicalize_output_dir_deduplication(self):
        """Tests that canonicalize_output_dir eliminates words/words duplication and handles varied path formats."""
        # Case 1: project_dir has words/, output_dir is words/horse -> should NOT create words/words/horse
        res1 = canonicalize_output_dir("pkm/wiki/software/ayla-first-words/words", "words/horse", "horse")
        self.assertEqual(res1, "pkm/wiki/software/ayla-first-words/words/horse")

        # Case 2: project_dir is parent, output_dir is words/horse
        res2 = canonicalize_output_dir("pkm/wiki/software/ayla-first-words", "words/horse", "horse")
        self.assertEqual(res2, "pkm/wiki/software/ayla-first-words/words/horse")

        # Case 3: project_dir has words/, output_dir is just horse
        res3 = canonicalize_output_dir("pkm/wiki/software/ayla-first-words/words", "horse", "horse")
        self.assertEqual(res3, "pkm/wiki/software/ayla-first-words/words/horse")

        # Case 4: output_dir already absolute / full
        res4 = canonicalize_output_dir("pkm/wiki/software/ayla-first-words/words", "pkm/wiki/software/ayla-first-words/words/horse", "horse")
        self.assertEqual(res4, "pkm/wiki/software/ayla-first-words/words/horse")

    def test_validate_inter_node_paths_fails_on_mismatch(self):
        """Tests that validate_inter_node_paths catches path divergence between nodes."""
        valid_state = {
            "output_dir": "pkm/wiki/software/ayla-first-words/words/horse",
            "image_path": "pkm/wiki/software/ayla-first-words/words/horse/horse_image.jpg",
            "raw_video_path": "pkm/wiki/software/ayla-first-words/words/horse/horse_raw_video.mp4"
        }
        # Should succeed without error
        validate_inter_node_paths(valid_state, "test_node")

        # Divergent path (e.g. words/words/horse/horse_raw_video.mp4)
        invalid_state = {
            "output_dir": "pkm/wiki/software/ayla-first-words/words/horse",
            "image_path": "pkm/wiki/software/ayla-first-words/words/horse/horse_image.jpg",
            "raw_video_path": "words/words/horse/horse_raw_video.mp4"
        }
        with self.assertRaises(AssetInvariantError) as ctx:
            validate_inter_node_paths(invalid_state, "test_node")
        self.assertIn("Path mismatch between nodes at 'test_node'", str(ctx.exception))

    async def test_audio_ingestion_to_ideation_path_flow(self):
        """Tests end-to-end path consistency from Audio Ingestion into Ideation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = os.path.join(temp_dir, "ayla-first-words")
            words_dir = os.path.join(project_dir, "words")
            horse_dir = os.path.join(words_dir, "horse")
            os.makedirs(horse_dir, exist_ok=True)

            audio_file = os.path.join(horse_dir, "horse.m4a")
            with open(audio_file, "wb") as f:
                f.write(b"AUDIO_DATA")

            # Ingest Audio Node
            ingest_state = {
                "project_dir": project_dir,
                "output_dir": "words/horse",
                "topic": "horse"
            }
            ingest_res = await ingest_audio_node(ingest_state)
            self.assertEqual(ingest_res.get("source_audio_path"), audio_file)
            self.assertEqual(ingest_res.get("output_dir"), horse_dir)

            # Ideate Package Node
            ideate_state = {
                **ingest_state,
                **ingest_res,
                "style": "3D",
                "creator_instructions_path": os.path.join(project_dir, "02_Creator_Instructions.md")
            }
            with open(ideate_state["creator_instructions_path"], "w") as f:
                f.write("Instructions")

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img, \
                 patch("tools.agent_call.agent_call") as mock_agent_call:

                async def fake_img(args):
                    p = args["output_path"]
                    with open(p, "wb") as f: f.write(b"IMG_DATA")
                    return f"<payload>{p}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)

                async def fake_agent_call(args):
                    agent_id = args.get("agent_id")
                    prompt = str(args.get("prompt", ""))
                    if agent_id == "brand-editor" or "brand editor" in prompt.lower() or "qc playbook" in prompt.lower() or "audit" in prompt.lower():
                        return "<payload>VERDICT: APPROVED\nBrand checks passed.</payload>"
                    return "<payload># Horse Video Plot\nMotion prompt\nOverlay Text: 馬</payload>"
                mock_agent_call.ainvoke = AsyncMock(side_effect=fake_agent_call)

                ideate_res = await ideate_package_node(ideate_state)

                # Verify all ideation assets are under canonical horse_dir
                self.assertEqual(ideate_res["output_dir"], horse_dir)
                self.assertEqual(ideate_res["image_path"], os.path.join(horse_dir, "horse_image.jpg"))
                self.assertEqual(ideate_res["video_plot_path"], os.path.join(horse_dir, "horse_video_plot.md"))
                self.assertTrue(os.path.isfile(ideate_res["image_path"]))
                self.assertTrue(os.path.isfile(ideate_res["video_plot_path"]))

    async def test_ideation_to_production_plate_remix_verify_flow(self):
        """Tests that raw_video generated in render_plate is accurately mapped, remixed, and verified in downstream nodes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "horse")
            os.makedirs(output_dir, exist_ok=True)

            img_path = os.path.join(output_dir, "horse_image.jpg")
            with open(img_path, "wb") as f: f.write(b"IMG_BYTES")

            plot_path = os.path.join(output_dir, "horse_video_plot.md")
            with open(plot_path, "w") as f: f.write("# Horse Plot")
            plot_json_path = os.path.join(output_dir, "horse_video_plot.json")
            with open(plot_json_path, "w") as f:
                json.dump({"title": "Horse", "overlay_text": "馬", "source_audio": "horse.m4a"}, f)

            audio_path = os.path.join(output_dir, "horse.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO_BYTES")

            state = {
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "topic": "horse",
                "image_path": img_path,
                "video_plot_path": plot_path,
                "source_audio_path": audio_path
            }

            # 1. Render Plate Task
            with patch("graphs.content_creation.nodes.production.render_plate.generate_animation_veo3") as mock_veo:
                async def fake_veo(args):
                    out = args["output_path"]
                    with open(out, "wb") as f: f.write(b"RAW_PLATE_DATA")
                    return f"<payload>{out}</payload>"
                mock_veo.ainvoke = AsyncMock(side_effect=fake_veo)

                plate_res = await render_plate_task(state)
                expected_raw = os.path.join(output_dir, "horse_raw_video.mp4")
                self.assertEqual(plate_res["raw_video_path"], expected_raw)
                self.assertTrue(os.path.isfile(expected_raw))

            # 2. Remix Video Task
            state.update(plate_res)
            with patch("graphs.content_creation.nodes.production.remix_video.remix_video") as mock_remix:
                async def fake_remix(args):
                    out = args.get("output_path") or args.get("output_video_path")
                    with open(out, "wb") as f: f.write(b"REMIXED_VIDEO_DATA")
                    return f"<payload>{out}</payload><errors>None</errors>"
                mock_remix.ainvoke = AsyncMock(side_effect=fake_remix)

                remix_res = await remix_video_task(state)
                expected_remix = os.path.join(output_dir, "horse_video.mp4")
                self.assertEqual(remix_res["remixed_video_path"], expected_remix)
                self.assertTrue(remix_res["video_persisted"])
                self.assertTrue(os.path.isfile(expected_remix))

            # 3. Verify Video Task
            state.update(remix_res)
            with patch("graphs.content_creation.nodes.production.verify_video.extract_video_frames") as mock_extract, \
                 patch("graphs.content_creation.nodes.production.verify_video.audio_stream_probe") as mock_probe:

                mock_extract.ainvoke = AsyncMock(return_value="<payload>frame1.jpg\nframe2.jpg</payload><errors>None</errors>")
                mock_probe.ainvoke = AsyncMock(return_value="<payload>True</payload><errors>None</errors>")

                verify_res = await verify_video_task(state)
                self.assertTrue(verify_res["video_qc_passed"])
                self.assertEqual(verify_res["video_qc_feedback"], "All deterministic audio stream and keyframe visual criteria satisfied.")

    async def test_vertical_aspect_ratio_from_instructions(self):
        """Tests that aspect_ratio defined in creator instructions correctly propagates to video generation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "horse")
            os.makedirs(output_dir, exist_ok=True)

            instr_path = os.path.join(temp_dir, "02_Creator_Instructions.md")
            with open(instr_path, "w") as f:
                f.write("# Creator Instructions\nVideo Format: 9:16 (Vertical Reels)\naspect_ratio: 9:16\n")

            img_path = os.path.join(output_dir, "horse_image.jpg")
            with open(img_path, "wb") as f: f.write(b"IMAGE_DATA")

            plot_path = os.path.join(output_dir, "horse_video_plot.md")
            with open(plot_path, "w") as f: f.write("# Horse Plot")

            state = {
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "topic": "horse",
                "creator_instructions_path": instr_path,
                "image_path": img_path,
                "video_plot_path": plot_path
            }

            with patch("graphs.content_creation.nodes.production.render_plate.generate_animation_veo3") as mock_veo:
                async def fake_veo(args):
                    out = args["output_path"]
                    with open(out, "wb") as f: f.write(b"VERTICAL_VIDEO_DATA")
                    return f"<payload>{out}</payload>"
                mock_veo.ainvoke = AsyncMock(side_effect=fake_veo)

                await render_plate_task(state)

                mock_veo.ainvoke.assert_called_once()
                call_args = mock_veo.ainvoke.call_args[0][0]
                self.assertEqual(call_args.get("aspect_ratio"), "9:16")

    async def test_gate1_image_revision_invariant_incrementation(self):
        """Explicitly tests that Gate 1 image revision increments image_path to v2 and passes assert_gate1_revision_invariants."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "fish")
            os.makedirs(output_dir, exist_ok=True)

            v1_img = os.path.join(output_dir, "fish_image.jpg")
            with open(v1_img, "wb") as f:
                f.write(b"FISH_V1_BYTES")

            v1_plot = os.path.join(output_dir, "fish_video_plot.md")
            with open(v1_plot, "w") as f:
                f.write("# Plot v1")

            v2_plot = os.path.join(output_dir, "fish_video_plot_v2.md")
            with open(v2_plot, "w") as f:
                f.write("# Plot v2")

            state = {
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "topic": "fish",
                "image_path": v1_img,
                "video_plot_path": v1_plot,
                "gate1_decision": "revise_image",
                "latest_human_feedback": "Make the fish orange clownfish style"
            }

            from graphs.content_creation.nodes.ideation.generate_image import generate_image_task
            from graphs.content_creation.utils.invariants import assert_gate1_revision_invariants

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img:
                async def fake_img(args):
                    p = args["output_path"]
                    with open(p, "wb") as f: f.write(b"FISH_V2_BYTES")
                    return f"<payload>{p}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)

                img_res = await generate_image_task(state)

                expected_v2_img = os.path.join(output_dir, "fish_image_v2.jpg")
                self.assertEqual(img_res["image_path"], expected_v2_img)
                self.assertTrue(os.path.isfile(expected_v2_img))

                working_state = dict(state)
                working_state.update(img_res)
                working_state["video_plot_path"] = v2_plot
                # Ensure invariant assertion passes cleanly without error
                assert_gate1_revision_invariants(state, working_state)


if __name__ == "__main__":
    unittest.main()

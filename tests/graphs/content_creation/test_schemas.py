import unittest
from graphs.content_creation.schemas import VideoPlot, PlotAudit, FinalCopy

class TestSchemas(unittest.TestCase):
    def test_schemas_can_instantiate(self):
        vp = VideoPlot(title="t", source_image="i", source_audio="a", motion_prompt="m", overlay_text="t", markdown_content="c")
        self.assertEqual(vp.title, "t")

if __name__ == "__main__":
    unittest.main()

"""compress_image_bytes (core/channel/discord/images.py): attachments made small
enough to send to the model."""
import unittest

from core.channel.discord.images import compress_image_bytes


class TestCompressImageBytes(unittest.TestCase):

    def test_compress_image_bytes_valid(self):
        import io
        from PIL import Image

        # Create a large dummy image (2000 x 2000 RGBA)
        img = Image.new("RGBA", (2000, 2000), color=(255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw_bytes = buf.getvalue()

        compressed, mime = compress_image_bytes(raw_bytes, max_dim=1000, quality=75)
        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(len(compressed) > 0)
        self.assertTrue(len(compressed) < len(raw_bytes))

        # Check resized dimensions
        with Image.open(io.BytesIO(compressed)) as res_img:
            self.assertEqual(res_img.size, (1000, 1000))

    def test_compress_image_bytes_empty_and_corrupt(self):
        self.assertEqual(compress_image_bytes(b"")[0], b"")
        self.assertEqual(compress_image_bytes(None)[0], None)
        
        # Corrupt data returns original bytes
        corrupt = b"not_a_real_image"
        out_bytes, mime = compress_image_bytes(corrupt)
        self.assertEqual(out_bytes, corrupt)


if __name__ == "__main__":
    unittest.main()

"""Images attached to a Discord message, made small enough to send to the model."""


def compress_image_bytes(image_bytes: bytes, max_dim: int = 1560, quality: int = 80) -> tuple[bytes, str]:
    """Resizes and compresses image bytes to optimize storage and transmission."""
    if not image_bytes:
        return image_bytes, "image/jpeg"
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            width, height = img.size
            if max(width, height) > max_dim:
                ratio = max_dim / float(max(width, height))
                new_size = (int(width * ratio), int(height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"compress_image_bytes: Warning - failed to compress image: {e}")
        return image_bytes, "image/jpeg"

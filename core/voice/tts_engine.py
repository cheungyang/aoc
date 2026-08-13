import re
import os
import io
import asyncio
import tempfile

class TextSanitizer:
    """Cleans agent Markdown/XML output into natural spoken text."""
    
    @staticmethod
    def sanitize(text: str) -> str:
        if not text:
            return ""
            
        cleaned = text
        
        # 1. Remove XML/HTML tags (e.g. <poll>...</poll>, <job>...</job>, etc.)
        cleaned = re.sub(r"<[^>]+>.*?</[^>]+>", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        
        # 2. Replace multi-line code blocks with a brief spoken note
        cleaned = re.sub(r"```[a-zA-Z0-9_-]*\n(.*?)```", " (I have provided the code in the chat.) ", cleaned, flags=re.DOTALL)
        
        # 3. Replace inline code `code` with code
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        
        # 4. Replace markdown links [label](url) with label
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        
        # 5. Remove raw URLs
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        
        # 6. Remove markdown header symbols (#), bold/italic (*, _), blockquotes (>), strikethrough (~~)
        cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"[*_~]{1,3}", "", cleaned)
        cleaned = re.sub(r"^>\s+", "", cleaned, flags=re.MULTILINE)
        
        # 7. Convert markdown bullet lists to comma pauses
        cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*\d+\.\s+", "", cleaned, flags=re.MULTILINE)
        
        # 8. Remove all emojis (Unicode emojis, symbols, dingbats, and Discord custom emojis)
        emoji_pattern = (
            r"<a?:[a-zA-Z0-9_]+:[0-9]+>"  # Discord custom emoji
            r"|[\U00010000-\U0010ffff]"   # SMP (emojis, pictographs)
            r"|[\u200d\ufe0e\ufe0f]"      # Zero-width joiner & variation selectors
            r"|[\u2600-\u27bf]"           # Misc symbols & Dingbats (✅, ⚡, ✨, ❌, etc.)
            r"|[\u2300-\u23ff]"           # Misc technical
            r"|[\u2b50-\u2b55]"           # Stars and geometric shapes
            r"|[\u203c\u2049\u2139\u2194-\u21aa\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe\u2934\u2935\u3030\u303d\u3297\u3299]"
        )
        cleaned = re.sub(emoji_pattern, "", cleaned)
        
        # 9. Collapse whitespace and line breaks
        cleaned = re.sub(r"\n+", ". ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        
        return cleaned

class TTSEngine:
    """Text-to-Speech synthesizer supporting Edge-TTS and local neural engines."""
    
    def __init__(self, default_voice: str = "en-US-JennyNeural", default_speed: float = 1.0):
        self.default_voice = default_voice
        self.default_speed = default_speed

    async def synthesize_to_file(self, text: str, voice: str = None, speed: float = None) -> str:
        """
        Synthesizes text into a temporary MP3/WAV audio file.
        Returns the absolute file path.
        """
        sanitized_text = TextSanitizer.sanitize(text)
        if not sanitized_text:
            return ""
            
        voice = voice or self.default_voice
        speed = speed or self.default_speed
        
        # Format speed rate parameter (e.g. "+0%" or "+10%")
        speed_percent = int((speed - 1.0) * 100)
        speed_str = f"{'+' if speed_percent >= 0 else ''}{speed_percent}%"
        
        # Create temp file
        temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        temp_path = temp_file.name
        temp_file.close()
        
        try:
            import edge_tts
            communicate = edge_tts.Communicate(sanitized_text, voice=voice, rate=speed_str)
            await communicate.save(temp_path)
            return temp_path
        except Exception as e:
            print(f"[TTSEngine] Error synthesizing speech with edge-tts: {e}")
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            return ""

import re
from typing import List
from core.voice.tts_engine import TextSanitizer

class SentenceChunker:
    """
    Buffers streaming token deltas and yields clean, complete sentence chunks
    as soon as punctuation boundaries are encountered.
    """

    # Common abbreviations to avoid false positive sentence splitting
    ABBREVIATIONS = {
        "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.",
        "e.g.", "i.e.", "u.s.", "u.k.", "a.m.", "p.m.", "jan.", "feb.",
        "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "oct.", "nov.", "dec."
    }

    def __init__(self, min_chars: int = 20, max_chars: int = 140):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.buffer = ""
        self._sanitizer = TextSanitizer()

    def add_token(self, token: str) -> List[str]:
        """
        Appends a token delta and returns any ready sentence/clause chunks.
        """
        if not token:
            return []

        self.buffer += token
        return self._extract_ready_chunks()

    def flush(self) -> List[str]:
        """
        Flushes any remaining text in the buffer as a final chunk.
        """
        remaining = self.buffer.strip()
        self.buffer = ""
        if not remaining:
            return []

        cleaned = self._clean_for_speech(remaining)
        return [cleaned] if cleaned else []

    def _extract_ready_chunks(self) -> List[str]:
        chunks = []
        while True:
            split_pos = self._find_split_position(self.buffer)
            if split_pos is None:
                # If buffer exceeds max_chars, force split at the nearest clause/comma or space
                if len(self.buffer) >= self.max_chars:
                    split_pos = self._find_fallback_split(self.buffer)
                if split_pos is None:
                    break

            candidate = self.buffer[:split_pos].strip()
            self.buffer = self.buffer[split_pos:].lstrip()

            cleaned = self._clean_for_speech(candidate)
            if cleaned:
                chunks.append(cleaned)

        return chunks

    def _find_split_position(self, text: str) -> int | None:
        """Finds the character index immediately following a valid sentence end."""
        pattern = re.compile(r'([.!?\n]+)(\s+|$)')
        for match in pattern.finditer(text):
            punct_end = match.end(1)
            prefix = text[:punct_end].strip()

            # Check if this prefix ends with a known abbreviation or decimal number
            words = prefix.split()
            if words:
                last_word = words[-1].lower()
                if last_word in self.ABBREVIATIONS:
                    continue
                # Decimal number check (e.g. 3.14)
                if re.search(r'\d+\.\d*$', words[-1]):
                    continue

            # Ensure minimum character count for natural voice cadence
            if len(prefix) >= self.min_chars:
                return match.end()

        return None

    def _find_fallback_split(self, text: str) -> int | None:
        """Fallback split for overly long sentences at commas/clauses or whitespace."""
        clause_pattern = re.compile(r'([,;:—]+|\s+and\s+|\s+but\s+)(\s+)')
        for match in clause_pattern.finditer(text):
            if match.end() >= self.min_chars:
                return match.end()

        # Last resort: split at last space before max_chars
        space_idx = text.rfind(" ", self.min_chars, self.max_chars)
        if space_idx != -1:
            return space_idx + 1

        return None

    def _clean_for_speech(self, text: str) -> str:
        """Removes XML blocks, markdown formatting, and emojis."""
        if not text:
            return ""
        # 1. Remove XML tags and contents for tags like <poll>, <images>, etc.
        text = re.sub(r'<poll>.*?</poll>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<images>.*?</images>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<videos>.*?</videos>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<system_memory_log>.*?</system_memory_log>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)

        # 2. Run through standard voice TextSanitizer
        return self._sanitizer.sanitize(text).strip()

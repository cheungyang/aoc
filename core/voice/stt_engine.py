import io
import asyncio
from concurrent.futures import ThreadPoolExecutor
from faster_whisper import WhisperModel

class STTEngine:
    """Local Speech-to-Text engine using faster-whisper with thread pool execution."""
    _models: dict[str, WhisperModel] = {}
    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="STTWorker")

    def __init__(self, model_size: str = "base.en", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._ensure_model_loaded()

    def _ensure_model_loaded(self):
        key = f"{self.model_size}:{self.device}:{self.compute_type}"
        if key not in self._models:
            print(f"[STTEngine] Loading faster-whisper model '{self.model_size}' (device={self.device}, compute_type={self.compute_type})...")
            self._models[key] = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=4
            )
            print(f"[STTEngine] Model '{self.model_size}' ready.")
        self.model = self._models[key]

    def _sync_transcribe(self, wav_bytes: bytes) -> str:
        """Synchronously transcribes WAV bytes into text."""
        audio_stream = io.BytesIO(wav_bytes)
        print(f"[STTEngine] 🎙️ Feeding {len(wav_bytes)} bytes (~{len(wav_bytes)/32000:.2f}s) to Faster-Whisper...")
        segments, info = self.model.transcribe(
            audio_stream,
            beam_size=5,
            language="en",
            temperature=0.0,
            repetition_penalty=1.2,
            no_repeat_ngram_size=3,
            compression_ratio_threshold=2.4,
            vad_filter=False, # We already ran Silero VAD on input
            condition_on_previous_text=False
        )
        
        raw_segments = list(segments)
        transcribed_text = " ".join([segment.text for segment in raw_segments]).strip()
        print(f"[STTEngine] 📝 Faster-Whisper raw output ({len(raw_segments)} segments): '{transcribed_text}'")
        
        # Filter hallucinations/artifacts (e.g. YouTube endings hallucinated on quiet audio)
        cleaned = transcribed_text.lower().strip()
        hallucinations = [
            "[blank_audio]", "(blank audio)", "thank you", "thank you.", 
            "thanks for watching", "thanks for watching!", "thank you for watching", 
            "thank you for watching!", "please subscribe", "subscribe", 
            "subtitles by", "you", "."
        ]
        if cleaned in hallucinations or len(cleaned) == 0:
            return ""
            
        return transcribed_text

    async def transcribe(self, wav_bytes: bytes) -> str:
        """Asynchronously transcribes audio bytes without blocking the event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._sync_transcribe, wav_bytes)

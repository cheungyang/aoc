import os
import sys
import io
import wave
import asyncio
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.voice.stt_engine import STTEngine
from core.voice.tts_engine import TTSEngine
from core.voice.blurp_generator import BlurpGenerator
from core.loaders.agents_loader import AgentsLoader

async def main():
    print("========================================")
    print("🎙️ Local Voice Pipeline Dryrun Utility")
    print("========================================")

    # 1. Initialize Engines
    print("\n[1/4] Initializing STT & TTS Engines...")
    stt = STTEngine(model_size="base.en")
    tts = TTSEngine(default_voice="en-US-JennyNeural")
    print("✓ Engines ready.")

    # 2. Prepare or generate test audio
    test_wav_path = "assets/sounds/test_speech.wav"
    os.makedirs("assets/sounds", exist_ok=True)

    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        test_wav_path = sys.argv[1]
        print(f"\n[2/4] Using provided audio file: {test_wav_path}")
        with open(test_wav_path, "rb") as f:
            wav_bytes = f.read()
    else:
        print("\n[2/4] Generating synthetic test speech: 'testing testing 1 2 3'...")
        mp3_file = await tts.synthesize_to_file("testing testing 1 2 3")
        import imageio_ffmpeg
        import subprocess
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", mp3_file, "-f", "wav", "-ar", "16000", "-ac", "1", test_wav_path],
            capture_output=True,
            check=True
        )
        with open(test_wav_path, "rb") as f:
            wav_bytes = f.read()
        print(f"✓ Created test audio file: {test_wav_path} ({len(wav_bytes)} bytes)")

    # 3. Transcribe audio with Faster-Whisper
    print("\n[3/4] Transcribing audio with Faster-Whisper...")
    transcript = await stt.transcribe(wav_bytes)
    print(f"📝 Transcribed Text: '{transcript}'")

    if not transcript:
        print("❌ Transcription was empty or filtered.")
        return

    # 4. Execute LangGraph Agent (Concierge)
    print(f"\n[4/4] Executing LangGraph agent 'main' with prompt: '{transcript}'...")
    loader = AgentsLoader()
    agent = loader.get_agent("main")
    if agent:
        response = await agent.execute(content=transcript, source="voice_dryrun")
        print(f"\n💬 Agent Response:\n{response}")
        
        # Synthesize spoken response
        print("\n🔊 Synthesizing agent spoken response with Edge-TTS...")
        reply_audio = await tts.synthesize_to_file(response)
        print(f"✓ Agent speech audio generated at: {reply_audio}")
    else:
        print("Agent 'main' not found.")

    print("\n========================================")
    print("✅ Dryrun completed successfully!")
    print("========================================")

if __name__ == "__main__":
    asyncio.run(main())
